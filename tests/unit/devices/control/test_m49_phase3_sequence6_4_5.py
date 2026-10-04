import pytest
import os
from pathlib import Path
from unittest.mock import Mock

from holomed.persistence.sessions import DurableSessionStore
from holomed.persistence.authority import ControllerAuthorityStore, DeviceEpochAuthority
from holomed.devices.control.daemon import ReconciliationDaemon
from holomed.devices.reconciler import TelemetryReconciler
from holomed.devices.transport import TelemetryTransport
from holomed.devices.resolution import ExecutionResolutionGate
from holomed.devices.control.recovery import StateRehydrationEngine
from holomed.devices.models import ExecutionTelemetryEvent, CommandState, EventSourceAuthority
from holomed.devices.control.exceptions import StaleEpochError

@pytest.fixture
def temp_storage(tmp_path):
    root = tmp_path / "persistence_test_g13"
    root.mkdir(parents=True, exist_ok=True)
    return root

@pytest.fixture
def setup_environment(temp_storage):
    c_auth = ControllerAuthorityStore(temp_storage)
    c_auth.allocate_next_epoch() # C=1
    
    dev_auth = DeviceEpochAuthority(temp_storage / "devices")
    dev_auth.allocate_next_device_epoch("dev1") # D=1
    
    store = DurableSessionStore(temp_storage, epoch_id=1)
    store.start_session("s1", 1)
    
    store.record_operation_admitted(
        session_id="s1",
        endpoint_id="ep1",
        device_id="dev1",
        device_epoch=1,
        controller_epoch=1,
        physical_operation_id="op1",
        command_nonce="nonce1", correlation_id="nonce1",
        execution_id="test_exec",
        command_name="cmd1"
    )
    
    return temp_storage, c_auth, dev_auth

def publish_event_and_reconcile(daemon, transport, device_epoch, controller_epoch):
    event = ExecutionTelemetryEvent(
        event_id="evt1",
        endpoint_id="ep1",
        session_id="s1",
        endpoint_lease_generation=1,
        execution_id="test_exec",
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
            "device_epoch": device_epoch,
            "controller_epoch": controller_epoch,
            "physical_operation_id": "op1",
            "command_nonce": "nonce1"
        }
    )
    transport.publisher.publish(event)
    daemon.run_reconciliation_cycle()

def test_stale_controller_telemetry_cannot_release_capacity(setup_environment):
    temp_storage, c_auth, dev_auth = setup_environment
    
    # Rollover controller epoch
    c_auth.allocate_next_epoch() # C=2
    
    store_new = DurableSessionStore(temp_storage, epoch_id=2)
    transport = TelemetryTransport()
    resolution_gate = ExecutionResolutionGate()
    reconciler = TelemetryReconciler(transport, resolution_gate)
    daemon = ReconciliationDaemon(reconciler, store_new)
    
    assert store_new.get_active_physical_operations() == 1
    
    # D=1, C=1 telemetry (Stale C)
    publish_event_and_reconcile(daemon, transport, 1, 1)
    
    assert daemon.stale_epoch_rejections == 1
    assert store_new.get_active_physical_operations() == 1

def test_stale_device_telemetry_cannot_release_capacity(setup_environment):
    temp_storage, c_auth, dev_auth = setup_environment
    
    # Rollover device epoch
    dev_auth.allocate_next_device_epoch("dev1") # D=2
    
    store = DurableSessionStore(temp_storage, epoch_id=1)
    transport = TelemetryTransport()
    resolution_gate = ExecutionResolutionGate()
    reconciler = TelemetryReconciler(transport, resolution_gate)
    daemon = ReconciliationDaemon(reconciler, store)
    
    assert store.get_active_physical_operations() == 1
    
    # D=1, C=1 telemetry (Stale D)
    publish_event_and_reconcile(daemon, transport, 1, 1)
    
    assert daemon.stale_epoch_rejections == 1
    assert store.get_active_physical_operations() == 1

def test_simultaneous_stale_telemetry_rejection(setup_environment):
    temp_storage, c_auth, dev_auth = setup_environment
    
    c_auth.allocate_next_epoch() # C=2
    dev_auth.allocate_next_device_epoch("dev1") # D=2
    
    store_new = DurableSessionStore(temp_storage, epoch_id=2)
    transport = TelemetryTransport()
    resolution_gate = ExecutionResolutionGate()
    reconciler = TelemetryReconciler(transport, resolution_gate)
    daemon = ReconciliationDaemon(reconciler, store_new)
    
    assert store_new.get_active_physical_operations() == 1
    
    # D=1, C=1 telemetry (Stale C and D)
    publish_event_and_reconcile(daemon, transport, 1, 1)
    
    assert daemon.stale_epoch_rejections == 1
    assert store_new.get_active_physical_operations() == 1

def test_m49_seq64_r21_ctrl_restart_only(setup_environment):
    temp_storage, c_auth, dev_auth = setup_environment
    
    c_auth.allocate_next_epoch() # C=2
    store_new = DurableSessionStore(temp_storage, epoch_id=2)
    engine = StateRehydrationEngine(store_new, Mock(), c_auth)
    engine.rehydrate_controller_state("s2")
    
    transport = TelemetryTransport()
    resolution_gate = ExecutionResolutionGate()
    reconciler = TelemetryReconciler(transport, resolution_gate)
    daemon = ReconciliationDaemon(reconciler, store_new)
    
    # Active snapshot should show FAULTED_UNKNOWN
    ops = store_new.get_active_operations_snapshot()
    canon = ("dev1", 1, 1, "op1", "nonce1")
    assert canon in ops
    assert ops[canon]["resolution"] == "FAULTED_UNKNOWN"
    
    # Inject C1 telemetry
    publish_event_and_reconcile(daemon, transport, 1, 1)
    
    # State should remain FAULTED_UNKNOWN
    ops = store_new.get_active_operations_snapshot()
    assert ops[canon]["resolution"] == "FAULTED_UNKNOWN"
    assert daemon.stale_epoch_rejections == 1

def test_m49_seq64_r22_device_restart_only(setup_environment):
    temp_storage, c_auth, dev_auth = setup_environment
    
    dev_auth.allocate_next_device_epoch("dev1") # D=2
    store = DurableSessionStore(temp_storage, epoch_id=1)
    
    engine = StateRehydrationEngine(store, Mock(), c_auth)
    engine.rehydrate_device_state("s2", "dev1")
    
    transport = TelemetryTransport()
    resolution_gate = ExecutionResolutionGate()
    reconciler = TelemetryReconciler(transport, resolution_gate)
    daemon = ReconciliationDaemon(reconciler, store)
    
    ops = store.get_active_operations_snapshot()
    canon = ("dev1", 1, 1, "op1", "nonce1")
    assert canon in ops
    assert ops[canon]["resolution"] == "FAULTED_UNKNOWN"
    
    # Inject D1 telemetry
    publish_event_and_reconcile(daemon, transport, 1, 1)
    
    ops = store.get_active_operations_snapshot()
    assert ops[canon]["resolution"] == "FAULTED_UNKNOWN"
    assert daemon.stale_epoch_rejections == 1

def test_m49_seq64_r23_simultaneous_restart(setup_environment):
    temp_storage, c_auth, dev_auth = setup_environment
    
    c_auth.allocate_next_epoch() # C=2
    dev_auth.allocate_next_device_epoch("dev1") # D=2
    
    store_new = DurableSessionStore(temp_storage, epoch_id=2)
    engine = StateRehydrationEngine(store_new, Mock(), c_auth)
    engine.rehydrate_controller_state("s2")
    
    transport = TelemetryTransport()
    resolution_gate = ExecutionResolutionGate()
    reconciler = TelemetryReconciler(transport, resolution_gate)
    daemon = ReconciliationDaemon(reconciler, store_new)
    
    ops = store_new.get_active_operations_snapshot()
    canon = ("dev1", 1, 1, "op1", "nonce1")
    assert canon in ops
    assert ops[canon]["resolution"] == "FAULTED_UNKNOWN"
    
    # Inject D1, C1 telemetry
    publish_event_and_reconcile(daemon, transport, 1, 1)
    
    ops = store_new.get_active_operations_snapshot()
    assert ops[canon]["resolution"] == "FAULTED_UNKNOWN"
    assert daemon.stale_epoch_rejections == 1

def test_future_epochs_fail_closed(setup_environment):
    temp_storage, c_auth, dev_auth = setup_environment
    
    store = DurableSessionStore(temp_storage, epoch_id=1)
    transport = TelemetryTransport()
    resolution_gate = ExecutionResolutionGate()
    reconciler = TelemetryReconciler(transport, resolution_gate)
    daemon = ReconciliationDaemon(reconciler, store)
    
    # Forged C2
    publish_event_and_reconcile(daemon, transport, 1, 2)
    assert daemon.stale_epoch_rejections == 0
    assert store.get_active_physical_operations() == 1
    
    # Forged D2
    publish_event_and_reconcile(daemon, transport, 2, 1)
    assert daemon.stale_epoch_rejections == 0
    assert store.get_active_physical_operations() == 1

