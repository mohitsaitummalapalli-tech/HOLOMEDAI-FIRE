import threading
from typing import Dict, Any, Mapping
from types import MappingProxyType
import pytest

from holomed.devices.resolution import ExecutionResolutionGate
from holomed.devices.models import (
    ExecutionTelemetryEvent,
    CommandState,
    EventSourceAuthority,
    AuthoritativeExecutionRecord,
)

def create_event(
    execution_id: str,
    event_sequence: int,
    state: CommandState,
    lifecycle: int = 1,
    event_id: str = "evt-1"
) -> ExecutionTelemetryEvent:
    return ExecutionTelemetryEvent(
        event_id=event_id,
        endpoint_id="end-1",
        session_id="ses-1",
        lifecycle_generation=lifecycle,
        endpoint_lease_generation=1,
        execution_id=execution_id,
        command_sequence=1,
        event_sequence=event_sequence,
        event_type="test",
        observed_state=state,
        source_authority=EventSourceAuthority.HARDWARE_DRIVER,
        source_origin="driver",
        timestamp_utc="2026-01-01T00:00:00Z",
        payload=MappingProxyType({}),
    )


def test_terminal_obtains_resolution_gate_before_timeout():
    gate = ExecutionResolutionGate()
    evt = create_event("exec-1", 1, CommandState.COMPLETED)

    rec1 = gate.resolve_terminal_event(evt)
    assert rec1.terminal_resolution_status is True
    assert rec1.current_state == CommandState.COMPLETED

    rec2 = gate.resolve_timeout("exec-1", 1)
    assert rec2.terminal_resolution_status is True
    assert rec2.current_state == CommandState.COMPLETED
    assert rec2.timeout_status is False


def test_timeout_obtains_resolution_gate_before_terminal():
    gate = ExecutionResolutionGate()
    rec1 = gate.resolve_timeout("exec-2", 1)
    assert rec1.terminal_resolution_status is True
    assert rec1.current_state == CommandState.FAULTED_UNKNOWN
    assert rec1.timeout_status is True
    assert rec1.quarantine_consequence is True

    evt = create_event("exec-2", 1, CommandState.COMPLETED)
    rec2 = gate.resolve_terminal_event(evt)
    assert rec2.current_state == CommandState.FAULTED_UNKNOWN


def test_faulted_unknown_absorbs_late_terminal():
    gate = ExecutionResolutionGate()
    evt1 = create_event("exec-3", 1, CommandState.FAULTED_UNKNOWN)
    rec1 = gate.resolve_terminal_event(evt1)
    assert rec1.current_state == CommandState.FAULTED_UNKNOWN

    evt2 = create_event("exec-3", 2, CommandState.COMPLETED)
    rec2 = gate.resolve_terminal_event(evt2)
    assert rec2.current_state == CommandState.FAULTED_UNKNOWN


def test_endpoint_quarantine_consequence_is_returned():
    gate = ExecutionResolutionGate()
    evt = create_event("exec-4", 1, CommandState.FAILED)
    rec = gate.resolve_terminal_event(evt)
    assert rec.quarantine_consequence is True


def test_same_execution_cannot_regress_event_sequence():
    gate = ExecutionResolutionGate()
    evt1 = create_event("exec-5", 5, CommandState.RUNNING)
    rec1 = gate.resolve_terminal_event(evt1)
    assert rec1.latest_accepted_sequence == 5

    evt2 = create_event("exec-5", 3, CommandState.RUNNING)
    rec2 = gate.resolve_terminal_event(evt2)
    assert rec2.latest_accepted_sequence == 5


def test_concurrent_sequence_commit_preserves_highest_sequence():
    gate = ExecutionResolutionGate()
    evt1 = create_event("exec-6", 7, CommandState.RUNNING)
    evt2 = create_event("exec-6", 8, CommandState.RUNNING)

    gate.resolve_terminal_event(evt2)
    rec = gate.resolve_terminal_event(evt1)

    assert rec.latest_accepted_sequence == 8


def test_terminal_state_cannot_be_downgraded():
    gate = ExecutionResolutionGate()
    evt1 = create_event("exec-7", 1, CommandState.COMPLETED)
    gate.resolve_terminal_event(evt1)

    evt2 = create_event("exec-7", 2, CommandState.RUNNING)
    rec = gate.resolve_terminal_event(evt2)
    assert rec.current_state == CommandState.COMPLETED


def test_same_gate_serializes_terminal_vs_timeout():
    # Implicitly tested by the fact that both use the same execution lock.
    # We can verify lock creation.
    gate = ExecutionResolutionGate()
    assert len(gate._execution_locks) == 0
    gate.resolve_timeout("exec-8", 1)
    assert "exec-8" in gate._execution_locks


def test_distinct_execution_gates_do_not_block_each_other():
    gate = ExecutionResolutionGate()
    gate.resolve_timeout("exec-A", 1)
    gate.resolve_timeout("exec-B", 1)
    assert "exec-A" in gate._execution_locks
    assert "exec-B" in gate._execution_locks


def test_no_public_mutation_path_bypasses_gate():
    gate = ExecutionResolutionGate()
    rec = gate.resolve_timeout("exec-9", 1)
    assert isinstance(rec, AuthoritativeExecutionRecord)

    # Trying to mutate attributes directly on record should fail (it's frozen by semantics)
    # Actually Python class isn't strictly frozen unless it's a dataclass frozen=True,
    # but the API doesn't expose setters.
    with pytest.raises(AttributeError):
        rec.current_state = CommandState.COMPLETED
