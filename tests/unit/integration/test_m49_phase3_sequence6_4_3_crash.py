import os
import multiprocessing
from pathlib import Path

def _child_process_crash(storage_root: str, device_id: str):
    from holomed.persistence.authority import DeviceEpochAuthority
    
    # 1. Allocate epoch
    auth = DeviceEpochAuthority(Path(storage_root))
    new_epoch = auth.allocate_next_device_epoch(device_id)
    
    # 2. Hard crash before any other operations
    os._exit(0)

def test_device_epoch_crash_boundary(tmp_path):
    storage_root = tmp_path / "devices"
    storage_root.mkdir(parents=True)
    device_id = "test-device-123"
    
    # Run the child process
    ctx = multiprocessing.get_context('spawn')
    p = ctx.Process(target=_child_process_crash, args=(str(storage_root), device_id))
    p.start()
    p.join()
    
    assert p.exitcode == 0
    
    # Parent verifies
    from holomed.persistence.authority import DeviceEpochAuthority
    auth = DeviceEpochAuthority(storage_root)
    
    current_epoch = auth.read_current_device_epoch(device_id)
    
    # The allocated epoch must be exactly 1, since the domain initialized at 0, next is 1
    # Actually, DeviceEpochAuthority allocates next epoch by reading, it might be 1.
    assert current_epoch is not None, "Crash resulted in missing epoch"
    assert current_epoch == 1, f"Expected epoch 1, got {current_epoch}"
    
    # Also verify that no DEVICE_READY state exists in the device journal!
    # Because allocating an epoch does NOT make the device READY.
    
    # The journal file should not even exist because commit_device_ready (which initializes/writes) was never called.
    journal_path = storage_root.parent / f"{device_id}.jsonl"
    assert not journal_path.exists(), f"Journal {journal_path} must not exist without commit_device_ready"

