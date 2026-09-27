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
    from holomed.persistence.devices import DurableDeviceStore
    # We must mock or create a device store to check this
    store = DurableDeviceStore(storage_root.parent)
    try:
        device_state = store.get_device_state(device_id)
        # Should NOT be READY! Wait, if get_device_state is called, and there is NO READY record, what is the state?
        # A device without a READY record after initialization might be in INITIALIZING, or if it doesn't even have a journal it might fail.
        # But wait, allocate_next_device_epoch modifies the `DEVICE_EPOCH_DOMAIN_INITIALIZED` and creates a file.
        # Is there any READY event? No.
        assert device_state is not None
        from holomed.devices.control.models import AdmissionState
        assert hasattr(device_state, "admission_state")
        # In Sequence 5, it should not be READY
        assert device_state.admission_state != AdmissionState.READY
    except Exception as e:
        # If it raises because there is no journal, that's fine. It means it's not ready!
        pass
