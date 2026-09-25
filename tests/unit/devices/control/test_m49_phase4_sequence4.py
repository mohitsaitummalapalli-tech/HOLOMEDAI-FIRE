import pytest
import uuid
import threading
import time
from typing import Optional

from holomed.devices.reconciler import TelemetryReconciler
from holomed.devices.resolution import ExecutionResolutionGate
from holomed.devices.transport import TelemetryTransport, TelemetryPublisher
from holomed.devices.control.daemon import ReconciliationDaemon
from holomed.persistence.sessions import DurableSessionStore
from holomed.persistence.authority import ControllerAuthorityStore
from holomed.devices.models import (
    ExecutionTelemetryEvent,
    CommandState,
    EventSourceAuthority
)

@pytest.fixture
def session_store(tmp_path):
    auth = ControllerAuthorityStore(tmp_path)
    auth.allocate_next_epoch()
    store = DurableSessionStore(storage_root=tmp_path, epoch_id=auth.read_current_epoch())
    yield store
    store.clear()

@pytest.fixture
def components(session_store):
    gate = ExecutionResolutionGate()
    transport = TelemetryTransport()
    reconciler = TelemetryReconciler(transport, gate)
    daemon = ReconciliationDaemon(reconciler, session_store, polling_interval=0.01)
    return transport, gate, daemon, session_store

def create_event(
    execution_id: str,
    sequence: int,
    state: CommandState,
    session_id: str = "sess-1",
    lifecycle_gen: int = 1,
    epoch: int = 1
) -> ExecutionTelemetryEvent:
    return ExecutionTelemetryEvent(
        event_id=str(uuid.uuid4()),
        endpoint_id="end-1",
        session_id=session_id,
        lifecycle_generation=lifecycle_gen,
        endpoint_lease_generation=1,
        execution_id=execution_id,
        command_sequence=1,
        event_sequence=sequence,
        event_type="test",
        observed_state=state,
        source_authority=EventSourceAuthority.HARDWARE_DRIVER,
        source_origin="test",
        timestamp_utc="2026-09-24T00:00:00Z",
        payload={"device_id": "dev-1", "device_epoch": 1, "controller_epoch": epoch, "physical_operation_id": "op-1", "command_nonce": "nonce-1"},
        evidence_generation=1,
        cryptographic_signature="sig",
        fencing_challenge="challenge"
    )

def test_race_a_timeout_first(components):
    transport, gate, daemon, store = components
    daemon.start()
    
    exec_id = "exec-race-a"
    session_id = store.start_session("test_client", store._epoch_id).session_id
    store.record_operation_admitted(
        endpoint_id="end-1", session_id=session_id, device_id="dev-1",
        device_epoch=1, controller_epoch=store._epoch_id,
        physical_operation_id="op-1", command_nonce="nonce-1",
        execution_id=exec_id, command_name="cmd1"
    )
    
    assert store.get_active_physical_operations() == 1
    
    # 1. Timeout fires FIRST
    gate.resolve_timeout(exec_id, lifecycle_generation=1)
    
    # 2. Terminal evidence arrives LATER
    event = create_event(exec_id, sequence=1, state=CommandState.OPERATION_COMPLETED, session_id=session_id, epoch=store._epoch_id)
    publisher = TelemetryPublisher(transport)
    publisher.publish(event)
    
    # Wait for daemon
    time.sleep(0.1)
    daemon.stop()
    
    # Capacity must NOT be released because timeout won and state is FAULTED_UNKNOWN
    assert store.get_active_physical_operations() == 1

def test_race_b_terminal_first(components):
    transport, gate, daemon, store = components
    daemon.start()
    
    exec_id = "exec-race-b"
    session_id = store.start_session("test_client", store._epoch_id).session_id
    store.record_operation_admitted(
        endpoint_id="end-1", session_id=session_id, device_id="dev-1",
        device_epoch=1, controller_epoch=store._epoch_id,
        physical_operation_id="op-1", command_nonce="nonce-1",
        execution_id=exec_id, command_name="cmd1"
    )
    
    assert store.get_active_physical_operations() == 1
    
    # 1. Terminal evidence arrives FIRST
    event = create_event(exec_id, sequence=1, state=CommandState.OPERATION_COMPLETED, session_id=session_id, epoch=store._epoch_id)
    publisher = TelemetryPublisher(transport)
    publisher.publish(event)
    
    time.sleep(0.1) # allow daemon to process
    
    # 2. Timeout fires LATER
    gate.resolve_timeout(exec_id, lifecycle_generation=1)
    
    daemon.stop()
    
    # Capacity MUST be released exactly once
    assert store.get_active_physical_operations() == 0

def test_late_timeout(components):
    transport, gate, daemon, store = components
    daemon.start()
    exec_id = "exec-late-timeout"
    session_id = store.start_session("test_client", store._epoch_id).session_id
    store.record_operation_admitted(
        endpoint_id="end-1", session_id=session_id, device_id="dev-1",
        device_epoch=1, controller_epoch=store._epoch_id,
        physical_operation_id="op-1", command_nonce="nonce-1",
        execution_id=exec_id, command_name="cmd1"
    )
    
    event = create_event(exec_id, sequence=1, state=CommandState.OPERATION_COMPLETED, session_id=session_id, epoch=store._epoch_id)
    publisher = TelemetryPublisher(transport)
    publisher.publish(event)
    
    time.sleep(0.1)
    
    record = gate.resolve_timeout(exec_id, lifecycle_generation=1)
    daemon.stop()
    
    assert record.current_state == CommandState.OPERATION_COMPLETED
    assert store.get_active_physical_operations() == 0

def test_late_telemetry_no_resurrection(components):
    transport, gate, daemon, store = components
    daemon.start()
    exec_id = "exec-late-telem"
    session_id = store.start_session("test_client", store._epoch_id).session_id
    store.record_operation_admitted(
        endpoint_id="end-1", session_id=session_id, device_id="dev-1",
        device_epoch=1, controller_epoch=store._epoch_id,
        physical_operation_id="op-1", command_nonce="nonce-1",
        execution_id=exec_id, command_name="cmd1"
    )
    
    record = gate.resolve_timeout(exec_id, lifecycle_generation=1)
    assert record.current_state == CommandState.FAULTED_UNKNOWN
    
    event = create_event(exec_id, sequence=1, state=CommandState.OPERATION_COMPLETED, session_id=session_id, epoch=store._epoch_id)
    publisher = TelemetryPublisher(transport)
    publisher.publish(event)
    
    time.sleep(0.1)
    daemon.stop()
    
    assert gate._records[exec_id].current_state == CommandState.FAULTED_UNKNOWN
    assert store.get_active_physical_operations() == 1

def test_epoch_fencing_behavior(components):
    transport, gate, daemon, store = components
    daemon.start()
    exec_id = "exec-epoch-fence"
    session_id = store.start_session("test_client", store._epoch_id).session_id
    store.record_operation_admitted(
        endpoint_id="end-1", session_id=session_id, device_id="dev-1",
        device_epoch=1, controller_epoch=store._epoch_id,
        physical_operation_id="op-1", command_nonce="nonce-1",
        execution_id=exec_id, command_name="cmd1"
    )
    
    # 1. Telemetry from old epoch is rejected
    event = create_event(exec_id, sequence=1, state=CommandState.OPERATION_COMPLETED, session_id=session_id, epoch=999)
    publisher = TelemetryPublisher(transport)
    publisher.publish(event)
    
    time.sleep(0.1)
    
    # Gate may resolve it, but daemon will reject it and NOT release capacity
    assert gate._records[exec_id].terminal_resolution_status is True
    assert store.get_active_physical_operations() == 1
    
    # 2. Timeout resolves it securely
    gate.resolve_timeout(exec_id, lifecycle_generation=1)
    daemon.stop()
    assert store.get_active_physical_operations() == 1

def test_idempotency_behavior(components):
    transport, gate, daemon, store = components
    daemon.start()
    exec_id = "exec-idem"
    session_id = store.start_session("test_client", store._epoch_id).session_id
    store.record_operation_admitted(
        endpoint_id="end-1", session_id=session_id, device_id="dev-1",
        device_epoch=1, controller_epoch=store._epoch_id,
        physical_operation_id="op-1", command_nonce="nonce-1",
        execution_id=exec_id, command_name="cmd1"
    )
    
    event = create_event(exec_id, sequence=1, state=CommandState.OPERATION_COMPLETED, session_id=session_id, epoch=store._epoch_id)
    publisher = TelemetryPublisher(transport)
    publisher.publish(event)
    publisher.publish(event) # duplicate
    
    time.sleep(0.1)
    
    # Timeout fires but it's idempotent/rejected
    gate.resolve_timeout(exec_id, lifecycle_generation=1)
    daemon.stop()
    
    assert gate._records[exec_id].current_state == CommandState.OPERATION_COMPLETED
    assert store.get_active_physical_operations() == 0


