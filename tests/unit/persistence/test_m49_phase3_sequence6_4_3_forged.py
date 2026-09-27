import pytest
from holomed.persistence.coordinator import DurableGlobalCoordinator
from holomed.persistence.sessions import DurableSessionStore
from holomed.persistence.devices import DurableDeviceStore
from holomed.persistence.authority import ControllerAuthorityStore, DeviceEpochAuthority
from holomed.persistence.exceptions import PersistenceEpochMismatchError, PersistenceResourceMissingError

def test_forged_epoch_rejection(tmp_path):
    storage_root = tmp_path / "holomed_data"
    storage_root.mkdir(parents=True)
    
    auth_store = ControllerAuthorityStore(storage_root)
    device_store = DurableDeviceStore(storage_root / "devices")
    session_store = DurableSessionStore(storage_root, epoch_id=1)
    
    coord = DurableGlobalCoordinator(auth_store, device_store, session_store)
    
    device_id = "test-device-forged"
    
    # We must first initialize the session store since it requires an initialized domain
    auth_store.allocate_next_epoch()
    session_id = session_store.start_session("system", 1).session_id
    
    # Device epoch authority
    dev_auth = DeviceEpochAuthority(storage_root / "devices")
    
    # It must fail when no epoch is allocated at all (PersistenceResourceMissingError propagates from sessions.py and is caught or bubbled)
    # Wait, in sessions.py we removed the try/except, so it will raise PersistenceResourceMissingError because the file doesn't exist!
    with pytest.raises((PersistenceEpochMismatchError, PersistenceResourceMissingError)):
        session_store.record_operation_admitted(
            session_id=session_id,
            endpoint_id="ep-1",
            device_id=device_id,
            device_epoch=999,  # completely forged
            controller_epoch=1,
            physical_operation_id=None,
            command_nonce="nonce-1",
            execution_id="exec-1",
            command_name="test_cmd"
        )
    
    # Now allocate epoch 1
    true_epoch = coord.allocate_device_restart_epoch(device_id)
    assert true_epoch == 1
    
    # Try to admit with epoch 2 (forged)
    with pytest.raises(PersistenceEpochMismatchError, match="Stale device epoch 2 rejected; authoritative device epoch is 1"):
        session_store.record_operation_admitted(
            session_id=session_id,
            endpoint_id="ep-1",
            device_id=device_id,
            device_epoch=2,  # forged
            controller_epoch=1,
            physical_operation_id=None,
            command_nonce="nonce-2",
            execution_id="exec-2",
            command_name="test_cmd"
        )
        
    # Try to admit with epoch 0 (stale)
    with pytest.raises(PersistenceEpochMismatchError, match="Stale device epoch 0 rejected; authoritative device epoch is 1"):
        session_store.record_operation_admitted(
            session_id=session_id,
            endpoint_id="ep-1",
            device_id=device_id,
            device_epoch=0,  # stale
            controller_epoch=1,
            physical_operation_id=None,
            command_nonce="nonce-3",
            execution_id="exec-3",
            command_name="test_cmd"
        )
        
    # Admit with correct epoch to get an operation
    op_id, is_replay, res = session_store.record_operation_admitted(
        session_id=session_id,
        endpoint_id="ep-1",
        device_id=device_id,
        device_epoch=1,  # correct
        controller_epoch=1,
        physical_operation_id=None,
        command_nonce="nonce-valid",
        execution_id="exec-valid",
        command_name="test_cmd"
    )
    
    # Now try to terminate with forged epoch
    with pytest.raises(PersistenceEpochMismatchError):
        session_store.record_operation_terminated(
            session_id=session_id,
            device_id=device_id,
            device_epoch=999,  # forged
            controller_epoch=1,
            physical_operation_id=op_id,
            command_nonce="nonce-valid",
            resolution="SUCCESS"
        )
        
    # Terminate with correct epoch
    session_store.record_operation_terminated(
        session_id=session_id,
        device_id=device_id,
        device_epoch=1,  # correct
        controller_epoch=1,
        physical_operation_id=op_id,
        command_nonce="nonce-valid",
        resolution="SUCCESS"
    )
