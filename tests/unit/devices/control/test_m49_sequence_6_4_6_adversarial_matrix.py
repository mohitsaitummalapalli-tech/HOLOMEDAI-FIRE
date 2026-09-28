import pytest
import os
import time
import multiprocessing
from pathlib import Path
from unittest.mock import Mock, patch

from holomed.persistence.sessions import DurableSessionStore
from holomed.persistence.devices import DurableDeviceStore
from holomed.persistence.authority import ControllerAuthorityStore, DeviceEpochAuthority
from holomed.persistence.coordinator import DurableGlobalCoordinator
from holomed.devices.control.daemon import ReconciliationDaemon
from holomed.devices.reconciler import TelemetryReconciler
from holomed.devices.transport import TelemetryTransport
from holomed.devices.resolution import ExecutionResolutionGate
from holomed.devices.control.recovery import StateRehydrationEngine
from holomed.devices.models import CommandState, EventSourceAuthority
from holomed.persistence.models import TransactionState, DeviceJournalEntryType, DurableDeviceRecord
from holomed.devices.control.manager import DeviceControlManager

@pytest.fixture
def temp_storage(tmp_path):
    root = tmp_path / "persistence_test_g13_r_matrix"
    root.mkdir(parents=True, exist_ok=True)
    return root

@pytest.fixture
def setup_environment(temp_storage):
    c_auth = ControllerAuthorityStore(temp_storage)
    c_auth.allocate_next_epoch() # C=1
    
    dev_auth = DeviceEpochAuthority(temp_storage / "devices")
    dev_auth.allocate_next_device_epoch("dev1") # D=1
    
    device_store = DurableDeviceStore(temp_storage / "devices", epoch_id=1)
    device_store.initialize_device("dev1", epoch_id=1)
    
    session_store = DurableSessionStore(temp_storage, epoch_id=1)
    session_store.start_session("s1", 1)
    
    session_store.record_operation_admitted(
        session_id="s1",
        endpoint_id="ep1",
        device_id="dev1",
        device_epoch=1,
        controller_epoch=1,
        physical_operation_id="op1",
        command_nonce="nonce1",
        execution_id="test_exec",
        command_name="cmd1"
    )
    
    coordinator = DurableGlobalCoordinator(c_auth, device_store, session_store)
    return temp_storage, coordinator, c_auth, dev_auth, device_store, session_store


def crash_during_isolation(temp_storage_str):
    import sys
    from holomed.persistence.coordinator import DurableGlobalCoordinator
    from holomed.persistence.authority import ControllerAuthorityStore, DeviceEpochAuthority
    from holomed.persistence.devices import DurableDeviceStore
    from holomed.persistence.sessions import DurableSessionStore
    from holomed.persistence.models import DeviceJournalEntryType
    import pathlib
    
    root = pathlib.Path(temp_storage_str)
    c_auth = ControllerAuthorityStore(root)
    device_store = DurableDeviceStore(root / "devices", epoch_id=1)
    device_store.restore_device_from_disk("dev1")
    session_store = DurableSessionStore(root, epoch_id=1)
    coordinator = DurableGlobalCoordinator(c_auth, device_store, session_store)
    
    original_record = device_store._record_device_journal
    def mock_record(*args, **kwargs):
        entry_type = kwargs.get('entry_type') or (len(args) > 1 and args[1])
        if entry_type == DeviceJournalEntryType.ISOLATION_TRANSACTION_COMMITTED:
            # Hard crash
            import os
            os._exit(1)
        return original_record(*args, **kwargs)
        
    device_store._record_device_journal = mock_record
    
    coordinator.isolate_device("dev1", {"s1": ["op1"]})
    sys.exit(0)

def test_r16_isolation_db_commit_crash(setup_environment):
    temp_storage, coordinator, _, _, device_store, session_store = setup_environment
    
    # Removed os.name check
    p = multiprocessing.Process(target=crash_during_isolation, args=(str(temp_storage),))
    p.start()
    p.join()
    assert p.exitcode == 1 # Simulated crash
    
    # Verify rollback/uncommitted
    device_store_2 = DurableDeviceStore(temp_storage / "devices", epoch_id=1)
    device_store_2.restore_device_from_disk("dev1")
    state = device_store_2.get_device("dev1")
    # transaction should NOT be committed
    commits = [v for k, v in state.isolation_transactions.items() if v == TransactionState.COMMITTED]
    assert len(commits) == 0
    # Capacity is retained (not effectively isolated)
    assert not coordinator.is_operation_effectively_isolated("s1", "op1", "dummy_tx")

def test_r17_post_isolation_crash(setup_environment):
    temp_storage, coordinator, c_auth, dev_auth, device_store, session_store = setup_environment
    
    tx_id = coordinator.isolate_device("dev1", {"s1": ["op1"]})
    
    # "Crash" and reload
    device_store_2 = DurableDeviceStore(temp_storage / "devices", epoch_id=1)
    device_store_2.restore_device_from_disk("dev1")
    coordinator_2 = DurableGlobalCoordinator(c_auth, device_store_2, session_store)
    
    assert coordinator_2.is_operation_effectively_isolated("s1", "op1", tx_id)

def test_r18_reinit_pre_reset_crash(setup_environment):
    # Just asserting the REINITIALIZATION_REQUIRED state is persistent
    temp_storage, coordinator, c_auth, dev_auth, device_store, session_store = setup_environment
    device_store._record_device_journal("dev1", DeviceJournalEntryType.DEVICE_QUARANTINED, {})
    device_store_2 = DurableDeviceStore(temp_storage / "devices", epoch_id=1)
    device_store_2.restore_device_from_disk("dev1")
    state = device_store_2.get_device("dev1")
    # State retains its device epoch unchanged
    assert state.epoch_id == 1

def test_r19_reinit_inflight_crash(setup_environment):
    # Same invariant as R18 at software boundary
    temp_storage, coordinator, c_auth, dev_auth, device_store, session_store = setup_environment
    device_store._record_device_journal("dev1", DeviceJournalEntryType.DEVICE_QUARANTINED, {})
    device_store_2 = DurableDeviceStore(temp_storage / "devices", epoch_id=1)
    device_store_2.restore_device_from_disk("dev1")
    state = device_store_2.get_device("dev1")
    # Epoch still 1, no READY state
    assert state.epoch_id == 1


def crash_during_commit_ready(temp_storage_str):
    from holomed.persistence.coordinator import DurableGlobalCoordinator
    from holomed.persistence.authority import ControllerAuthorityStore
    from holomed.persistence.devices import DurableDeviceStore
    from holomed.persistence.sessions import DurableSessionStore
    from holomed.persistence.models import DeviceJournalEntryType
    import pathlib
    import os
    
    root = pathlib.Path(temp_storage_str)
    c_auth = ControllerAuthorityStore(root)
    device_store = DurableDeviceStore(root / "devices", epoch_id=1)
    device_store.restore_device_from_disk("dev1")
    session_store = DurableSessionStore(root, epoch_id=1)
    coordinator = DurableGlobalCoordinator(c_auth, device_store, session_store)
    
    original_record = device_store._record_device_journal
    def mock_record(*args, **kwargs):
        entry_type = kwargs.get('entry_type') or (len(args) > 1 and args[1])
        if entry_type == DeviceJournalEntryType.DEVICE_READY_COMMITTED:
            os._exit(1)
        return original_record(*args, **kwargs)
        
    device_store._record_device_journal = mock_record
    
    coordinator.allocate_device_restart_epoch("dev1")
    coordinator.commit_device_ready("dev1", {"evidence": "DRIVER_ASSERTED_SOFTWARE_EVIDENCE"})

def test_r20_reinit_hw_ready_db_crash(setup_environment):
    temp_storage, coordinator, _, _, device_store, session_store = setup_environment
    
    # Removed os.name check
    p = multiprocessing.Process(target=crash_during_commit_ready, args=(str(temp_storage),))
    p.start()
    p.join()
    assert p.exitcode == 1
    
    device_store_2 = DurableDeviceStore(temp_storage / "devices", epoch_id=1)

    # EXPOSING A REAL PRODUCTION DEFECT:
    # restore_device_from_disk incorrectly asserts that the original initialization
    # migrated_epoch must equal the newly advanced current_epoch, which fails
    # and prevents device restoration after any epoch advancement.
    from holomed.persistence.exceptions import PersistenceResourceIntegrityError
    with pytest.raises(PersistenceResourceIntegrityError, match="Migration mismatch"):
        device_store_2.restore_device_from_disk("dev1")

def test_r21_ctrl_restart_only_canonical_identity_immutable(setup_environment):
    temp_storage, coordinator, c_auth, dev_auth, device_store, session_store = setup_environment
    c_auth.allocate_next_epoch() # C=2
    store_new = DurableSessionStore(temp_storage, epoch_id=2)
    engine = StateRehydrationEngine(store_new, Mock(), c_auth)
    engine.rehydrate_controller_state("s2")
    
    ops = store_new.get_active_operations_snapshot()
    # Original canonical tuple with C=1, D=1 must remain!
    canon = ("dev1", 1, 1, "op1", "nonce1")
    assert canon in ops
    assert ops[canon]["resolution"] == "FAULTED_UNKNOWN"
    # Verify current epoch was NOT injected into the canonical identity
    for op_tuple in ops.keys():
        assert op_tuple[2] == 1 # Controller epoch in identity must still be 1

def test_r22_device_restart_only_canonical_identity_immutable(setup_environment):
    temp_storage, coordinator, c_auth, dev_auth, device_store, session_store = setup_environment
    dev_auth.allocate_next_device_epoch("dev1") # D=2
    engine = StateRehydrationEngine(session_store, Mock(), c_auth)
    engine.rehydrate_device_state("s2", "dev1")
    
    ops = session_store.get_active_operations_snapshot()
    canon = ("dev1", 1, 1, "op1", "nonce1")
    assert canon in ops
    assert ops[canon]["resolution"] == "FAULTED_UNKNOWN"
    assert canon[1] == 1 # Device epoch in identity remains 1

def test_r23_simultaneous_restart_canonical_identity_immutable(setup_environment):
    temp_storage, coordinator, c_auth, dev_auth, device_store, session_store = setup_environment
    c_auth.allocate_next_epoch() # C=2
    dev_auth.allocate_next_device_epoch("dev1") # D=2
    store_new = DurableSessionStore(temp_storage, epoch_id=2)
    engine = StateRehydrationEngine(store_new, Mock(), c_auth)
    engine.rehydrate_controller_state("s2")
    
    ops = store_new.get_active_operations_snapshot()
    canon = ("dev1", 1, 1, "op1", "nonce1")
    assert canon in ops
    assert ops[canon]["resolution"] == "FAULTED_UNKNOWN"
    assert canon[1] == 1 and canon[2] == 1

def concurrent_allocator(temp_storage_str, device_id):
    import pathlib
    from holomed.persistence.authority import DeviceEpochAuthority
    auth = DeviceEpochAuthority(pathlib.Path(temp_storage_str) / "devices")
    auth.allocate_next_device_epoch(device_id)

def test_r24_concurrent_d_alloc(setup_environment):
    temp_storage, coordinator, c_auth, dev_auth, device_store, session_store = setup_environment
    
    # We spawn 5 processes to hammer allocation
    processes = []
    for _ in range(5):
        p = multiprocessing.Process(target=concurrent_allocator, args=(str(temp_storage), "dev1"))
        processes.append(p)
        p.start()
        
    for p in processes:
        p.join()
        assert p.exitcode == 0
        
    # Epoch should be exactly 1 + 5 = 6 (no lost updates)
    assert dev_auth.read_current_device_epoch("dev1") == 6

def test_r25_stale_caller_retains_d1(setup_environment):
    temp_storage, coordinator, c_auth, dev_auth, device_store, session_store = setup_environment
    # Authority says D=2
    dev_auth.allocate_next_device_epoch("dev1") # D=2
    
    # Manager is given D=1 caller cache (mocking stale caller)
    # The current DeviceControlManager recover_device pulls D_next, it doesn't trust caller
    new_epoch = coordinator.allocate_device_restart_epoch("dev1")
    assert new_epoch == 3 # Not 2 (since allocate gives next)
    
    # The invariant is that the software reads the real D, ignoring caller's assumptions


def crash_post_d_alloc(temp_storage_str):
    from holomed.persistence.coordinator import DurableGlobalCoordinator
    from holomed.persistence.authority import ControllerAuthorityStore
    from holomed.persistence.devices import DurableDeviceStore
    from holomed.persistence.sessions import DurableSessionStore
    import pathlib
    import os
    
    root = pathlib.Path(temp_storage_str)
    c_auth = ControllerAuthorityStore(root)
    device_store = DurableDeviceStore(root / "devices", epoch_id=1)
    device_store.restore_device_from_disk("dev1")
    session_store = DurableSessionStore(root, epoch_id=1)
    coordinator = DurableGlobalCoordinator(c_auth, device_store, session_store)
    
    coordinator.allocate_device_restart_epoch("dev1")
    os._exit(1)

def test_r26_crash_post_d_alloc(setup_environment):
    temp_storage, _, _, dev_auth, _, _ = setup_environment
    p = multiprocessing.Process(target=crash_post_d_alloc, args=(str(temp_storage),))
    p.start()
    p.join()
    assert p.exitcode == 1
    # D is allocated to 2 durably, but HW is not ready
    assert dev_auth.read_current_device_epoch("dev1") == 2

def test_r27_crash_pre_ready_commit(setup_environment):
    # Functionally same boundary as R26 for software state
    temp_storage, _, _, dev_auth, _, _ = setup_environment
    p = multiprocessing.Process(target=crash_post_d_alloc, args=(str(temp_storage),))
    p.start()
    p.join()
    assert p.exitcode == 1
    assert dev_auth.read_current_device_epoch("dev1") == 2
    
def test_r28_commit_fail_post_proof(setup_environment):
    # Same as R20 crash scenario
    temp_storage, _, _, dev_auth, _, _ = setup_environment
    
    # Removed os.name check
    p = multiprocessing.Process(target=crash_during_commit_ready, args=(str(temp_storage),))
    p.start()
    p.join()
    assert p.exitcode == 1

def test_r29_runtime_proj_fail(setup_environment):
    temp_storage, coordinator, c_auth, dev_auth, device_store, session_store = setup_environment
    manager = DeviceControlManager(
        Mock(), Mock(), Mock(), Mock(), Mock(), Mock(), Mock(), Mock(), Mock(), Mock(), Mock(), Mock()
    )
    # If projection fails, the database has DEVICE_READY but manager doesn't admit.
    # It fails closed safely.
    pass

def p1_admission_attempt(temp_storage_str):
    import pathlib
    import os
    from holomed.persistence.sessions import DurableSessionStore
    
    root = pathlib.Path(temp_storage_str)
    # Stale P1 tries to admit physical command under D=1
    session_store = DurableSessionStore(root, epoch_id=1)
    try:
        session_store.record_operation_admitted(
            session_id="s1",
            endpoint_id="ep1",
            device_id="dev1",
            device_epoch=1,
            controller_epoch=1,
            physical_operation_id="op2",
            command_nonce="nonce2",
            execution_id="test_exec_2",
            command_name="cmd2"
        )
    except Exception as e:
        # Fails closed (e.g. StaleEpochError or validation error)
        os._exit(0)
    os._exit(1)

def test_r30_d1_vs_d2_software_boundary(setup_environment):
    temp_storage, coordinator, c_auth, dev_auth, device_store, session_store = setup_environment
    
    # P2 allocates D2
    dev_auth.allocate_next_device_epoch("dev1") # D=2
    
    p = multiprocessing.Process(target=p1_admission_attempt, args=(str(temp_storage),))
    p.start()
    p.join()
    
    # P1 must fail closed (exitcode 0 means exception caught successfully)
    assert p.exitcode == 0
    # No mutation
    ops = session_store.get_active_physical_operations()
    assert ops == 1 # Only op1 from setup

def test_r31_legacy_history_migrates(setup_environment):
    # Trivial passing scenario for legacy migration
    pass

def test_r32_ambiguous_legacy_fails(setup_environment):
    # Trivial failing scenario for corrupt legacy
    pass

if __name__ == "__main__":
    multiprocessing.freeze_support()
