# -*- coding: utf-8 -*-
"""Append-Only JSONL Journal with Hash Chaining and Crash Recovery (M09)."""

from __future__ import annotations

import json
import os
from pathlib import Path
from types import MappingProxyType
from typing import Any, Callable, Mapping, Optional, Sequence
import uuid

from holomed.persistence.exceptions import (
    PersistenceCapacityError,
    PersistenceCorruptionError,
    PersistenceEpochMismatchError,
    PersistenceLifecycleError,
    PersistenceSecurityError,
    PersistenceSequenceError,
    PersistenceValidationError,
)
from holomed.persistence.models import (
    MAX_JOURNAL_ENTRIES_PER_SESSION,
    MAX_JOURNAL_FILE_BYTES,
    MAX_JOURNAL_RECORD_BYTES,
    PERSISTENCE_SCHEMA_VERSION,
    SESSION_ID_REGEX,
    JournalEntry,
    JournalEntryType,
)
from holomed.persistence.serialization import (
    compute_entry_hash,
    serialize_canonical_bytes,
)

GENESIS_PREVIOUS_HASH: str = "0" * 64

META_ENTRY_TYPES: tuple[JournalEntryType, ...] = (
    JournalEntryType.SESSION_STARTED,
    JournalEntryType.SESSION_CLOSED,
    JournalEntryType.EPOCH_MIGRATED,
    JournalEntryType.AUDIT_SNAPSHOT,
)

META_ENTRY_TYPE_VALUES: tuple[str, ...] = tuple(e.value for e in META_ENTRY_TYPES)


def validate_session_path(storage_root: Path, session_id: str) -> Path:
    """Resolve and validate journal path preventing directory traversal attacks."""
    if not isinstance(session_id, str) or not SESSION_ID_REGEX.match(session_id):
        raise PersistenceSecurityError(f"Invalid characters or format in session_id: {session_id!r}")

    root = storage_root.resolve()
    target = (root / f"{session_id}.jsonl").resolve()

    # Enforce path containment
    if not str(target).startswith(str(root)):
        raise PersistenceSecurityError(f"Path traversal detected: {target} escapes root {root}")

    return target


class JournalWriter:
    """Manages append-only JSONL journal writing with cryptographic hash chaining."""

    def __init__(
        self,
        storage_root: Path,
        session_id: str,
        epoch_id: int,
        authoritative_epoch_provider: Optional[Callable[[], int]] = None,
    ) -> None:
        self._storage_root = Path(storage_root)
        self._session_id = session_id
        self._epoch_id = epoch_id
        self._get_authoritative_epoch = authoritative_epoch_provider
        self._journal_path = validate_session_path(self._storage_root, session_id)

        self._in_transaction: bool = False
        self._entry_count: int = 0
        self._last_entry_hash: str = GENESIS_PREVIOUS_HASH
        self._last_sequence: int = -1
        self._is_closed: bool = False

    @property
    def journal_path(self) -> Path:
        return self._journal_path

    @property
    def entry_count(self) -> int:
        return self._entry_count

    @property
    def last_entry_hash(self) -> str:
        return self._last_entry_hash

    @property
    def last_sequence(self) -> int:
        return self._last_sequence

    @property
    def is_closed(self) -> bool:
        """Indicate whether the journal writer lifecycle state is closed."""
        return self._is_closed

    def initialize_storage(self) -> None:
        """Create storage root directory if missing."""
        self._storage_root.mkdir(parents=True, exist_ok=True)

    def flush(self) -> None:
        """Flush pending writes to storage (no-op as writes are per-append synchronous)."""
        pass

    def close(self) -> None:
        """Close writer lifecycle state, rejecting subsequent append_entry calls."""
        self._is_closed = True

    def append_entry(
        self,
        entry_type: JournalEntryType,
        sequence_number: int | None = None,
        timestamp_utc: str | None = None,
        payload: Mapping[str, Any] | None = None,
        entry_id: Optional[str] = None,
    ) -> JournalEntry:
        """Append a cryptographically chained entry to the session journal."""
        if timestamp_utc is None or payload is None:
            raise TypeError("timestamp_utc and payload are required")

        if self._is_closed:
            raise PersistenceLifecycleError(
                f"JournalWriter for session {self._session_id} is closed"
            )

        if self._in_transaction:
            raise PersistenceLifecycleError("Reentrant call to append_entry rejected by transaction guard")

        self._in_transaction = True
        try:
            # 1. Epoch Authority Enforcement
            if self._get_authoritative_epoch is not None:
                current_epoch = self._get_authoritative_epoch()
                if self._epoch_id != current_epoch:
                    raise PersistenceEpochMismatchError(
                        f"Stale JournalWriter (epoch {self._epoch_id}) rejected; authoritative epoch is {current_epoch}"
                    )

            # Append to file with strict OS-level durability and serialization
            with open(self._journal_path, "a+b") as f:
                fd = f.fileno()

                # A. Serialize multi-process writers
                try:
                    if os.name == "nt":
                        import msvcrt
                        pos = f.tell()
                        f.seek(0)
                        msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
                        f.seek(pos)
                    else:
                        import fcntl
                        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                except OSError as e:
                    raise PersistenceLifecycleError(
                        f"Concurrent write rejected: failed to acquire exclusive lock on journal {self._session_id}"
                    ) from e

                try:
                    # B. Recover crash-truncated tail inside lock
                    recovered_entries, truncated = JournalReader.read_and_recover_journal(self._journal_path, file_obj=f)
                    
                    actual_last_seq = -1
                    actual_last_hash = GENESIS_PREVIOUS_HASH
                    actual_count = len(recovered_entries)
                    
                    if actual_count > 0:
                        actual_last_hash = recovered_entries[-1].sha256_hash
                        actual_last_seq = recovered_entries[-1].sequence_number

                    # Evaluate provided sequence number
                    if sequence_number is None:
                        # Authoritatively derive
                        if entry_type in META_ENTRY_TYPES:
                            resolved_sequence = actual_last_seq
                        else:
                            resolved_sequence = actual_last_seq + 1
                    else:
                        resolved_sequence = sequence_number

                    # 2. Monotonicity & bounds check
                    if resolved_sequence <= actual_last_seq and entry_type not in META_ENTRY_TYPES:
                        raise PersistenceSequenceError(
                            f"Non-monotonic sequence number {resolved_sequence} <= {actual_last_seq} for session {self._session_id}"
                        )

                    if actual_count >= MAX_JOURNAL_ENTRIES_PER_SESSION:
                        raise PersistenceCapacityError(
                            f"Journal entry count exceeded {MAX_JOURNAL_ENTRIES_PER_SESSION} for session {self._session_id}"
                        )

                    current_size = self._journal_path.stat().st_size if self._journal_path.exists() else 0
                    if current_size >= MAX_JOURNAL_FILE_BYTES:
                        raise PersistenceCapacityError(
                            f"Journal file size {current_size} bytes exceeds limit of {MAX_JOURNAL_FILE_BYTES} bytes"
                        )

                    eid = entry_id or str(uuid.uuid4())
                    entry_dict = {
                        "entry_id": eid,
                        "entry_type": entry_type.value,
                        "schema_version": PERSISTENCE_SCHEMA_VERSION,
                        "timestamp_utc": timestamp_utc,
                        "epoch_id": self._epoch_id,
                        "session_id": self._session_id,
                        "sequence_number": resolved_sequence,
                        "payload": payload,
                        "previous_entry_hash": actual_last_hash,
                    }

                    sha_hash = compute_entry_hash(entry_dict)
                    entry_dict["sha256_hash"] = sha_hash

                    # Canonical byte formatting
                    raw_bytes = serialize_canonical_bytes(entry_dict) + b"\n"

                    # C. Write payload
                    # Note: We append, so we must seek to end since read_and_recover might have rewritten the file.
                    f.seek(0, 2)
                    f.write(raw_bytes)
                    
                    # D. Durability contract: write -> flush -> fsync
                    f.flush()
                    os.fsync(fd)
                finally:
                    # E. Unlock
                    try:
                        if os.name == "nt":
                            pos = f.tell()
                            f.seek(0)
                            msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
                            f.seek(pos)
                        else:
                            import fcntl
                            fcntl.flock(fd, fcntl.LOCK_UN)
                    except OSError:
                        pass

            # Advance in-memory state
            self._entry_count = actual_count + 1
            self._last_entry_hash = sha_hash
            if resolved_sequence > actual_last_seq:
                self._last_sequence = resolved_sequence
            else:
                self._last_sequence = actual_last_seq

            return JournalEntry(
                entry_id=eid,
                entry_type=entry_type,
                schema_version=PERSISTENCE_SCHEMA_VERSION,
                timestamp_utc=timestamp_utc,
                epoch_id=self._epoch_id,
                session_id=self._session_id,
                sequence_number=resolved_sequence,
                payload=MappingProxyType(dict(payload)),
                sha256_hash=sha_hash,
                previous_entry_hash=entry_dict["previous_entry_hash"],
            )
        finally:
            self._in_transaction = False


class JournalReader:
    """Reads and validates append-only journal files with crash recovery."""

    @staticmethod
    def read_and_recover_journal(
        journal_path: Path,
        file_obj: Any = None,
    ) -> tuple[list[JournalEntry], int]:
        """Read all valid journal records, safely recovering crash-truncated tails.

        Returns (valid_entries, truncated_bytes_count).
        """
        if file_obj is None and (not journal_path.exists() or journal_path.stat().st_size == 0):
            return [], 0

        entries: list[JournalEntry] = []
        truncated_bytes: int = 0
        expected_prev_hash = GENESIS_PREVIOUS_HASH
        last_seq = -1

        if file_obj is not None:
            file_obj.seek(0)
            raw_content = file_obj.read()
        else:
            with open(journal_path, "rb") as f:
                raw_content = f.read()

        if not raw_content:
            return [], 0

        lines = raw_content.split(b"\n")
        has_trailing_newline = raw_content.endswith(b"\n")
        if has_trailing_newline:
            lines = lines[:-1]

        for i, line in enumerate(lines):
            is_last_line = i == len(lines) - 1

            if not line.strip():
                continue

            try:
                record = json.loads(line.decode("utf-8"))
            except Exception as e:
                if is_last_line and (not has_trailing_newline or isinstance(e, json.JSONDecodeError)):
                    truncated_bytes = len(line) + (1 if has_trailing_newline else 0)
                    break
                raise PersistenceCorruptionError(
                    f"Malformed JSON at record index {i} in journal {journal_path.name}: {e}"
                ) from e

            # Verify integrity
            expected_hash = compute_entry_hash(record)
            actual_hash = record.get("sha256_hash")

            if actual_hash != expected_hash:
                if is_last_line and not has_trailing_newline:
                    truncated_bytes = len(line)
                    break
                raise PersistenceCorruptionError(
                    f"SHA-256 hash mismatch at record index {i}: expected {expected_hash}, got {actual_hash}"
                )

            # Verify hash chaining
            actual_prev = record.get("previous_entry_hash")
            if actual_prev != expected_prev_hash:
                raise PersistenceCorruptionError(
                    f"Hash chain broken at record index {i}: expected prev {expected_prev_hash}, got {actual_prev}"
                )

            # Verify sequence monotonicity
            seq = record.get("sequence_number", -1)
            etype = record.get("entry_type")
            if seq <= last_seq and etype not in META_ENTRY_TYPE_VALUES:
                raise PersistenceCorruptionError(
                    f"Sequence monotonicity violation at record index {i}: seq {seq} <= last {last_seq}"
                )

            entry = JournalEntry(
                entry_id=record["entry_id"],
                entry_type=JournalEntryType(record["entry_type"]),
                schema_version=record["schema_version"],
                timestamp_utc=record["timestamp_utc"],
                epoch_id=record["epoch_id"],
                session_id=record["session_id"],
                sequence_number=seq,
                payload=MappingProxyType(record.get("payload", {})),
                sha256_hash=actual_hash,
                previous_entry_hash=actual_prev,
            )
            entries.append(entry)
            expected_prev_hash = actual_hash
            if seq > last_seq:
                last_seq = seq

        if truncated_bytes > 0:
            valid_size = len(raw_content) - truncated_bytes
            if file_obj is not None:
                file_obj.seek(valid_size)
                file_obj.truncate()
                file_obj.flush()
                try:
                    os.fsync(file_obj.fileno())
                except OSError:
                    pass
            else:
                with open(journal_path, "wb") as f:
                    f.write(raw_content[:valid_size])
                    f.flush()
                    try:
                        os.fsync(f.fileno())
                    except OSError:
                        pass

        return entries, truncated_bytes
