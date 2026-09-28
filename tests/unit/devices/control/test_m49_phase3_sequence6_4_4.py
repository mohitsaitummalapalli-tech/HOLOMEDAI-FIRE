import pytest
import os
import tempfile
from pathlib import Path
from unittest.mock import Mock, patch

from holomed.persistence.sessions import DurableSessionStore
from holomed.persistence.authority import ControllerAuthorityStore, DeviceEpochAuthority
from holomed.devices.control.recovery import StateRehydrationEngine
from holomed.persistence.exceptions import PersistenceResourceIntegrityError

@pytest.fixture
def temp_storage():
    with tempfile.TemporaryDirectory() as td:
        yield Path(td)

@pytest.fixture
def stores(temp_storage):
    c_auth = ControllerAuthorityStore(temp_storage)
    c_auth.allocate_next_epoch()
    d_auth = DeviceEpochAuthority(temp_storage / "devices")
    d_auth.allocate_next_device_epoch("dev1")
    session_store = DurableSessionStore(temp_storage, epoch_id=1)
    return c_auth, d_auth, session_store

def test_controller_restart_does_not_advance_device_epoch(stores):
    c_auth, d_auth, _ = stores
    
    assert c_auth.read_current_epoch() == 1
    assert d_auth.read_current_device_epoch("dev1") == 1
    
    c_auth.allocate_next_epoch()
    
    assert c_auth.read_current_epoch() == 2
    assert d_auth.read_current_device_epoch("dev1") == 1

def test_device_restart_does_not_advance_controller_epoch(stores):
    c_auth, d_auth, _ = stores
    
    assert c_auth.read_current_epoch() == 1
    assert d_auth.read_current_device_epoch("dev1") == 1
    
    d_auth.allocate_next_device_epoch("dev1")
    
    assert c_auth.read_current_epoch() == 1
    assert d_auth.read_current_device_epoch("dev1") == 2

def test_recovery_reads_independent_controller_and_device_epochs(stores, temp_storage):
    c_auth, d_auth, _ = stores
    c_auth.allocate_next_epoch()
    d_auth.allocate_next_device_epoch("dev1")
    d_auth.allocate_next_device_epoch("dev1")
    
    assert c_auth.read_current_epoch() == 2
    assert d_auth.read_current_device_epoch("dev1") == 3
    
    session_store = DurableSessionStore(temp_storage, epoch_id=2)
    engine = StateRehydrationEngine(session_store, Mock(), c_auth)
    
    # We will trace the authority reads using patches
    with patch.object(c_auth, 'read_current_epoch', return_value=2) as mock_c_read, \
         patch.object(engine._device_authority, 'read_current_device_epoch', return_value=3) as mock_d_read:
        engine.rehydrate_device_state("s2", "dev1")
        
        mock_c_read.assert_called_once()
        mock_d_read.assert_called_once_with("dev1")

def test_controller_recovery_preserves_historical_device_epoch(stores, temp_storage):
    c_auth, d_auth, session_store = stores
    session_store.start_session("s1", 1)
    session_store.record_operation_admitted(
        session_id="s1",
        device_id="dev1",
        device_epoch=1,
        controller_epoch=1,
        physical_operation_id="op1",
        command_nonce="nonce1",
        execution_id="test",
        endpoint_id="ep1",
        command_name="cmd1"
    )
    
    c_auth.allocate_next_epoch() # C2
    new_store = DurableSessionStore(temp_storage, epoch_id=2)
    
    engine = StateRehydrationEngine(new_store, Mock(), c_auth)
    engine.rehydrate_controller_state("s2")
    
    ops = new_store.get_active_operations_snapshot()
    canon = ("dev1", 1, 1, "op1", "nonce1")
    assert canon in ops
    assert ops[canon]["resolution"] == "FAULTED_UNKNOWN"

def test_device_recovery_preserves_historical_controller_epoch(stores, temp_storage):
    c_auth, d_auth, session_store = stores
    session_store.start_session("s1", 1)
    session_store.record_operation_admitted(
        session_id="s1",
        device_id="dev1",
        device_epoch=1,
        controller_epoch=1,
        physical_operation_id="op1",
        command_nonce="nonce1",
        execution_id="test",
        endpoint_id="ep1",
        command_name="cmd1"
    )
    
    d_auth.allocate_next_device_epoch("dev1") # D2
    
    new_store = DurableSessionStore(temp_storage, epoch_id=1)
    engine = StateRehydrationEngine(new_store, Mock(), c_auth)
    engine.rehydrate_device_state("s2", "dev1")
    
    ops = new_store.get_active_operations_snapshot()
    canon = ("dev1", 1, 1, "op1", "nonce1")
    assert canon in ops
    assert ops[canon]["resolution"] == "FAULTED_UNKNOWN"

def test_simultaneous_restart_has_independent_c_and_d(stores, temp_storage):
    c_auth, d_auth, session_store = stores
    session_store.start_session("s1", 1)
    session_store.record_operation_admitted(
        session_id="s1",
        device_id="dev1",
        device_epoch=1,
        controller_epoch=1,
        physical_operation_id="op1",
        command_nonce="nonce1",
        execution_id="test",
        endpoint_id="ep1",
        command_name="cmd1"
    )
    
    c_auth.allocate_next_epoch() # C2
    d_auth.allocate_next_device_epoch("dev1") # D2
    
    new_store = DurableSessionStore(temp_storage, epoch_id=2)
    engine = StateRehydrationEngine(new_store, Mock(), c_auth)
    # Controller recovery will quarantine it
    engine.rehydrate_controller_state("s2")
    # Device recovery will see it is already quarantined
    engine.rehydrate_device_state("s2", "dev1")
    
    ops = new_store.get_active_operations_snapshot()
    canon = ("dev1", 1, 1, "op1", "nonce1")
    assert canon in ops
    
    assert c_auth.read_current_epoch() == 2
    assert d_auth.read_current_device_epoch("dev1") == 2

def test_missing_controller_authority_fails_closed(stores, temp_storage):
    c_auth, d_auth, session_store = stores
    # Corrupt it
    os.remove(temp_storage / "controller_epoch.json")
    
    engine = StateRehydrationEngine(session_store, Mock(), c_auth)
    with pytest.raises(PersistenceResourceIntegrityError):
        engine.rehydrate_controller_state("s2")
        
    with pytest.raises(PersistenceResourceIntegrityError):
        engine.rehydrate_device_state("s2", "dev1")

def test_missing_device_authority_fails_closed(stores, temp_storage):
    c_auth, d_auth, session_store = stores
    # Corrupt device authority
    os.remove(temp_storage / "devices" / "dev1" / "device_epoch.json")
    
    engine = StateRehydrationEngine(session_store, Mock(), c_auth)
    with pytest.raises(PersistenceResourceIntegrityError):
        engine.rehydrate_device_state("s2", "dev1")

def test_recovery_does_not_allocate_device_epoch(stores):
    c_auth, d_auth, session_store = stores
    engine = StateRehydrationEngine(session_store, Mock(), c_auth)
    
    with patch.object(engine._device_authority, 'allocate_next_device_epoch') as mock_allocate:
        engine.rehydrate_device_state("s2", "dev1")
        mock_allocate.assert_not_called()

def test_unresolved_capacity_survives_rehydration(stores, temp_storage):
    c_auth, d_auth, session_store = stores
    session_store.start_session("s1", 1)
    session_store.record_operation_admitted(
        session_id="s1",
        device_id="dev1",
        device_epoch=1,
        controller_epoch=1,
        physical_operation_id="op1",
        command_nonce="nonce1",
        execution_id="test",
        endpoint_id="ep1",
        command_name="cmd1"
    )
    
    assert session_store.get_active_physical_operations() == 1
    
    c_auth.allocate_next_epoch()
    new_store = DurableSessionStore(temp_storage, epoch_id=2)
    engine = StateRehydrationEngine(new_store, Mock(), c_auth)
    engine.rehydrate_controller_state("s2")
    
    # Still 1 active capacity
    assert new_store.get_active_physical_operations() == 1
    
    ops = new_store.get_active_operations_snapshot()
    canon = ("dev1", 1, 1, "op1", "nonce1")
    assert ops[canon]["resolution"] == "FAULTED_UNKNOWN"

def test_rehydration_does_not_rewrite_canonical_identity(stores, temp_storage):
    c_auth, d_auth, session_store = stores
    session_store.start_session("s1", 1)
    session_store.record_operation_admitted(
        session_id="s1",
        device_id="dev1",
        device_epoch=1,
        controller_epoch=1,
        physical_operation_id="op1",
        command_nonce="nonce1",
        execution_id="test",
        endpoint_id="ep1",
        command_name="cmd1"
    )
    
    c_auth.allocate_next_epoch()
    new_store = DurableSessionStore(temp_storage, epoch_id=2)
    engine = StateRehydrationEngine(new_store, Mock(), c_auth)
    engine.rehydrate_controller_state("s2")
    
    ops = new_store.get_active_operations_snapshot()
    assert list(ops.keys()) == [("dev1", 1, 1, "op1", "nonce1")]

from holomed.devices.control.manager import DeviceControlManager, AdmissionState
from holomed.devices.registry import DeviceRegistry
from holomed.devices.transport import TelemetryTransport
from holomed.devices.reconciler import TelemetryReconciler
from holomed.devices.resolution import ExecutionResolutionGate
from holomed.devices.control.daemon import ReconciliationDaemon
from holomed.devices.models import ExecutionTelemetryEvent, CommandState, EventSourceAuthority

def test_pre_ready_recovery_blocks_telemetry_mutation(stores, temp_storage):
    """
    Simulate that telemetry cannot mutate recovered durable state before readiness.
    Actually this tests that recovery correctly quarantines and telemetry doesn't un-quarantine it.
    """
    c_auth, d_auth, session_store = stores
    session_store.start_session("s1", 1)
    session_store.record_operation_admitted(
        session_id="s1",
        device_id="dev1",
        device_epoch=1,
        controller_epoch=1,
        physical_operation_id="op1",
        command_nonce="nonce1",
        execution_id="test",
        endpoint_id="ep1",
        command_name="cmd1"
    )
    
    assert session_store.get_active_physical_operations() == 1
    
    c_auth.allocate_next_epoch()
    new_store = DurableSessionStore(temp_storage, epoch_id=2)
    engine = StateRehydrationEngine(new_store, Mock(), c_auth)
    
    transport = TelemetryTransport()
    resolution_gate = ExecutionResolutionGate()
    reconciler = TelemetryReconciler(transport, resolution_gate)
    daemon = ReconciliationDaemon(reconciler, new_store)
    
    registry = Mock()
    manager = DeviceControlManager(
        registry=registry,
        rehydration_engine=engine,
        reconciliation_daemon=daemon
    )
    
    # 2. Put the control plane into AdmissionState.REHYDRATING
    manager._admission_state = AdmissionState.REHYDRATING
    engine.rehydrate_controller_state("s2")
    
    # 4. Before recovery finishes, publish a terminal telemetry event for that execution
    event = ExecutionTelemetryEvent(
        event_id="evt1",
        endpoint_id="ep1",
        session_id="s1",
        endpoint_lease_generation=1,
        execution_id="test",
        command_sequence=1,
        event_sequence=1,
        event_type="test_event",
        observed_state=CommandState.COMPLETED,
        source_authority=EventSourceAuthority.HARDWARE_DRIVER,
        source_origin="TestDriver",
        timestamp_utc="2026-09-28T00:00:00Z",
        evidence_generation=1,
        cryptographic_signature=None,
        fencing_challenge=None,
        lifecycle_generation=1,
        payload={
            "device_id": "dev1",
            "device_epoch": 1,
            "controller_epoch": 1,
            "physical_operation_id": "op1",
            "command_nonce": "nonce1"
        }
    )
    transport.publisher.publish(event)
    
    # 5. Prove that while REHYDRATING:
    # - reconciliation is not yet enabled for mutation (caught by StaleEpochError or blocked)
    # - telemetry cannot convert the operation to a terminal physical resolution
    # - capacity remains retained
    # - historical canonical identity remains unchanged
    daemon.run_reconciliation_cycle()
    
    assert daemon.stale_epoch_rejections == 1
    assert new_store.get_active_physical_operations() == 1
    
    ops = new_store.get_active_operations_snapshot()
    canon = ("dev1", 1, 1, "op1", "nonce1")
    assert canon in ops
    assert ops[canon]["resolution"] == "FAULTED_UNKNOWN"
    
    # 6. Finish recovery
    manager._admission_state = AdmissionState.READY

from holomed.persistence.exceptions import PersistenceEpochMismatchError

def test_adversarial_forged_historical_recovery_is_rejected(stores, temp_storage):
    """
    1. Device authority is D2.
    2. Operation historically belongs to D1.
    3. Caller supplies forged _is_historical_recovery=True.
    4. Attempt terminal physical resolution (COMPLETED).
    5. Operation is rejected.
    6. No journal mutation occurs, capacity unchanged.
    """
    c_auth, d_auth, session_store = stores
    session_store.start_session("s1", 1)
    session_store.record_operation_admitted(
        session_id="s1",
        device_id="dev1",
        device_epoch=1,
        controller_epoch=1,
        physical_operation_id="op1",
        command_nonce="nonce1",
        execution_id="test",
        endpoint_id="ep1",
        command_name="cmd1"
    )

    d_auth.allocate_next_device_epoch("dev1") # D2

    assert session_store.get_active_physical_operations() == 1

    with pytest.raises(PersistenceEpochMismatchError, match="Historical recovery requires FAULTED_UNKNOWN"):
        session_store.record_operation_terminated(
            session_id="s1",
            device_id="dev1",
            device_epoch=1,
            controller_epoch=1,
            physical_operation_id="op1",
            command_nonce="nonce1",
            resolution="COMPLETED",
            _is_historical_recovery=True
        )

    # Must fail closed. Capacity must remain retained.
    assert session_store.get_active_physical_operations() == 1

def test_correct_historical_recovery_classification_succeeds(stores, temp_storage):
    """
    1. Device authority is D2.
    2. Historical D1 operation.
    3. _is_historical_recovery=True is supplied.
    4. FAULTED_UNKNOWN recovery classification succeeds.
    5. Historical canonical identity remains D1/C1.
    6. Capacity remains retained.
    """
    c_auth, d_auth, session_store = stores
    session_store.start_session("s1", 1)
    session_store.record_operation_admitted(
        session_id="s1",
        device_id="dev1",
        device_epoch=1,
        controller_epoch=1,
        physical_operation_id="op1",
        command_nonce="nonce1",
        execution_id="test",
        endpoint_id="ep1",
        command_name="cmd1"
    )

    d_auth.allocate_next_device_epoch("dev1") # D2
    assert session_store.get_active_physical_operations() == 1

    session_store.record_operation_terminated(
        session_id="s1",
        device_id="dev1",
        device_epoch=1,
        controller_epoch=1,
        physical_operation_id="op1",
        command_nonce="nonce1",
        resolution="FAULTED_UNKNOWN",
        _is_historical_recovery=True
    )

    assert session_store.get_active_physical_operations() == 1
    
    ops = session_store.get_active_operations_snapshot()
    canon = ("dev1", 1, 1, "op1", "nonce1")
    assert canon in ops
    assert ops[canon]["resolution"] == "FAULTED_UNKNOWN"

def test_adversarial_historical_recovery_newer_than_authority_rejected(stores, temp_storage):
    """
    1. Device authority is D2.
    2. Operation historically belongs to D1.
    3. Forged D999 value supplied as device_epoch with _is_historical_recovery=True.
    4. Attempt terminal physical resolution or even FAULTED_UNKNOWN.
    5. Must fail closed.
    6. Capacity must remain retained.
    """
    c_auth, d_auth, session_store = stores
    session_store.start_session("s1", 1)
    session_store.record_operation_admitted(
        session_id="s1",
        device_id="dev1",
        device_epoch=1,
        controller_epoch=1,
        physical_operation_id="op1",
        command_nonce="nonce1",
        execution_id="test",
        endpoint_id="ep1",
        command_name="cmd1"
    )

    d_auth.allocate_next_device_epoch("dev1") # D2
    assert session_store.get_active_physical_operations() == 1

    with pytest.raises(PersistenceEpochMismatchError, match="cannot be strictly newer than authoritative"):
        session_store.record_operation_terminated(
            session_id="s1",
            device_id="dev1",
            device_epoch=999, # FORGED newer epoch
            controller_epoch=1,
            physical_operation_id="op1",
            command_nonce="nonce1",
            resolution="FAULTED_UNKNOWN",
            _is_historical_recovery=True
        )

    assert session_store.get_active_physical_operations() == 1
