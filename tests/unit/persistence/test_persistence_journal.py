# -*- coding: utf-8 -*-
"""Unit Tests for JournalWriter, JournalReader, Hash Chaining, and Crash Recovery."""

from __future__ import annotations

import json
from pathlib import Path
import pytest

from holomed.persistence.exceptions import (
    PersistenceCapacityError,
    PersistenceCorruptionError,
    PersistenceSecurityError,
    PersistenceSequenceError,
)
from holomed.persistence.journal import (
    GENESIS_PREVIOUS_HASH,
    JournalReader,
    JournalWriter,
    validate_session_path,
)
from holomed.persistence.models import JournalEntryType


def test_sha256_hash_chain_validation(temp_storage_root: Path) -> None:
    """Verify genesis entry uses 64 zeros and successive entries chain hashes."""
    writer = JournalWriter(temp_storage_root, "sess_chain_01", epoch_id=1)
    writer.initialize_storage()

    e1 = writer.append_entry(JournalEntryType.SESSION_STARTED, -1, "2026-09-01T20:00:00Z", {"action": "START"})
    assert e1.previous_entry_hash == GENESIS_PREVIOUS_HASH

    e2 = writer.append_entry(JournalEntryType.CYCLE_COMPLETED, 0, "2026-09-01T20:00:01Z", {"status": "COMPLETED"})
    assert e2.previous_entry_hash == e1.sha256_hash

    e3 = writer.append_entry(JournalEntryType.CYCLE_COMPLETED, 1, "2026-09-01T20:00:02Z", {"status": "COMPLETED"})
    assert e3.previous_entry_hash == e2.sha256_hash

    # Read back and verify
    entries, truncated = JournalReader.read_and_recover_journal(writer.journal_path)
    assert len(entries) == 3
    assert truncated == 0


def test_hash_tamper_detection(temp_storage_root: Path) -> None:
    """Verify altering a single payload byte triggers PersistenceCorruptionError."""
    writer = JournalWriter(temp_storage_root, "sess_tamper_01", epoch_id=1)
    writer.initialize_storage()

    writer.append_entry(JournalEntryType.SESSION_STARTED, -1, "2026-09-01T20:00:00Z", {"action": "START"})
    writer.append_entry(JournalEntryType.CYCLE_COMPLETED, 0, "2026-09-01T20:00:01Z", {"status": "COMPLETED"})

    # Tamper with file
    with open(writer.journal_path, "r", encoding="utf-8") as f:
        lines = f.readlines()

    # Modify status in second record without updating hash
    tampered_dict = json.loads(lines[1])
    tampered_dict["payload"]["status"] = "TAMPERED"
    lines[1] = json.dumps(tampered_dict) + "\n"

    with open(writer.journal_path, "w", encoding="utf-8") as f:
        f.writelines(lines)

    with pytest.raises(PersistenceCorruptionError):
        JournalReader.read_and_recover_journal(writer.journal_path)


def test_crash_recovery_partial_tail_truncation(temp_storage_root: Path) -> None:
    """Verify incomplete write at EOF is cleanly truncated back to last valid newline."""
    writer = JournalWriter(temp_storage_root, "sess_crash_01", epoch_id=1)
    writer.initialize_storage()

    writer.append_entry(JournalEntryType.SESSION_STARTED, -1, "2026-09-01T20:00:00Z", {"action": "START"})
    writer.append_entry(JournalEntryType.CYCLE_COMPLETED, 0, "2026-09-01T20:00:01Z", {"status": "COMPLETED"})

    # Append an incomplete partial line (simulating power cut mid-write)
    with open(writer.journal_path, "ab") as f:
        f.write(b'{"entry_id": "cut_off", "payload": {"sta')

    # Read and recover
    entries, truncated = JournalReader.read_and_recover_journal(writer.journal_path)
    assert len(entries) == 2
    assert truncated > 0

    # Ensure file on disk was cleanly truncated
    entries_after, truncated_after = JournalReader.read_and_recover_journal(writer.journal_path)
    assert len(entries_after) == 2
    assert truncated_after == 0


def test_middle_record_corruption_fails_closed(temp_storage_root: Path) -> None:
    """Verify corruption in the middle of a file fails closed and does NOT truncate."""
    writer = JournalWriter(temp_storage_root, "sess_mid_corrupt", epoch_id=1)
    writer.initialize_storage()

    writer.append_entry(JournalEntryType.SESSION_STARTED, -1, "2026-09-01T20:00:00Z", {"action": "START"})
    writer.append_entry(JournalEntryType.CYCLE_COMPLETED, 0, "2026-09-01T20:00:01Z", {"status": "COMPLETED"})
    writer.append_entry(JournalEntryType.CYCLE_COMPLETED, 1, "2026-09-01T20:00:02Z", {"status": "COMPLETED"})

    # Corrupt middle line
    with open(writer.journal_path, "rb") as f:
        content = f.read()

    lines = content.split(b"\n")
    lines[1] = b"CORRUPTED_JSON_LINE"
    with open(writer.journal_path, "wb") as f:
        f.write(b"\n".join(lines))

    with pytest.raises(PersistenceCorruptionError):
        JournalReader.read_and_recover_journal(writer.journal_path)


def test_broken_hash_chain_fails_closed(temp_storage_root: Path) -> None:
    """Verify broken previous_entry_hash chain raises PersistenceCorruptionError."""
    writer = JournalWriter(temp_storage_root, "sess_broken_chain", epoch_id=1)
    writer.initialize_storage()

    writer.append_entry(JournalEntryType.SESSION_STARTED, -1, "2026-09-01T20:00:00Z", {"action": "START"})
    
    # Manually append an entry with a broken previous_entry_hash
    from holomed.persistence.serialization import compute_entry_hash, serialize_canonical_bytes
    from holomed.persistence.models import PERSISTENCE_SCHEMA_VERSION
    
    e2_dict = {
        "entry_id": "broken_chain_id",
        "entry_type": JournalEntryType.CYCLE_COMPLETED.value,
        "schema_version": PERSISTENCE_SCHEMA_VERSION,
        "timestamp_utc": "2026-09-01T20:00:01Z",
        "epoch_id": 1,
        "session_id": "sess_broken_chain",
        "sequence_number": 0,
        "payload": {"status": "COMPLETED"},
        "previous_entry_hash": "f" * 64,
    }
    e2_dict["sha256_hash"] = compute_entry_hash(e2_dict)
    
    with open(writer.journal_path, "ab") as f:
        f.write(serialize_canonical_bytes(e2_dict) + b"\n")

    with pytest.raises(PersistenceCorruptionError):
        JournalReader.read_and_recover_journal(writer.journal_path)


def test_path_traversal_rejection(temp_storage_root: Path) -> None:
    """Verify attempts to pass path traversal characters are blocked."""
    with pytest.raises(PersistenceSecurityError):
        validate_session_path(temp_storage_root, "../../escaped")

    with pytest.raises(PersistenceSecurityError):
        validate_session_path(temp_storage_root, "sess/subfolder")


def test_fsync_failure_propagation(temp_storage_root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Verify that fsync OSError propagates and prevents successful write."""
    from holomed.persistence.journal import JournalWriter, JournalEntryType
    
    writer = JournalWriter(temp_storage_root, "sess_fsync_fail", 1)
    
    # Mock os.fsync to raise OSError
    def mock_fsync(fd):
        raise OSError("Simulated fsync failure")
        
    monkeypatch.setattr("os.fsync", mock_fsync)
    
    with pytest.raises(OSError, match="Simulated fsync failure"):
        writer.append_entry(
            entry_type=JournalEntryType.OPERATION_ADMITTED,
            sequence_number=None,
            timestamp_utc="2026-09-01T20:00:00Z",
            payload={"status": "ADMITTED"},
            entry_id="op_fsync_fail"
        )

import time
import pytest
from unittest.mock import patch, MagicMock

def test_journal_transient_permission_error(temp_storage_root: Path) -> None:
    """Verify bounded retries recover a transient PermissionError on read."""
    writer = JournalWriter(temp_storage_root, "sess_transient", epoch_id=1)
    writer.initialize_storage()
    writer.append_entry(JournalEntryType.SESSION_STARTED, -1, "2026-09-01T20:00:00Z", {})
    
    # Mock builtins.open to throw PermissionError twice, then succeed
    call_count = 0
    real_open = open
    def mock_open(*args, **kwargs):
        nonlocal call_count
        if call_count < 2:
            call_count += 1
            raise PermissionError("Transient Windows Lock")
        return real_open(*args, **kwargs)
        
    with patch("builtins.open", side_effect=mock_open):
        entries, _ = JournalReader.read_and_recover_journal(writer.journal_path)
    
    assert len(entries) == 1
    assert call_count == 2


def test_journal_persistent_permission_error(temp_storage_root: Path) -> None:
    """Verify bounded retries surface a persistent PermissionError after exhaustion."""
    writer = JournalWriter(temp_storage_root, "sess_persistent", epoch_id=1)
    writer.initialize_storage()
    writer.append_entry(JournalEntryType.SESSION_STARTED, -1, "2026-09-01T20:00:00Z", {})
    
    with patch("builtins.open", side_effect=PermissionError("Persistent Windows Lock")):
        with pytest.raises(PermissionError, match="Persistent Windows Lock"):
            JournalReader.read_and_recover_journal(writer.journal_path)

