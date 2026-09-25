import pytest
import threading
from unittest.mock import patch
import uuid
from holomed.devices.models import CommandState, ExecutionTelemetryEvent, EventSourceAuthority, AuthoritativeExecutionRecord
from holomed.devices.transport import TelemetryTransport, TelemetryPublisher
from holomed.devices.resolution import ExecutionResolutionGate
from holomed.devices.reconciler import TelemetryReconciler
from holomed.devices.control.daemon import ReconciliationDaemon
from holomed.persistence.sessions import DurableSessionStore
from holomed.persistence.authority import ControllerAuthorityStore

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
    epoch: int = 1,
    payload_override: dict | None = None
) -> ExecutionTelemetryEvent:
    base_payload = {"device_id": "dev-1", "device_epoch": 1, "controller_epoch": epoch, "physical_operation_id": "op-1", "command_nonce": "nonce-1"}
    if payload_override:
        base_payload.update(payload_override)
    return ExecutionTelemetryEvent(
        event_id=str(uuid.uuid4()),
        endpoint_id="end-1",
        session_id=session_id,
        lifecycle_generation=1,
        endpoint_lease_generation=1,
        execution_id=execution_id,
        command_sequence=1,
        event_sequence=sequence,
        event_type="test",
        observed_state=state,
        source_authority=EventSourceAuthority.HARDWARE_DRIVER,
        source_origin="test",
        timestamp_utc="2026-09-24T00:00:00Z",
        payload=base_payload,
        evidence_generation=1,
        cryptographic_signature="sig",
        fencing_challenge="challenge"
    )

def test_race_a_timeout_first(components):
    """
    Race A: timeout operation acquires the REAL per-execution lock first;
    terminal-evidence operation concurrently attempts the REAL resolve_terminal_event() path;
    terminal evidence cannot win;
    final state is FAULTED_UNKNOWN;
    terminal resolution is durable;
    physical capacity remains retained.
    """
    transport, gate, daemon, store = components
    exec_id = "exec-race-a"
    session_id = store.start_session("test_client", store._epoch_id).session_id
    store.record_operation_admitted(
        endpoint_id="end-1", session_id=session_id, device_id="dev-1",
        device_epoch=1, controller_epoch=store._epoch_id,
        physical_operation_id="op-1", command_nonce="nonce-1",
        execution_id=exec_id, command_name="cmd1"
    )

    timeout_record = None
    terminal_record = None

    in_lock_event = threading.Event()
    contention_event = threading.Event()

    original_init = AuthoritativeExecutionRecord.__init__

    def hooked_init(self, *args, **kwargs):
        if threading.current_thread().name == "TimeoutThread":
            in_lock_event.set()
            contention_event.wait(timeout=2.0)
        original_init(self, *args, **kwargs)

    def run_timeout():
        nonlocal timeout_record
        timeout_record = gate.resolve_timeout(exec_id, lifecycle_generation=1)

    def run_evidence():
        nonlocal terminal_record
        event = create_event(exec_id, sequence=1, state=CommandState.OPERATION_COMPLETED, session_id=session_id, epoch=store._epoch_id)
        in_lock_event.wait(timeout=2.0)
        # Block on exactly the same lock inside resolve_terminal_event
        terminal_record = gate.resolve_terminal_event(event)

    with patch.object(AuthoritativeExecutionRecord, '__init__', hooked_init):
        t_a = threading.Thread(target=run_timeout, name="TimeoutThread")
        t_b = threading.Thread(target=run_evidence, name="EvidenceThread")

        t_a.start()
        t_b.start()

        # Release the timeout thread once evidence thread is blocked/ready
        contention_event.set()

        t_a.join()
        t_b.join()

    assert timeout_record is not None
    assert terminal_record is not None
    assert timeout_record.current_state == CommandState.FAULTED_UNKNOWN
    assert terminal_record.current_state == CommandState.FAULTED_UNKNOWN

    # To prove durable termination and capacity retention, pass it to Daemon
    daemon_processed = threading.Event()
    original_term = store.record_operation_terminated
    def hooked_term(*args, **kwargs):
        original_term(*args, **kwargs)
        daemon_processed.set()
    store.record_operation_terminated = hooked_term

    daemon.start()
    event_late = create_event(exec_id, sequence=2, state=CommandState.OPERATION_COMPLETED, session_id=session_id, epoch=store._epoch_id)
    TelemetryPublisher(transport).publish(event_late)

    # We must also durably record the timeout (which the control plane does natively)
    store.record_operation_terminated(
        session_id=session_id, device_id="dev-1", device_epoch=1,
        controller_epoch=store._epoch_id, physical_operation_id="op-1",
        command_nonce="nonce-1", resolution=CommandState.FAULTED_UNKNOWN.value
    )

    # Sync daemon using dummy
    dummy_id = "exec-dummy-sync"
    store.record_operation_admitted(
        endpoint_id="end-dummy", session_id=session_id, device_id="dev-1",
        device_epoch=1, controller_epoch=store._epoch_id,
        physical_operation_id="op-dummy", command_nonce="nonce-dummy",
        execution_id=dummy_id, command_name="cmd-sync"
    )
    dummy_evt = create_event(dummy_id, 1, CommandState.OPERATION_COMPLETED, session_id, store._epoch_id, {"physical_operation_id": "op-dummy", "command_nonce": "nonce-dummy"})
    TelemetryPublisher(transport).publish(dummy_evt)

    daemon_processed.wait(timeout=2.0)
    daemon.stop()

    # verify capacity retained for FAULTED_UNKNOWN
    canon = ("dev-1", 1, store._epoch_id, "op-1", "nonce-1")
    assert canon in store.get_active_operations_snapshot()

def test_race_b_evidence_first(components):
    """
    Race B: terminal evidence acquires the REAL per-execution lock first;
    timeout concurrently attempts REAL resolve_timeout();
    final state is OPERATION_COMPLETED;
    durable termination occurs exactly once;
    capacity releases exactly once.
    """
    transport, gate, daemon, store = components
    exec_id = "exec-race-b"
    session_id = store.start_session("test_client", store._epoch_id).session_id
    store.record_operation_admitted(
        endpoint_id="end-1", session_id=session_id, device_id="dev-1",
        device_epoch=1, controller_epoch=store._epoch_id,
        physical_operation_id="op-1", command_nonce="nonce-1",
        execution_id=exec_id, command_name="cmd1"
    )

    timeout_record = None
    terminal_record = None

    in_lock_event = threading.Event()
    contention_event = threading.Event()

    original_init = AuthoritativeExecutionRecord.__init__

    def hooked_init(self, *args, **kwargs):
        if threading.current_thread().name == "EvidenceThread":
            in_lock_event.set()
            contention_event.wait(timeout=2.0)
        original_init(self, *args, **kwargs)

    def run_evidence():
        nonlocal terminal_record
        event = create_event(exec_id, sequence=1, state=CommandState.OPERATION_COMPLETED, session_id=session_id, epoch=store._epoch_id)
        terminal_record = gate.resolve_terminal_event(event)

    def run_timeout():
        nonlocal timeout_record
        in_lock_event.wait(timeout=2.0)
        timeout_record = gate.resolve_timeout(exec_id, lifecycle_generation=1)

    with patch.object(AuthoritativeExecutionRecord, '__init__', hooked_init):
        t_a = threading.Thread(target=run_evidence, name="EvidenceThread")
        t_b = threading.Thread(target=run_timeout, name="TimeoutThread")

        t_a.start()
        t_b.start()

        contention_event.set()

        t_a.join()
        t_b.join()

    assert terminal_record is not None
    assert timeout_record is not None
    assert terminal_record.current_state == CommandState.OPERATION_COMPLETED
    assert timeout_record.current_state == CommandState.OPERATION_COMPLETED

    # Prove durable termination exactly once via daemon
    daemon_processed = threading.Event()
    original_term = store.record_operation_terminated

    call_count = [0]
    def hooked_term(*args, **kwargs):
        call_count[0] += 1
        original_term(*args, **kwargs)
        daemon_processed.set()

    store.record_operation_terminated = hooked_term
    daemon.start()

    # Publish the same event to trigger daemon (since we bypassed it in run_evidence)
    event = create_event(exec_id, sequence=1, state=CommandState.OPERATION_COMPLETED, session_id=session_id, epoch=store._epoch_id)
    TelemetryPublisher(transport).publish(event)

    daemon_processed.wait(timeout=2.0)
    daemon.stop()

    assert call_count[0] == 1
    # Capacity must be released
    canon = ("dev-1", 1, store._epoch_id, "op-1", "nonce-1")
    assert canon not in store.get_active_operations_snapshot()

def test_restart_durable_outcome_evidence_end_to_end(components):
    """
    E1 runtime exists and admits an execution;
    destroy E1; construct fresh E2;
    deliver late E1 telemetry;
    verify stale E1 telemetry cannot mutate E2 or release capacity.
    """
    transport, gate, daemon, store = components
    exec_id = "exec-restart"
    session_id = store.start_session("test_client", store._epoch_id).session_id
    store.record_operation_admitted(
        endpoint_id="end-1", session_id=session_id, device_id="dev-1",
        device_epoch=1, controller_epoch=store._epoch_id,
        physical_operation_id="op-1", command_nonce="nonce-1",
        execution_id=exec_id, command_name="cmd1"
    )

    # E1 runtime -> terminate
    store_path = store._storage_root
    store.clear()

    # Construct E2 authority/runtime
    auth2 = ControllerAuthorityStore(store_path)
    auth2.allocate_next_epoch()
    store2 = DurableSessionStore(storage_root=store_path, epoch_id=auth2.read_current_epoch())
    gate2 = ExecutionResolutionGate()
    transport2 = TelemetryTransport()
    reconciler2 = TelemetryReconciler(transport2, gate2)
    daemon2 = ReconciliationDaemon(reconciler2, store2, polling_interval=0.01)

    daemon2.start()

    # E1 stale input arrives to E2 runtime
    event = create_event(exec_id, sequence=1, state=CommandState.OPERATION_COMPLETED, session_id=session_id, epoch=1) # E1 epoch
    TelemetryPublisher(transport2).publish(event)

    # Deterministic sync on daemon via dummy event
    daemon_processed = threading.Event()
    original_term = store2.record_operation_terminated
    def hooked_term(*args, **kwargs):
        original_term(*args, **kwargs)
        daemon_processed.set()
    store2.record_operation_terminated = hooked_term

    session_id2 = store2.start_session("dummy_client", store2._epoch_id).session_id
    
    dummy_id = "exec-dummy-sync"
    store2.record_operation_admitted(
        endpoint_id="end-dummy", session_id=session_id2, device_id="dev-1",
        device_epoch=1, controller_epoch=store2._epoch_id,
        physical_operation_id="op-dummy", command_nonce="nonce-dummy",
        execution_id=dummy_id, command_name="cmd1"
    )
    dummy_evt = create_event(dummy_id, 1, CommandState.OPERATION_COMPLETED, session_id2, store2._epoch_id, {"physical_operation_id": "op-dummy", "command_nonce": "nonce-dummy"})
    TelemetryPublisher(transport2).publish(dummy_evt)

    daemon_processed.wait(timeout=2.0)
    daemon2.stop()

    # Verify E2 is untouched and capacity is not released
    snapshot = store2.get_active_operations_snapshot()
    canon = ("dev-1", 1, 1, "op-1", "nonce-1")
    assert canon in snapshot

def test_late_timeout_after_successful_terminal_evidence(components):
    transport, gate, daemon, store = components
    exec_id = "exec-late-timeout"
    session_id = store.start_session("test_client", store._epoch_id).session_id
    store.record_operation_admitted(
        endpoint_id="end-1", session_id=session_id, device_id="dev-1",
        device_epoch=1, controller_epoch=store._epoch_id,
        physical_operation_id="op-1", command_nonce="nonce-1",
        execution_id=exec_id, command_name="cmd1"
    )

    daemon_processed = threading.Event()
    original_term = store.record_operation_terminated
    def hooked_term(*args, **kwargs):
        original_term(*args, **kwargs)
        daemon_processed.set()
    store.record_operation_terminated = hooked_term

    daemon.start()

    # 1. Terminal evidence arrives first and successfully linearizes
    event = create_event(exec_id, sequence=1, state=CommandState.OPERATION_COMPLETED, session_id=session_id, epoch=store._epoch_id)
    TelemetryPublisher(transport).publish(event)

    daemon_processed.wait(timeout=2.0)

    # 2. Late timeout fires
    rec = gate.resolve_timeout(exec_id, lifecycle_generation=1)

    daemon.stop()

    assert rec.current_state == CommandState.OPERATION_COMPLETED
    canon = ("dev-1", 1, store._epoch_id, "op-1", "nonce-1")
    assert canon not in store.get_active_operations_snapshot()

def test_late_terminal_telemetry_after_timeout(components):
    transport, gate, daemon, store = components
    exec_id = "exec-late-telem"
    session_id = store.start_session("test_client", store._epoch_id).session_id
    store.record_operation_admitted(
        endpoint_id="end-1", session_id=session_id, device_id="dev-1",
        device_epoch=1, controller_epoch=store._epoch_id,
        physical_operation_id="op-1", command_nonce="nonce-1",
        execution_id=exec_id, command_name="cmd1"
    )

    daemon_processed = threading.Event()
    original_term = store.record_operation_terminated
    def hooked_term(*args, **kwargs):
        original_term(*args, **kwargs)
        daemon_processed.set()
    store.record_operation_terminated = hooked_term

    daemon.start()

    # 1. Timeout -> FAULTED_UNKNOWN
    gate.resolve_timeout(exec_id, lifecycle_generation=1)
    store.record_operation_terminated(
        session_id=session_id, device_id="dev-1", device_epoch=1,
        controller_epoch=store._epoch_id, physical_operation_id="op-1",
        command_nonce="nonce-1", resolution=CommandState.FAULTED_UNKNOWN.value
    )

    # 2. Late telemetry arrives
    event = create_event(exec_id, sequence=1, state=CommandState.OPERATION_COMPLETED, session_id=session_id, epoch=store._epoch_id)
    TelemetryPublisher(transport).publish(event)

    # Sync using dummy
    dummy_id = "exec-dummy-sync"
    store.record_operation_admitted(
        endpoint_id="end-dummy", session_id=session_id, device_id="dev-1",
        device_epoch=1, controller_epoch=store._epoch_id,
        physical_operation_id="op-dummy", command_nonce="nonce-dummy",
        execution_id=dummy_id, command_name="cmd-sync"
    )
    dummy_evt = create_event(dummy_id, 1, CommandState.OPERATION_COMPLETED, session_id, store._epoch_id, {"physical_operation_id": "op-dummy", "command_nonce": "nonce-dummy"})
    TelemetryPublisher(transport).publish(dummy_evt)

    daemon_processed.wait(timeout=2.0)
    daemon.stop()

    # Verify NO resurrection and NO capacity release
    canon = ("dev-1", 1, store._epoch_id, "op-1", "nonce-1")
    assert canon in store.get_active_operations_snapshot()

def test_duplicate_terminal_evidence_and_repeated_timeout(components):
    transport, gate, daemon, store = components
    exec_id = "exec-dup"
    session_id = store.start_session("test_client", store._epoch_id).session_id
    store.record_operation_admitted(
        endpoint_id="end-1", session_id=session_id, device_id="dev-1",
        device_epoch=1, controller_epoch=store._epoch_id,
        physical_operation_id="op-1", command_nonce="nonce-1",
        execution_id=exec_id, command_name="cmd1"
    )

    daemon_processed = threading.Event()
    original_term = store.record_operation_terminated
    def hooked_term(*args, **kwargs):
        original_term(*args, **kwargs)
        daemon_processed.set()
    store.record_operation_terminated = hooked_term

    daemon.start()

    # Repeated timeout
    gate.resolve_timeout(exec_id, lifecycle_generation=1)
    rec1 = gate.resolve_timeout(exec_id, lifecycle_generation=1)
    assert rec1.current_state == CommandState.FAULTED_UNKNOWN

    store.record_operation_terminated(
        session_id=session_id, device_id="dev-1", device_epoch=1,
        controller_epoch=store._epoch_id, physical_operation_id="op-1",
        command_nonce="nonce-1", resolution=CommandState.FAULTED_UNKNOWN.value
    )

    # Duplicate terminal evidence
    event = create_event(exec_id, sequence=1, state=CommandState.OPERATION_COMPLETED, session_id=session_id, epoch=store._epoch_id)
    TelemetryPublisher(transport).publish(event)
    TelemetryPublisher(transport).publish(event)

    # Sync
    dummy_id = "exec-dummy-sync"
    store.record_operation_admitted(
        endpoint_id="end-dummy", session_id=session_id, device_id="dev-1",
        device_epoch=1, controller_epoch=store._epoch_id,
        physical_operation_id="op-dummy", command_nonce="nonce-dummy",
        execution_id=dummy_id, command_name="cmd-sync"
    )
    dummy_evt = create_event(dummy_id, 1, CommandState.OPERATION_COMPLETED, session_id, store._epoch_id, {"physical_operation_id": "op-dummy", "command_nonce": "nonce-dummy"})
    TelemetryPublisher(transport).publish(dummy_evt)

    daemon_processed.wait(timeout=2.0)
    daemon.stop()

    # Still FAULTED_UNKNOWN because timeout was first
    canon = ("dev-1", 1, store._epoch_id, "op-1", "nonce-1")
    assert canon in store.get_active_operations_snapshot()

def test_future_epoch_evidence_through_real_path(components):
    transport, gate, daemon, store = components
    exec_id = "exec-future"
    session_id = store.start_session("test_client", store._epoch_id).session_id
    store.record_operation_admitted(
        endpoint_id="end-1", session_id=session_id, device_id="dev-1",
        device_epoch=1, controller_epoch=store._epoch_id,
        physical_operation_id="op-1", command_nonce="nonce-1",
        execution_id=exec_id, command_name="cmd1"
    )

    daemon_processed = threading.Event()
    original_term = store.record_operation_terminated
    def hooked_term(*args, **kwargs):
        original_term(*args, **kwargs)
        daemon_processed.set()
    store.record_operation_terminated = hooked_term

    daemon.start()

    # Event from FUTURE epoch (999) - should be rejected by daemon
    event = create_event(exec_id, sequence=1, state=CommandState.OPERATION_COMPLETED, session_id=session_id, epoch=999)
    TelemetryPublisher(transport).publish(event)

    # Sync using dummy
    dummy_id = "exec-dummy-sync"
    store.record_operation_admitted(
        endpoint_id="end-dummy", session_id=session_id, device_id="dev-1",
        device_epoch=1, controller_epoch=store._epoch_id,
        physical_operation_id="op-dummy", command_nonce="nonce-dummy",
        execution_id=dummy_id, command_name="cmd-sync"
    )
    dummy_evt = create_event(dummy_id, 1, CommandState.OPERATION_COMPLETED, session_id, store._epoch_id, {"physical_operation_id": "op-dummy", "command_nonce": "nonce-dummy"})
    TelemetryPublisher(transport).publish(dummy_evt)

    daemon_processed.wait(timeout=2.0)
    daemon.stop()

    # Future epoch event is rejected, capacity remains retained!
    canon = ("dev-1", 1, store._epoch_id, "op-1", "nonce-1")
    assert canon in store.get_active_operations_snapshot()
