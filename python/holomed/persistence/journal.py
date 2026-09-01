# -*- coding: utf-8 -*-
"""Append-Only JSONL Journal with Hash Chaining and Crash Recovery (M09)."""

from __future__ import annotations

import json
import os
from pathlib import Path
from types import MappingProxyType
from typing import Any, Mapping, Optional, Sequence
import uuid

from holomed.persistence.exceptions import (
    PersistenceCapacityError,
    PersistenceCorruptionError,
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

    def __init__(self, storage_root: Path, session_id: str, epoch_id: int) -> None:
        self._storage_root = Path(storage_root)
        self._session_id = session_id
        self._epoch_id = epoch_id
        self._journal_path = validate_session_path(self._storage_root, session_id)

        self._in_transaction: bool = False
        self._entry_count: int = 0
        self._last_entry_hash: str = GENESIS_PREVIOUS_HASH
        self._last_sequence: int = -1

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

    def initialize_storage(self) -> None:
        """Create storage root directory if missing."""
        self._storage_root.mkdir(parents=True, exist_ok=True)

    def append_entry(
        self,
        entry_type: JournalEntryType,
        sequence_number: int,
        timestamp_utc: str,
        payload: Mapping[str, Any],
        entry_id: Optional[str] = None,
    ) -> JournalEntry:
        """Append a cryptographically chained entry to the session journal."""
        if self._in_transaction:
            raise PersistenceLifecycleError("Reentrant call to append_entry rejected by transaction guard")

        self._in_transaction = True
        try:
            # 1. Monotonicity & bounds check
            if sequence_number <= self._last_sequence and entry_type not in META_ENTRY_TYPES:
                raise PersistenceSequenceError(
                    f"Non-monotonic sequence number {sequence_number} <= {self._last_sequence} for session {self._session_id}"
                )

            if self._entry_count >= MAX_JOURNAL_ENTRIES_PER_SESSION:
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
                "sequence_number": sequence_number,
                "payload": payload,
                "previous_entry_hash": self._last_entry_hash,
            }

            sha_hash = compute_entry_hash(entry_dict)
            entry_dict["sha256_hash"] = sha_hash

            # Canonical byte formatting
            raw_bytes = serialize_canonical_bytes(entry_dict) + b"\n"

            # Append to file
            with open(self._journal_path, "ab") as f:
                f.write(raw_bytes)
                f.flush()

            # Advance in-memory state
            self._entry_count += 1
            self._last_entry_hash = sha_hash
            if sequence_number > self._last_sequence:
                self._last_sequence = sequence_number

            return JournalEntry(
                entry_id=eid,
                entry_type=entry_type,
                schema_version=PERSISTENCE_SCHEMA_VERSION,
                timestamp_utc=timestamp_utc,
                epoch_id=self._epoch_id,
                session_id=self._session_id,
                sequence_number=sequence_number,
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
    ) -> tuple[list[JournalEntry], int]:
        """Read all valid journal records, safely recovering crash-truncated tails.

        Returns (valid_entries, truncated_bytes_count).
        """
        if not journal_path.exists() or journal_path.stat().st_size == 0:
            return [], 0

        entries: list[JournalEntry] = []
        truncated_bytes: int = 0
        expected_prev_hash = GENESIS_PREVIOUS_HASH
        last_seq = -1

        with open(journal_path, "rb") as f:
            raw_content = f.read()

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
            with open(journal_path, "wb") as f:
                f.write(raw_content[:valid_size])
                f.flush()

        return entries, truncated_bytes
