import os
import json
import pytest
from pathlib import Path
from datetime import datetime, timezone

from holomed.persistence.exceptions import PersistenceLifecycleError
from holomed.persistence.devices import DurableDeviceStore
from holomed.persistence.device_journal import DeviceJournalWriter
from holomed.persistence.models import DeviceJournalEntryType, TransactionState
from holomed.persistence.authority import DeviceEpochAuthority


def _create_legacy_journal(storage_root: Path, device_id: str, max_epoch: int = 5):
    """Creates a legacy journal without the DOMAIN_INITIALIZED marker and without device_epoch.json."""
    writer = DeviceJournalWriter(
        storage_root,
        device_id,
        epoch_id=max_epoch,
        authoritative_epoch_provider=None,
    )
    writer.initialize_storage()
    # Write a simple entry
    writer.append_entry(
        entry_type=DeviceJournalEntryType.DEVICE_QUARANTINED,
        timestamp_utc=datetime.now(timezone.utc).isoformat(),
        payload={"reason": "test"}
    )
    return writer.journal_path


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
    # Manually create the device epoch file
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
    
    # Manually append the marker
    writer = DeviceJournalWriter(tmp_path, device_id, 5, None)
    writer.append_entry(
        entry_type=DeviceJournalEntryType.DEVICE_EPOCH_DOMAIN_INITIALIZED,
        timestamp_utc=datetime.now(timezone.utc).isoformat(),
        payload={"migrated_epoch": 5}
    )
    
    # Do NOT create device_epoch.json
    
    store = DurableDeviceStore(tmp_path, epoch_id=42)
    with pytest.raises(PersistenceLifecycleError, match="Ambiguous legacy history.*has DEVICE_EPOCH_DOMAIN_INITIALIZED marker but no device_epoch.json"):
        store.restore_device_from_disk(device_id)


def test_m4_valid_initialized_state(tmp_path: Path):
    """M4: Valid initialized state: device epoch file and journal marker exist."""
    device_id = "12345678-1234-5678-1234-567812345678"
    _create_legacy_journal(tmp_path, device_id, max_epoch=5)
    
    store = DurableDeviceStore(tmp_path, epoch_id=42)
    store.restore_device_from_disk(device_id) # performs migration
    
    # Second instance should load without issue
    store2 = DurableDeviceStore(tmp_path, epoch_id=99)
    device = store2.restore_device_from_disk(device_id)
    assert device.epoch_id == 5


def _migrating_worker(storage_root_str, dev_id, sync_event):
    # We simulate holding the lock as if we are halfway through migration
    storage_root = Path(storage_root_str)
    auth = DeviceEpochAuthority(storage_root)
    lock_path = auth._get_lock_path(dev_id)
    if not lock_path.exists():
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        lock_path.touch()
        
    with open(lock_path, "a") as f:
        fd = f.fileno()
        auth._acquire_lock(fd)
        # Signal the main process that lock is held
        sync_event.set()
        import time
        time.sleep(1) # hold for a bit
        # process exits without writing device_epoch.json or marker
        # this simulates a crash mid-migration


def test_m5_concurrent_migration_blocked(tmp_path: Path):
    """M5: Crash boundary: concurrent migration is blocked by OS lock."""
    import multiprocessing
    
    device_id = "12345678-1234-5678-1234-567812345678"
    _create_legacy_journal(tmp_path, device_id, max_epoch=5)
    
    sync_event = multiprocessing.Event()
    p = multiprocessing.Process(target=_migrating_worker, args=(str(tmp_path), device_id, sync_event))
    p.start()
    
    assert sync_event.wait(timeout=5), "Worker didn't signal lock acquisition"
    
    store = DurableDeviceStore(tmp_path, epoch_id=42)
    
    # This should block until the other process crashes (exits and releases lock)
    # Then it should succeed
    device = store.restore_device_from_disk(device_id)
    assert device.epoch_id == 5
    p.join()


def test_m6_idempotency(tmp_path: Path):
    """M6: Idempotency: Restoring twice results in the same epoch and no duplicate markers."""
    device_id = "12345678-1234-5678-1234-567812345678"
    _create_legacy_journal(tmp_path, device_id, max_epoch=5)
    
    store = DurableDeviceStore(tmp_path, epoch_id=42)
    device1 = store.restore_device_from_disk(device_id)
    
    lines_after_first = (tmp_path / f"{device_id}.jsonl").read_text().strip().splitlines()
    
    device2 = store.restore_device_from_disk(device_id)
    
    lines_after_second = (tmp_path / f"{device_id}.jsonl").read_text().strip().splitlines()
    
    assert len(lines_after_first) == len(lines_after_second)
    assert device1.epoch_id == device2.epoch_id
