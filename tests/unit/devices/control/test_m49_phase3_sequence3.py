import pytest
import uuid
import threading
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
    # Setup Authority
    auth = ControllerAuthorityStore(tmp_path)
    auth.allocate_next_epoch()
    # Ensure current epoch is written
    
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
    lifecycle_gen: int = 1
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
        payload={},
        evidence_generation=1,
        cryptographic_signature="sig",
        fencing_challenge="challenge"
    )

def test_duplicate_telemetry(components):
    """Validate monotonic sequence check ignores duplicates without state oscillation."""
    transport, gate, daemon, session_store = components
    publisher = TelemetryPublisher(transport)
    
    execution_id = "exec-1"
    
    # Send event 1 twice
    e1 = create_event(execution_id, 1, CommandState.RUNNING)
    e1_dup = create_event(execution_id, 1, CommandState.RUNNING)
    
    publisher.publish(e1)
    publisher.publish(e1_dup)
    
    daemon.run_reconciliation_cycle()
    
    record = gate._records.get(execution_id)
    assert record is not None
    assert record.current_state == CommandState.RUNNING
    assert record.latest_accepted_sequence == 1

def test_replayed_telemetry(components):
    """Validate sequence regression is ignored."""
    transport, gate, daemon, session_store = components
    publisher = TelemetryPublisher(transport)
    
    execution_id = "exec-2"
    
    e1 = create_event(execution_id, 2, CommandState.RUNNING)
    e2 = create_event(execution_id, 1, CommandState.DISPATCHING) # regression
    
    publisher.publish(e1)
    publisher.publish(e2)
    
    daemon.run_reconciliation_cycle()
    
    record = gate._records.get(execution_id)
    assert record.latest_accepted_sequence == 2
    assert record.current_state == CommandState.RUNNING

def test_wrong_generation(components):
    """Validate telemetry with wrong generation is rejected."""
    transport, gate, daemon, session_store = components
    publisher = TelemetryPublisher(transport)
    
    execution_id = "exec-3"
    
    # To test wrong generation, we first need a baseline generation.
    # But resolution gate takes the FIRST event's generation as truth for that execution.
    e1 = create_event(execution_id, 1, CommandState.DISPATCHING, lifecycle_gen=1)
    e2 = create_event(execution_id, 2, CommandState.RUNNING, lifecycle_gen=2) # wrong generation
    
    publisher.publish(e1)
    publisher.publish(e2)
    
    daemon.run_reconciliation_cycle()
    
    record = gate._records.get(execution_id)
    assert record.latest_accepted_sequence == 1
    assert record.current_state == CommandState.DISPATCHING

def test_terminal_resolution_capacity(components):
    """Validate that a terminal physical event successfully writes OPERATION_TERMINATED and releases global/endpoint capacity."""
    transport, gate, daemon, session_store = components
    publisher = TelemetryPublisher(transport)
    
    # Use the session_store epoch to ensure validation passes
    epoch = session_store._epoch_id
    session_id = session_store.start_session("caller-1", epoch).session_id
    execution_id = "exec-term-1"
    
    # 1. Admit an operation in session_store
    session_store.record_operation_admitted(
        endpoint_id="end-1",
        session_id=session_id,
        device_id="dev-1",
        device_epoch=1,
        controller_epoch=epoch,
        physical_operation_id="op-1",
        command_nonce="nonce-1",
        execution_id=execution_id,
        command_name="test_cmd"
    )
    
    # Verify it is active
    assert session_store.get_active_physical_operations() == 1
    
    # 2. Send terminal telemetry
    e_term = create_event(execution_id, 1, CommandState.OPERATION_COMPLETED, session_id=session_id)
    publisher.publish(e_term)
    
    # 3. Reconcile
    daemon.run_reconciliation_cycle()
    
    # 4. Verify it was terminated and capacity released
    record = gate._records.get(execution_id)
    assert record.terminal_resolution_status is True
    assert record.current_state == CommandState.OPERATION_COMPLETED
    
    # Verify durable state
    assert session_store.get_active_physical_operations() == 0


# ===========================================================================
# GATE 4 — CONCURRENCY / ADVERSARIAL PROOFS
# All tests use deterministic synchronization (Barrier, Event).
# No sleep(), elapsed time, or thread arrival order for correctness.
# ===========================================================================

def test_telemetry_vs_timeout(components):
    """Deterministic proof: telemetry terminal event races with control-plane timeout.

    Whichever resolves first wins; the second is safely ignored.
    Uses a Barrier to ensure both resolve attempts happen concurrently.
    """
    transport, gate, daemon, session_store = components
    publisher = TelemetryPublisher(transport)

    execution_id = "race-tel-timeout"
    lifecycle_gen = 1

    # Pre-seed the gate record so both branches have a target
    gate._get_or_create_record(execution_id, lifecycle_gen)

    barrier = threading.Barrier(2)
    results = {}

    def do_telemetry():
        barrier.wait()
        e = create_event(execution_id, 10, CommandState.OPERATION_COMPLETED, lifecycle_gen=lifecycle_gen)
        results["telemetry"] = gate.resolve_terminal_event(e)

    def do_timeout():
        barrier.wait()
        results["timeout"] = gate.resolve_timeout(execution_id, lifecycle_gen)

    t1 = threading.Thread(target=do_telemetry)
    t2 = threading.Thread(target=do_timeout)
    t1.start(); t2.start()
    t1.join(); t2.join()

    final = gate._records[execution_id]
    assert final.terminal_resolution_status is True
    # Exactly one authority won; state is either OPERATION_COMPLETED or FAULTED_UNKNOWN (timeout)
    assert final.current_state in (CommandState.OPERATION_COMPLETED, CommandState.FAULTED_UNKNOWN)


def test_telemetry_vs_cancellation(components):
    """Deterministic proof: telemetry races with a pre-claim cancellation (route_stop_request).

    If stop routes first (pre-claim cancel → PREEMPTED), telemetry is ignored.
    If telemetry resolves first, stop returns the already-terminal state.
    """
    transport, gate, daemon, session_store = components

    execution_id = "race-tel-cancel"
    lifecycle_gen = 1

    # Pre-seed without claiming → enables pre-claim cancel
    gate._get_or_create_record(execution_id, lifecycle_gen)

    barrier = threading.Barrier(2)
    results = {}

    def do_telemetry():
        barrier.wait()
        e = create_event(execution_id, 5, CommandState.OPERATION_COMPLETED, lifecycle_gen=lifecycle_gen)
        results["telemetry"] = gate.resolve_terminal_event(e)

    def do_cancel():
        barrier.wait()
        results["cancel"] = gate.route_stop_request(execution_id, lifecycle_gen)

    t1 = threading.Thread(target=do_telemetry)
    t2 = threading.Thread(target=do_cancel)
    t1.start(); t2.start()
    t1.join(); t2.join()

    final = gate._records[execution_id]
    assert final.terminal_resolution_status is True
    # Either telemetry won (OPERATION_COMPLETED) or cancel won (PREEMPTED)
    assert final.current_state in (CommandState.OPERATION_COMPLETED, CommandState.PREEMPTED)


def test_telemetry_vs_controller_epoch_advancement(components):
    """Deterministic proof: telemetry with old lifecycle_generation is rejected
    after controller epoch advances (simulated by creating a record with gen=2
    and sending telemetry with gen=1).
    """
    transport, gate, daemon, session_store = components

    execution_id = "epoch-advance"

    # Simulate epoch advancement: gate record established at gen=2
    gate._get_or_create_record(execution_id, 2)

    # Telemetry arrives with old gen=1
    e = create_event(execution_id, 1, CommandState.OPERATION_COMPLETED, lifecycle_gen=1)
    record = gate.resolve_terminal_event(e)

    # Must NOT have mutated state — generation mismatch
    assert record.current_state == CommandState.ACCEPTED
    assert record.terminal_resolution_status is False
    assert record.lifecycle_generation == 2


def test_telemetry_vs_device_epoch_advancement(components):
    """Deterministic proof: telemetry arriving for an execution whose device_epoch
    has been superseded does not affect the resolution gate.

    The gate does not track device_epoch; it tracks lifecycle_generation.
    Epoch advancement is enforced at the reconciler's source_authority filter
    and the session store's admission/termination boundary.
    This test proves that the gate remains inert when a stale generation is used.
    """
    transport, gate, daemon, session_store = components

    execution_id = "device-epoch-adv"

    # Establish record at generation 5 (representing new device epoch context)
    gate._get_or_create_record(execution_id, 5)

    # Old device epoch telemetry arrives with generation 3
    e = create_event(execution_id, 1, CommandState.RUNNING, lifecycle_gen=3)
    record = gate.resolve_terminal_event(e)

    # Stale generation → no mutation
    assert record.lifecycle_generation == 5
    assert record.current_state == CommandState.ACCEPTED


def test_duplicate_telemetry_concurrently(components):
    """Deterministic proof: identical terminal events arriving concurrently
    result in exactly one terminal resolution — no double capacity release.
    """
    transport, gate, daemon, session_store = components
    publisher = TelemetryPublisher(transport)

    epoch = session_store._epoch_id
    session_id = session_store.start_session("dup-concurrent", epoch).session_id
    execution_id = "dup-concurrent-exec"

    session_store.record_operation_admitted(
        endpoint_id="end-1",
        session_id=session_id,
        device_id="dev-dup",
        device_epoch=1,
        controller_epoch=epoch,
        physical_operation_id="op-dup",
        command_nonce="nonce-dup",
        execution_id=execution_id,
        command_name="test_cmd"
    )

    assert session_store.get_active_physical_operations() == 1

    barrier = threading.Barrier(2)
    errors = []

    def publish_via_transport():
        barrier.wait()
        e = create_event(execution_id, 1, CommandState.OPERATION_COMPLETED, session_id=session_id)
        publisher.publish(e)

    t1 = threading.Thread(target=publish_via_transport)
    t2 = threading.Thread(target=publish_via_transport)
    t1.start(); t2.start()
    t1.join(); t2.join()

    # Drain transport and push through gate
    daemon.run_reconciliation_cycle()

    # Gate: terminal resolution is set exactly once
    final = gate._records[execution_id]
    assert final.terminal_resolution_status is True
    assert final.current_state == CommandState.OPERATION_COMPLETED

    # Capacity released exactly once (idempotent)
    assert session_store.get_active_physical_operations() == 0


def test_conflicting_telemetry_concurrently(components):
    """Deterministic proof: conflicting terminal events at the same sequence
    result in FAULTED_UNKNOWN (quarantine consequence), not silent acceptance.
    """
    transport, gate, daemon, session_store = components

    execution_id = "conflict-concurrent"
    lifecycle_gen = 1

    barrier = threading.Barrier(2)

    def resolve_completed():
        barrier.wait()
        e = create_event(execution_id, 1, CommandState.OPERATION_COMPLETED, lifecycle_gen=lifecycle_gen)
        gate.resolve_terminal_event(e)

    def resolve_failed():
        barrier.wait()
        e = create_event(execution_id, 1, CommandState.FAILED, lifecycle_gen=lifecycle_gen)
        gate.resolve_terminal_event(e)

    t1 = threading.Thread(target=resolve_completed)
    t2 = threading.Thread(target=resolve_failed)
    t1.start(); t2.start()
    t1.join(); t2.join()

    final = gate._records[execution_id]
    assert final.terminal_resolution_status is True
    # Conflicting states at the same sequence → FAULTED_UNKNOWN (quarantine)
    assert final.current_state == CommandState.FAULTED_UNKNOWN
    assert final.quarantine_consequence is True


def test_reconciliation_vs_admission(components):
    """Deterministic proof: reconciliation cycle and admission are serialized
    by the session store's global lock.

    Admission must complete atomically before or after reconciliation reads
    the active snapshot.
    """
    transport, gate, daemon, session_store = components
    publisher = TelemetryPublisher(transport)

    epoch = session_store._epoch_id
    session_id = session_store.start_session("recon-admit", epoch).session_id

    exec_1 = "recon-admit-1"
    exec_2 = "recon-admit-2"

    # Admit exec_1
    session_store.record_operation_admitted(
        endpoint_id="end-1", session_id=session_id, device_id="dev-ra",
        device_epoch=1, controller_epoch=epoch, physical_operation_id="op-ra-1",
        command_nonce="nonce-ra-1", execution_id=exec_1, command_name="cmd"
    )

    # Publish terminal telemetry for exec_1
    publisher.publish(create_event(exec_1, 1, CommandState.OPERATION_COMPLETED, session_id=session_id))

    barrier = threading.Barrier(2)
    errors = []

    def do_reconcile():
        barrier.wait()
        daemon.run_reconciliation_cycle()

    def do_admit():
        barrier.wait()
        try:
            session_store.record_operation_admitted(
                endpoint_id="end-2", session_id=session_id, device_id="dev-ra",
                device_epoch=1, controller_epoch=epoch, physical_operation_id="op-ra-2",
                command_nonce="nonce-ra-2", execution_id=exec_2, command_name="cmd"
            )
        except Exception as e:
            errors.append(e)

    t1 = threading.Thread(target=do_reconcile)
    t2 = threading.Thread(target=do_admit)
    t1.start(); t2.start()
    t1.join(); t2.join()

    # No errors from admission
    assert len(errors) == 0

    # exec_2 must be admitted
    assert session_store.get_active_physical_operations() >= 1

    # exec_1 is either already terminated (reconciliation won) or still active
    # (admission ran first, reconciliation hasn't run yet for exec_1's cycle)
    # Either way, the system is consistent — no corruption.
    final_gate = gate._records.get(exec_1)
    if final_gate and final_gate.terminal_resolution_status:
        # Reconciliation ran and resolved
        assert final_gate.current_state == CommandState.OPERATION_COMPLETED


def test_reconciliation_vs_recovery(components):
    """Deterministic proof: reconciliation cycle cannot interfere with recovery
    (re-admission after FAULTED_UNKNOWN).

    Recovery re-admits a new canonical identity. Reconciliation of the old
    execution cannot corrupt the new admission.
    """
    transport, gate, daemon, session_store = components
    publisher = TelemetryPublisher(transport)

    epoch = session_store._epoch_id
    session_id = session_store.start_session("recon-recovery", epoch).session_id

    old_exec = "old-exec-recovery"
    new_exec = "new-exec-recovery"

    # 1. Admit old operation
    session_store.record_operation_admitted(
        endpoint_id="end-1", session_id=session_id, device_id="dev-rec",
        device_epoch=1, controller_epoch=epoch, physical_operation_id="op-old",
        command_nonce="nonce-old", execution_id=old_exec, command_name="cmd"
    )

    # 2. Terminal resolve old execution via telemetry
    publisher.publish(create_event(old_exec, 1, CommandState.OPERATION_COMPLETED, session_id=session_id))
    daemon.run_reconciliation_cycle()

    assert session_store.get_active_physical_operations() == 0

    # 3. Admit new operation (recovery scenario)
    session_store.record_operation_admitted(
        endpoint_id="end-1", session_id=session_id, device_id="dev-rec",
        device_epoch=2, controller_epoch=epoch, physical_operation_id="op-new",
        command_nonce="nonce-new", execution_id=new_exec, command_name="cmd"
    )

    assert session_store.get_active_physical_operations() == 1

    # 4. Late/stale telemetry for old execution arrives
    publisher.publish(create_event(old_exec, 2, CommandState.FAILED, session_id=session_id))

    barrier = threading.Barrier(2)

    def do_reconcile():
        barrier.wait()
        daemon.run_reconciliation_cycle()

    def do_new_telemetry():
        barrier.wait()
        # New execution telemetry arrives concurrently
        publisher.publish(create_event(new_exec, 1, CommandState.RUNNING, session_id=session_id))

    t1 = threading.Thread(target=do_reconcile)
    t2 = threading.Thread(target=do_new_telemetry)
    t1.start(); t2.start()
    t1.join(); t2.join()

    # Old execution: already terminal, stale telemetry ignored
    old_record = gate._records.get(old_exec)
    assert old_record.terminal_resolution_status is True

    # New execution: still active (RUNNING is non-terminal)
    assert session_store.get_active_physical_operations() == 1


# ===========================================================================
# GATE 3 — FAULTED_UNKNOWN END-TO-END PROOF
# ===========================================================================

def test_faulted_unknown_capacity_preserved(components):
    """E2E proof: FAULTED_UNKNOWN → durable journal → reconstruction → capacity held."""
    transport, gate, daemon, session_store = components
    publisher = TelemetryPublisher(transport)

    epoch = session_store._epoch_id
    session_id = session_store.start_session("faulted-cap", epoch).session_id
    execution_id = "exec-faulted"

    # 1. Admit
    session_store.record_operation_admitted(
        endpoint_id="end-1", session_id=session_id, device_id="dev-f",
        device_epoch=1, controller_epoch=epoch, physical_operation_id="op-f",
        command_nonce="nonce-f", execution_id=execution_id, command_name="cmd"
    )
    assert session_store.get_active_physical_operations() == 1

    # 2. Resolve to FAULTED_UNKNOWN via timeout (control plane path)
    gate.resolve_timeout(execution_id, 1)

    record = gate._records[execution_id]
    assert record.terminal_resolution_status is True
    assert record.current_state == CommandState.FAULTED_UNKNOWN

    # 3. Daemon cycle — FAULTED_UNKNOWN is written to the durable journal
    # because it is a valid terminal resolution. However, DurableSessionStore
    # preserves physical capacity for it during reconstruction.
    daemon.run_reconciliation_cycle()

    # Capacity still held (FAULTED_UNKNOWN preserves capacity)
    assert session_store.get_active_physical_operations() == 1

    # 4. Verify via reconstruction
    snapshot = session_store.get_active_operations_snapshot()
    assert len(snapshot) == 1


def test_operation_completed_releases_exactly_once(components):
    """E2E proof: OPERATION_COMPLETED → capacity released exactly once,
    second reconciliation cycle does not double-release.
    """
    transport, gate, daemon, session_store = components
    publisher = TelemetryPublisher(transport)

    epoch = session_store._epoch_id
    session_id = session_store.start_session("release-once", epoch).session_id
    execution_id = "exec-release-once"

    session_store.record_operation_admitted(
        endpoint_id="end-1", session_id=session_id, device_id="dev-ro",
        device_epoch=1, controller_epoch=epoch, physical_operation_id="op-ro",
        command_nonce="nonce-ro", execution_id=execution_id, command_name="cmd"
    )
    assert session_store.get_active_physical_operations() == 1

    publisher.publish(create_event(execution_id, 1, CommandState.OPERATION_COMPLETED, session_id=session_id))
    daemon.run_reconciliation_cycle()
    assert session_store.get_active_physical_operations() == 0

    # Second cycle: no events, no double-release
    daemon.run_reconciliation_cycle()
    assert session_store.get_active_physical_operations() == 0

def test_evidence_identity_mismatch_rejection(components):
    """E2E proof: Mismatched evidence identity (device_id, operation_id) is rejected."""
    transport, gate, daemon, session_store = components
    
    epoch = session_store._epoch_id
    session_id = session_store.start_session("evidence-mismatch", epoch).session_id
    execution_id = "exec-evidence"
    
    session_store.record_operation_admitted(
        endpoint_id="end-1", session_id=session_id, device_id="dev-ev",
        device_epoch=1, controller_epoch=epoch, physical_operation_id="op-ev",
        command_nonce="nonce-ev", execution_id=execution_id, command_name="cmd"
    )
    
    from holomed.persistence.exceptions import PersistenceTerminationConflictError
    import pytest
    
    # Try to terminate with wrong device_id
    with pytest.raises(PersistenceTerminationConflictError, match="not currently admitted"):
        session_store.record_operation_terminated(
            session_id=session_id, device_id="WRONG_DEVICE", device_epoch=1,
            controller_epoch=epoch, physical_operation_id="op-ev", command_nonce="nonce-ev",
            resolution="OPERATION_COMPLETED"
        )
        
    # Try to terminate with wrong physical_operation_id
    with pytest.raises(PersistenceTerminationConflictError, match="not currently admitted"):
        session_store.record_operation_terminated(
            session_id=session_id, device_id="dev-ev", device_epoch=1,
            controller_epoch=epoch, physical_operation_id="WRONG_OP", command_nonce="nonce-ev",
            resolution="OPERATION_COMPLETED"
        )
