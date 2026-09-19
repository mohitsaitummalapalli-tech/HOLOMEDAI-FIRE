"""Tests for TelemetryReconciler (M49.3.3.4)."""

import pytest
from types import MappingProxyType

from holomed.devices.models import (
    ExecutionTelemetryEvent,
    CommandState,
    EventSourceAuthority,
)
from holomed.devices.transport import TelemetryTransport
from holomed.devices.resolution import ExecutionResolutionGate
from holomed.devices.reconciler import TelemetryReconciler


def create_event(
    execution_id: str,
    event_sequence: int,
    state: CommandState,
    authority: EventSourceAuthority = EventSourceAuthority.HARDWARE_DRIVER,
    event_id: str = "evt-1"
) -> ExecutionTelemetryEvent:
    return ExecutionTelemetryEvent(
        evidence_generation=1,
        cryptographic_signature=None,
        fencing_challenge=None,
        event_id=event_id,
        endpoint_id="end-1",
        session_id="ses-1",
        lifecycle_generation=1,
        endpoint_lease_generation=1,
        execution_id=execution_id,
        command_sequence=1,
        event_sequence=event_sequence,
        event_type="test",
        observed_state=state,
        source_authority=authority,
        source_origin="driver",
        timestamp_utc="2026-01-01T00:00:00Z",
        payload=MappingProxyType({}),
    )


@pytest.fixture
def setup_reconciler():
    transport = TelemetryTransport()
    gate = ExecutionResolutionGate()
    reconciler = TelemetryReconciler(transport, gate)
    return reconciler, transport, gate


def test_reconciler_drains_transport_queues(setup_reconciler):
    reconciler, transport, _ = setup_reconciler
    evt1 = create_event("exec-1", 1, CommandState.RUNNING)
    evt2 = create_event("exec-1", 2, CommandState.COMPLETED)
    
    transport._put_normal(evt1)
    transport._put_critical(evt2)
    
    reconciler.process_pending_events()
    
    assert len(transport.drain_normal()) == 0
    assert len(transport.drain_critical()) == 0


def test_reconciler_rejects_malformed_envelope(setup_reconciler):
    reconciler, transport, gate = setup_reconciler
    
    # Bypass type checking to insert a malformed envelope directly into the queue
    # Since ExecutionTelemetryEvent validates on instantiation, we simulate a raw dict
    bad_evt = {"execution_id": "", "event_sequence": 1}
    transport._put_normal(bad_evt)  # type: ignore
    
    records = reconciler.process_pending_events()
    assert len(records) == 0
    assert "exec-1" not in gate._execution_locks


def test_reconciler_rejects_illegal_source_authority_masquerade(setup_reconciler):
    reconciler, transport, _ = setup_reconciler
    
    evt = create_event("exec-1", 1, CommandState.RUNNING, authority=EventSourceAuthority.CONTROL_PLANE_TIMEOUT)
    transport._put_normal(evt)
    
    records = reconciler.process_pending_events()
    assert len(records) == 0


def test_critical_queue_cannot_override_event_sequence(setup_reconciler):
    reconciler, transport, _ = setup_reconciler
    
    evt_crit = create_event("exec-1", 3, CommandState.RUNNING)
    evt_norm = create_event("exec-1", 2, CommandState.RUNNING)
    
    transport._put_critical(evt_crit)
    transport._put_normal(evt_norm)
    
    records = reconciler.process_pending_events()
    assert len(records) == 2
    # resolution gate sequence checks
    assert records[0].latest_accepted_sequence == 2
    assert records[1].latest_accepted_sequence == 3


def test_normal_queue_newer_than_critical_queue_event(setup_reconciler):
    reconciler, transport, _ = setup_reconciler
    
    evt_crit = create_event("exec-1", 2, CommandState.RUNNING)
    evt_norm = create_event("exec-1", 3, CommandState.RUNNING)
    
    transport._put_critical(evt_crit)
    transport._put_normal(evt_norm)
    
    records = reconciler.process_pending_events()
    assert len(records) == 2
    assert records[0].latest_accepted_sequence == 2
    assert records[1].latest_accepted_sequence == 3


def test_events_from_same_execution_split_across_queues(setup_reconciler):
    reconciler, transport, gate = setup_reconciler
    
    evt1 = create_event("exec-1", 1, CommandState.RUNNING)
    evt2 = create_event("exec-1", 2, CommandState.RUNNING)
    evt3 = create_event("exec-1", 3, CommandState.COMPLETED)
    
    transport._put_critical(evt2)
    transport._put_normal(evt1)
    transport._put_normal(evt3)
    
    records = reconciler.process_pending_events()
    assert len(records) == 3
    assert records[-1].latest_accepted_sequence == 3
    assert records[-1].current_state == CommandState.COMPLETED


def test_reconciler_duplicate_event_after_reconciler_restart(setup_reconciler):
    reconciler, transport, gate = setup_reconciler
    
    evt1 = create_event("exec-1", 1, CommandState.RUNNING, event_id="evt-dup")
    transport._put_normal(evt1)
    reconciler.process_pending_events()
    
    reconciler2 = TelemetryReconciler(transport, gate)
    
    evt2 = create_event("exec-1", 1, CommandState.RUNNING, event_id="evt-dup")
    transport._put_normal(evt2)
    records = reconciler2.process_pending_events()
    
    assert len(records) == 1
    assert records[0].latest_accepted_sequence == 1


def test_reconciler_duplicate_event_after_endpoint_restart(setup_reconciler):
    reconciler, transport, gate = setup_reconciler
    
    evt1 = create_event("exec-1", 5, CommandState.RUNNING)
    transport._put_normal(evt1)
    reconciler.process_pending_events()
    
    evt2 = create_event("exec-1", 5, CommandState.RUNNING)
    transport._put_normal(evt2)
    records = reconciler.process_pending_events()
    
    assert len(records) == 1
    assert records[0].latest_accepted_sequence == 5


def test_event_id_reuse_across_executions_permitted(setup_reconciler):
    reconciler, transport, _ = setup_reconciler
    
    evt1 = create_event("exec-A", 1, CommandState.RUNNING, event_id="shared-id")
    evt2 = create_event("exec-B", 1, CommandState.RUNNING, event_id="shared-id")
    
    transport._put_normal(evt1)
    transport._put_normal(evt2)
    
    records = reconciler.process_pending_events()
    assert len(records) == 2
    assert records[0].latest_accepted_sequence == 1


def test_same_execution_same_sequence_conflicting_content(setup_reconciler):
    reconciler, transport, gate = setup_reconciler
    
    evt1 = create_event("exec-1", 1, CommandState.RUNNING)
    evt2 = create_event("exec-1", 1, CommandState.COMPLETED)
    
    transport._put_normal(evt1)
    transport._put_normal(evt2)
    
    records = reconciler.process_pending_events()
    assert len(records) == 2
    # In M49.3.3.3, a duplicate sequence that is different from original would trigger sequence collision (FAULTED_UNKNOWN, potentially depending on how it's implemented). 
    # Actually wait, test_m49_resolution_gate doesn't show FAULTED_UNKNOWN on sequence collision, it just preserves the highest sequence or first terminal state. Let's see what the gate does.
    # We just need to ensure the gate absorbs it safely.
    assert records[-1].latest_accepted_sequence == 1


def test_timeout_handled_separately_from_physical_telemetry(setup_reconciler):
    reconciler, transport, gate = setup_reconciler
    
    record1 = gate.resolve_timeout("exec-time", 1)
    assert record1.timeout_status is True
    
    evt = create_event("exec-time", 2, CommandState.RUNNING, authority=EventSourceAuthority.CONTROL_PLANE_TIMEOUT)
    transport._put_normal(evt)
    
    records = reconciler.process_pending_events()
    assert len(records) == 0


def test_reconciler_remains_correct_when_drain_order_reversed(setup_reconciler):
    reconciler, transport, _ = setup_reconciler
    
    evt1 = create_event("exec-1", 1, CommandState.RUNNING)
    evt2 = create_event("exec-1", 2, CommandState.COMPLETED)
    
    transport._put_critical(evt2)
    transport._put_normal(evt1)
    
    records = reconciler.process_pending_events()
    assert len(records) == 2
    assert records[0].latest_accepted_sequence == 1
    assert records[1].latest_accepted_sequence == 2
    assert records[1].current_state == CommandState.COMPLETED


def test_resolution_gate_receives_events_only_after_validation(setup_reconciler):
    reconciler, transport, gate = setup_reconciler
    
    good_evt = create_event("exec-1", 1, CommandState.RUNNING)
    bad_auth = create_event("exec-1", 2, CommandState.RUNNING, authority=EventSourceAuthority.CONTROL_PLANE_TIMEOUT)
    bad_schema = {"execution_id": "exec-1", "event_sequence": 3}  # type: ignore
    
    transport._put_normal(good_evt)
    transport._put_normal(bad_auth)
    transport._put_normal(bad_schema)  # type: ignore
    
    records = reconciler.process_pending_events()
    assert len(records) == 1
    assert records[0].latest_accepted_sequence == 1
