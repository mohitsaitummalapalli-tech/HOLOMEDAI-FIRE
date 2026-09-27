# -*- coding: utf-8 -*-
"""Sequence 6.4.2 — Device Epoch Durable Record / Migration: Adversarial Proofs."""

import os
import json
import multiprocessing
import pytest
from pathlib import Path
from datetime import datetime, timezone

from holomed.persistence.exceptions import (
    PersistenceLifecycleError,
    PersistenceResourceIntegrityError,
)
from holomed.persistence.devices import DurableDeviceStore
from holomed.persistence.device_journal import DeviceJournalReader, DeviceJournalWriter
from holomed.persistence.models import DeviceJournalEntryType, TransactionState
from holomed.persistence.authority import DeviceEpochAuthority


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _create_legacy_journal(storage_root: Path, device_id: str, max_epoch: int = 5):
    """Creates a legacy journal without the DOMAIN_INITIALIZED marker and without device_epoch.json."""
    writer = DeviceJournalWriter(
        storage_root,
        device_id,
        epoch_id=max_epoch,
        authoritative_epoch_provider=None,
    )
    writer.initialize_storage()
    writer.append_entry(
        entry_type=DeviceJournalEntryType.DEVICE_QUARANTINED,
        timestamp_utc=datetime.now(timezone.utc).isoformat(),
        payload={"reason": "test"}
    )
    return writer.journal_path


def _count_markers(storage_root: Path, device_id: str) -> int:
    """Count DEVICE_EPOCH_DOMAIN_INITIALIZED markers in the durable journal."""
    journal_path = storage_root / f"{device_id}.jsonl"
    entries, _ = DeviceJournalReader.read_and_recover_journal(journal_path)
    return sum(
        1 for e in entries
        if e.entry_type == DeviceJournalEntryType.DEVICE_EPOCH_DOMAIN_INITIALIZED
    )


def _authority_exists(storage_root: Path, device_id: str) -> bool:
    """Check if the device_epoch.json file exists for a device."""
    auth = DeviceEpochAuthority(storage_root)
    return auth._get_epoch_path(device_id).exists()


# ---------------------------------------------------------------------------
# M1-M4: Basic migration state machine
# ---------------------------------------------------------------------------

def test_m1_legacy_history_explicit_migration(tmp_path: Path):
    """M1: Legacy history with no device epoch file migrates explicitly."""
    device_id = "12345678-1234-5678-1234-567812345678"
    _create_legacy_journal(tmp_path, device_id, max_epoch=5)

    store = DurableDeviceStore(tmp_path, epoch_id=42)
    device = store.restore_device_from_disk(device_id)

    assert device.epoch_id == 5

    # Verify device_epoch.json was created
    auth = DeviceEpochAuthority(tmp_path)
    assert auth.read_current_device_epoch(device_id) == 5

    # Verify marker was appended
    lines = (tmp_path / f"{device_id}.jsonl").read_text().strip().splitlines()
    last_record = json.loads(lines[-1])
    assert last_record["entry_type"] == DeviceJournalEntryType.DEVICE_EPOCH_DOMAIN_INITIALIZED.value
    assert last_record["payload"]["migrated_epoch"] == 5


def test_m2_ambiguous_file_no_marker(tmp_path: Path):
    """M2: Ambiguous: device epoch file exists but no journal marker -> fails closed."""
    device_id = "12345678-1234-5678-1234-567812345678"
    _create_legacy_journal(tmp_path, device_id, max_epoch=5)

    auth = DeviceEpochAuthority(tmp_path)
    lock_path = auth._get_lock_path(device_id)
    lock_path.touch()
    with open(auth._get_epoch_path(device_id), "w") as f:
        json.dump({"device_epoch": 5}, f)

    store = DurableDeviceStore(tmp_path, epoch_id=42)
    with pytest.raises(PersistenceLifecycleError, match="Ambiguous legacy history.*has device_epoch.json but no"):
        store.restore_device_from_disk(device_id)


def test_m3_ambiguous_marker_no_file(tmp_path: Path):
    """M3: Ambiguous: journal marker exists but no device epoch file -> fails closed."""
    device_id = "12345678-1234-5678-1234-567812345678"
    _create_legacy_journal(tmp_path, device_id, max_epoch=5)

    writer = DeviceJournalWriter(tmp_path, device_id, 5, None)
    writer.append_entry(
        entry_type=DeviceJournalEntryType.DEVICE_EPOCH_DOMAIN_INITIALIZED,
        timestamp_utc=datetime.now(timezone.utc).isoformat(),
        payload={"migrated_epoch": 5}
    )

    store = DurableDeviceStore(tmp_path, epoch_id=42)
    with pytest.raises(PersistenceLifecycleError, match="Ambiguous legacy history.*has DEVICE_EPOCH_DOMAIN_INITIALIZED marker but no device_epoch.json"):
        store.restore_device_from_disk(device_id)


def test_m4_valid_initialized_state(tmp_path: Path):
    """M4: Valid initialized state: device epoch file and journal marker exist."""
    device_id = "12345678-1234-5678-1234-567812345678"
    _create_legacy_journal(tmp_path, device_id, max_epoch=5)

    store = DurableDeviceStore(tmp_path, epoch_id=42)
    store.restore_device_from_disk(device_id)  # performs migration

    store2 = DurableDeviceStore(tmp_path, epoch_id=99)
    device = store2.restore_device_from_disk(device_id)
    assert device.epoch_id == 5


# ---------------------------------------------------------------------------
# M5-A: Crash before any durable migration write
# ---------------------------------------------------------------------------

def _worker_m5_a(storage_root_str, dev_id):
    """Crash immediately — no durable migration state written."""
    os._exit(0)


def test_m5_a_crash_before_migration(tmp_path: Path):
    """M5-A: Process crashes before writing anything. Legacy state is pristine."""
    device_id = "12345678-1234-5678-1234-567812345678"
    _create_legacy_journal(tmp_path, device_id, max_epoch=5)

    p = multiprocessing.Process(target=_worker_m5_a, args=(str(tmp_path), device_id))
    p.start()
    p.join()

    # Verify: no device_epoch.json, no migration marker, legacy journal unchanged
    assert not _authority_exists(tmp_path, device_id), "device_epoch.json must not exist after pre-write crash"
    assert _count_markers(tmp_path, device_id) == 0, "No migration marker must exist"

    # Fresh recovery must succeed with full migration
    store = DurableDeviceStore(tmp_path, epoch_id=42)
    device = store.restore_device_from_disk(device_id)
    assert device.epoch_id == 5
    assert _count_markers(tmp_path, device_id) == 1


# ---------------------------------------------------------------------------
# M5-B: Crash after authority fsync, before marker — via real production path
# ---------------------------------------------------------------------------

def _worker_m5_b(storage_root_str, dev_id):
    """Run the real production migration path, crash after authority fsync."""
    storage_root = Path(storage_root_str)
    store = DurableDeviceStore(storage_root, epoch_id=42)

    def _crash_after_authority(phase):
        if phase == "after_authority_fsync":
            os._exit(0)

    store._migration_test_hook = _crash_after_authority
    # This will crash mid-migration after authority is written but before marker
    try:
        store.restore_device_from_disk(dev_id)
    except SystemExit:
        pass  # Should not reach here; os._exit bypasses this


def test_m5_b_crash_after_authority_fsync_before_marker(tmp_path: Path):
    """M5-B: Crash through real migration path after authority fsync, before marker.

    Post-crash state: device_epoch.json EXISTS, marker ABSENT.
    Fresh process must FAIL CLOSED and must NOT overwrite authority.
    """
    device_id = "12345678-1234-5678-1234-567812345678"
    _create_legacy_journal(tmp_path, device_id, max_epoch=5)

    p = multiprocessing.Process(target=_worker_m5_b, args=(str(tmp_path), device_id))
    p.start()
    p.join()

    # Verify post-crash durable state
    assert _authority_exists(tmp_path, device_id), "device_epoch.json must exist after authority fsync"
    assert _count_markers(tmp_path, device_id) == 0, "marker must be absent (crash before write)"

    # Read the authority epoch to verify it was written correctly
    auth = DeviceEpochAuthority(tmp_path)
    assert auth.read_current_device_epoch(device_id) == 5

    # Fresh process must fail closed — authority exists but no marker
    store = DurableDeviceStore(tmp_path, epoch_id=42)
    with pytest.raises(PersistenceLifecycleError, match="Ambiguous legacy history.*has device_epoch.json but no"):
        store.restore_device_from_disk(device_id)


# ---------------------------------------------------------------------------
# M5-C: Crash immediately after marker durability — via real production path
# ---------------------------------------------------------------------------

def _worker_m5_c(storage_root_str, dev_id):
    """Run the real production migration path, crash after marker append."""
    storage_root = Path(storage_root_str)
    store = DurableDeviceStore(storage_root, epoch_id=42)

    def _crash_after_marker(phase):
        if phase == "after_marker_append":
            os._exit(0)

    store._migration_test_hook = _crash_after_marker
    try:
        store.restore_device_from_disk(dev_id)
    except SystemExit:
        pass


def test_m5_c_crash_after_marker_durable_commit(tmp_path: Path):
    """M5-C: Crash through real migration path after marker append.

    Both authority and marker are durably committed.
    Fresh process must load successfully with the same epoch and exactly one marker.
    """
    device_id = "12345678-1234-5678-1234-567812345678"
    _create_legacy_journal(tmp_path, device_id, max_epoch=5)

    p = multiprocessing.Process(target=_worker_m5_c, args=(str(tmp_path), device_id))
    p.start()
    p.join()

    # Verify post-crash durable state: both must exist
    assert _authority_exists(tmp_path, device_id), "device_epoch.json must exist"
    assert _count_markers(tmp_path, device_id) == 1, "exactly one marker must exist"

    auth = DeviceEpochAuthority(tmp_path)
    assert auth.read_current_device_epoch(device_id) == 5

    # Fresh process loads successfully
    store = DurableDeviceStore(tmp_path, epoch_id=99)
    device = store.restore_device_from_disk(device_id)
    assert device.epoch_id == 5

    # Verify marker count is still exactly one (no duplication)
    assert _count_markers(tmp_path, device_id) == 1


# ---------------------------------------------------------------------------
# M5-D: Stale-snapshot concurrent migration race proof
# ---------------------------------------------------------------------------

def _worker_m5_d_proc_a(storage_root_str, dev_id, authority_durable_event, resume_event):
    """Process A: Run real restore_device_from_disk() with a test hook.

    Pauses after authority fsync (authority written, marker NOT yet written).
    Signals AUTHORITY_DURABLE, then waits for RESUME before continuing to
    write the marker and complete migration.
    """
    storage_root = Path(storage_root_str)
    store = DurableDeviceStore(storage_root, epoch_id=42)

    def _hook(phase):
        if phase == "after_authority_fsync":
            # Authority is durable, marker is NOT yet written.
            # Signal the test that this boundary has been reached.
            authority_durable_event.set()
            # Wait for the test to tell us to resume (bounded).
            resume_event.wait(timeout=15)

    store._migration_test_hook = _hook
    store.restore_device_from_disk(dev_id)
    os._exit(0)


def _worker_m5_d_proc_b(storage_root_str, dev_id, b_started_event, b_done_event):
    """Process B: Run a completely fresh restore_device_from_disk().

    This process has its own fresh DurableDeviceStore instance.
    It will read the initial journal (no marker), then attempt to acquire
    the migration lock. It must block until Process A finishes.
    Once it acquires the lock, it must re-read the journal under the lock,
    observe the completed migration, and succeed.
    """
    b_started_event.set()
    storage_root = Path(storage_root_str)
    store = DurableDeviceStore(storage_root, epoch_id=99)
    store.restore_device_from_disk(dev_id)
    b_done_event.set()
    os._exit(0)


def test_m5_d_concurrent_migration_lock_serialization(tmp_path: Path):
    """M5-D: Proves the stale-snapshot concurrent migration race is eliminated.

    This test would FAIL against the old implementation where the journal
    was read before the lock was acquired. Process B would see no marker
    in its stale snapshot, then find authority exists (written by A), and
    incorrectly enter CASE B (fail-closed).

    With the fix, Process B re-reads the journal under the lock and sees
    the completed migration.

    Sequence:
    1. Process A starts real restore, acquires lock, writes authority, pauses
       at after_authority_fsync hook, signals AUTHORITY_DURABLE
    2. Process B starts real restore (fresh store, fresh process)
       signals B_STARTED, then blocks on the migration lock
    3. Test verifies B cannot complete (bounded assertion)
    4. Test signals A to RESUME
    5. A writes marker, completes migration, exits (releases lock)
    6. B acquires lock, re-reads journal under lock, sees completed migration
    7. B succeeds (does NOT raise CASE B error)
    """
    device_id = "12345678-1234-5678-1234-567812345678"
    _create_legacy_journal(tmp_path, device_id, max_epoch=5)

    authority_durable_event = multiprocessing.Event()
    resume_event = multiprocessing.Event()
    b_started_event = multiprocessing.Event()
    b_done_event = multiprocessing.Event()

    # Start Process A — runs real migration, pauses after authority fsync
    proc_a = multiprocessing.Process(
        target=_worker_m5_d_proc_a,
        args=(str(tmp_path), device_id, authority_durable_event, resume_event),
    )
    proc_a.start()
    assert authority_durable_event.wait(timeout=10), \
        "Process A didn't reach after_authority_fsync hook"

    # At this point: authority written + fsynced, marker NOT yet written, lock held by A.

    # Start Process B — fresh store, fresh process
    proc_b = multiprocessing.Process(
        target=_worker_m5_d_proc_b,
        args=(str(tmp_path), device_id, b_started_event, b_done_event),
    )
    proc_b.start()
    assert b_started_event.wait(timeout=5), "Process B didn't start"

    # Verify: B cannot complete while A holds the lock
    assert not b_done_event.wait(timeout=2), \
        "Process B must NOT complete migration while Process A holds the lock"

    # Signal Process A to resume — it will write the marker, verify, release lock, exit
    resume_event.set()
    proc_a.join(timeout=10)

    # Process B should now acquire the lock, re-read journal, see migration, succeed
    assert b_done_event.wait(timeout=10), \
        "Process B must complete successfully after Process A finishes migration"
    proc_b.join(timeout=5)

    # Verify final durable state
    assert _authority_exists(tmp_path, device_id)
    assert _count_markers(tmp_path, device_id) == 1

    auth = DeviceEpochAuthority(tmp_path)
    assert auth.read_current_device_epoch(device_id) == 5

    # Verify marker epoch matches authority
    journal_path = tmp_path / f"{device_id}.jsonl"
    entries, _ = DeviceJournalReader.read_and_recover_journal(journal_path)
    markers = [
        e for e in entries
        if e.entry_type == DeviceJournalEntryType.DEVICE_EPOCH_DOMAIN_INITIALIZED
    ]
    assert len(markers) == 1
    assert markers[0].payload["migrated_epoch"] == 5

    # Fresh restore succeeds
    store = DurableDeviceStore(tmp_path, epoch_id=99)
    device = store.restore_device_from_disk(device_id)
    assert device.epoch_id == 5


# ---------------------------------------------------------------------------
# M6: Fresh-process idempotency
# ---------------------------------------------------------------------------

def _worker_m6_migrate(storage_root_str, dev_id):
    """Perform migration in a separate process."""
    store = DurableDeviceStore(Path(storage_root_str), epoch_id=42)
    store.restore_device_from_disk(dev_id)
    os._exit(0)


def _worker_m6_verify(storage_root_str, dev_id, result_dict):
    """Read durable state in a completely fresh process."""
    storage_root = Path(storage_root_str)
    store = DurableDeviceStore(storage_root, epoch_id=99)
    device = store.restore_device_from_disk(dev_id)
    auth = DeviceEpochAuthority(storage_root)
    epoch_from_authority = auth.read_current_device_epoch(dev_id)
    marker_count = _count_markers(storage_root, dev_id)
    result_dict["epoch_from_store"] = device.epoch_id
    result_dict["epoch_from_authority"] = epoch_from_authority
    result_dict["marker_count"] = marker_count


def test_m6_idempotency(tmp_path: Path):
    """M6: Fresh-process idempotency.

    Verifies:
    - fresh store #1 → migration
    - fresh store #2 → same durable state
    - fresh authority #1 → same epoch
    - fresh authority #2 → same epoch
    - exactly ONE DEVICE_EPOCH_DOMAIN_INITIALIZED marker
    All reads use durable sources, not in-memory cache.
    """
    device_id = "12345678-1234-5678-1234-567812345678"
    _create_legacy_journal(tmp_path, device_id, max_epoch=5)

    # Migration in a separate process
    p1 = multiprocessing.Process(target=_worker_m6_migrate, args=(str(tmp_path), device_id))
    p1.start()
    p1.join()

    # Verification pass 1 — fresh process
    mgr = multiprocessing.Manager()
    result1 = mgr.dict()
    p2 = multiprocessing.Process(target=_worker_m6_verify, args=(str(tmp_path), device_id, result1))
    p2.start()
    p2.join()

    # Verification pass 2 — another fresh process
    result2 = mgr.dict()
    p3 = multiprocessing.Process(target=_worker_m6_verify, args=(str(tmp_path), device_id, result2))
    p3.start()
    p3.join()

    # All durable reads must agree
    assert result1["epoch_from_store"] == 5
    assert result1["epoch_from_authority"] == 5
    assert result1["marker_count"] == 1

    assert result2["epoch_from_store"] == 5
    assert result2["epoch_from_authority"] == 5
    assert result2["marker_count"] == 1


# ---------------------------------------------------------------------------
# Corrupt authority fails closed
# ---------------------------------------------------------------------------

def test_corrupt_authority_fails_closed(tmp_path: Path):
    device_id = "12345678-1234-5678-1234-567812345678"
    _create_legacy_journal(tmp_path, device_id, max_epoch=5)

    auth = DeviceEpochAuthority(tmp_path)
    lock_path = auth._get_lock_path(device_id)
    lock_path.touch()
    with open(auth._get_epoch_path(device_id), "w") as f:
        f.write("{corrupt_json")

    store = DurableDeviceStore(tmp_path, epoch_id=42)
    with pytest.raises(PersistenceLifecycleError, match="Corrupt device epoch authority"):
        store.restore_device_from_disk(device_id)


# ---------------------------------------------------------------------------
# Marker / authority epoch mismatch
# ---------------------------------------------------------------------------

def test_marker_authority_mismatch(tmp_path: Path):
    device_id = "12345678-1234-5678-1234-567812345678"
    _create_legacy_journal(tmp_path, device_id, max_epoch=5)

    auth = DeviceEpochAuthority(tmp_path)
    lock_path = auth._get_lock_path(device_id)
    lock_path.touch()
    with open(auth._get_epoch_path(device_id), "w") as f:
        json.dump({"device_epoch": 6}, f)

    writer = DeviceJournalWriter(tmp_path, device_id, 5, None)
    writer.append_entry(
        entry_type=DeviceJournalEntryType.DEVICE_EPOCH_DOMAIN_INITIALIZED,
        timestamp_utc=datetime.now(timezone.utc).isoformat(),
        payload={"migrated_epoch": 5}
    )

    store = DurableDeviceStore(tmp_path, epoch_id=42)
    with pytest.raises(PersistenceResourceIntegrityError, match="Migration mismatch.*marker epoch 5 != authority epoch 6"):
        store.restore_device_from_disk(device_id)


# ---------------------------------------------------------------------------
# Cross-device isolation
# ---------------------------------------------------------------------------

def test_cross_device_isolation(tmp_path: Path):
    """Device A migration must not mutate Device B's durable state."""
    device_a = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
    device_b = "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"

    _create_legacy_journal(tmp_path, device_a, max_epoch=3)
    _create_legacy_journal(tmp_path, device_b, max_epoch=7)

    # Capture device B's pre-migration durable state
    journal_b_before = (tmp_path / f"{device_b}.jsonl").read_text()
    assert not _authority_exists(tmp_path, device_b)

    # Migrate device A only
    store = DurableDeviceStore(tmp_path, epoch_id=42)
    device_a_record = store.restore_device_from_disk(device_a)
    assert device_a_record.epoch_id == 3

    # Verify device A has its own authority and marker
    auth = DeviceEpochAuthority(tmp_path)
    assert auth.read_current_device_epoch(device_a) == 3
    assert _count_markers(tmp_path, device_a) == 1

    # Verify device B is completely untouched
    journal_b_after = (tmp_path / f"{device_b}.jsonl").read_text()
    assert journal_b_before == journal_b_after, "Device B journal must be unchanged"
    assert not _authority_exists(tmp_path, device_b), "Device B must not have device_epoch.json"
    assert _count_markers(tmp_path, device_b) == 0, "Device B must not have migration marker"

    # Now migrate device B independently
    store2 = DurableDeviceStore(tmp_path, epoch_id=99)
    device_b_record = store2.restore_device_from_disk(device_b)
    assert device_b_record.epoch_id == 7

    # Verify both are independently correct
    assert auth.read_current_device_epoch(device_a) == 3
    assert auth.read_current_device_epoch(device_b) == 7
    assert _count_markers(tmp_path, device_a) == 1
    assert _count_markers(tmp_path, device_b) == 1


# ---------------------------------------------------------------------------
# Durable post-migration verification (production code path)
# ---------------------------------------------------------------------------

def test_post_migration_durable_verification(tmp_path: Path):
    """Verify that production migration performs durable verification internally.

    The durable verification reads device_epoch.json and the journal marker
    from disk (not local variables) and confirms they agree.
    A successful restore implies the verification passed.
    """
    device_id = "12345678-1234-5678-1234-567812345678"
    _create_legacy_journal(tmp_path, device_id, max_epoch=5)

    store = DurableDeviceStore(tmp_path, epoch_id=42)
    device = store.restore_device_from_disk(device_id)

    # If we got here, the internal durable verification passed.
    # Cross-check from outside:
    auth = DeviceEpochAuthority(tmp_path)
    durable_epoch = auth.read_current_device_epoch(device_id)
    assert durable_epoch == device.epoch_id == 5

    journal_path = tmp_path / f"{device_id}.jsonl"
    entries, _ = DeviceJournalReader.read_and_recover_journal(journal_path)
    markers = [
        e for e in entries
        if e.entry_type == DeviceJournalEntryType.DEVICE_EPOCH_DOMAIN_INITIALIZED
    ]
    assert len(markers) == 1
    assert markers[0].payload["migrated_epoch"] == durable_epoch
