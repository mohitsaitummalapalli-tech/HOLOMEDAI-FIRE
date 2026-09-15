import pytest
import time
import threading
import uuid
from typing import List
from datetime import datetime, timezone

from holomed.devices.models import (
    CommandState,
    EndpointSafetyState,
    EndpointState,
    ExecutionTelemetryEvent,
    EventSourceAuthority,
    PhysicalCommand,
    EndpointLease,
)
from holomed.devices.simulated import SimulatedPhysicalEndpoint
from holomed.devices.resolution import ExecutionResolutionGate
from holomed.devices.reconciler import TelemetryReconciler
from holomed.devices.interfaces import IExecutionTelemetryPublisher
from holomed.devices.transport import TelemetryTransport, TelemetryPublisher

@pytest.fixture
def gate():
    return ExecutionResolutionGate()

@pytest.fixture
def transport():
    return TelemetryTransport()

@pytest.fixture
def publisher(transport):
    return TelemetryPublisher(transport)

@pytest.fixture
def endpoint(gate, publisher):
    ep = SimulatedPhysicalEndpoint("ep_1", "dev_1", gate=gate, publisher=publisher)
    lease = EndpointLease(
        endpoint_id="ep_1",
        device_id="dev_1",
        session_id="session_1",
        lifecycle_generation=1,
        endpoint_lease_generation=1,
        execution_id="exec_1",
        capability_scope=frozenset(["cap_1"]),
    )
    ep.acquire_lease(lease)
    yield ep
    ep.stop_worker()

def create_cmd(exec_id: str, seq: int = 1):
    return PhysicalCommand(
        endpoint_id="ep_1",
        session_id="session_1",
        lifecycle_generation=1,
        endpoint_lease_generation=1,
        execution_id=exec_id,
        capability_scope=frozenset(["cap_1"]),
        command_sequence=seq,
        operation="move",
        parameters={},
    )


# a. normal telemetry success path
def test_normal_telemetry_success(endpoint, publisher, gate, transport):
    exec_id = "exec_normal"
    cmd = create_cmd(exec_id)
    endpoint.submit_command(cmd)

    # Wait for completion
    time.sleep(0.2)

    # Check published events
    # Reconciler
    reconciler = TelemetryReconciler(transport, gate)
    reconciler.process_pending_events()

    record = gate.resolve_timeout(exec_id, 1) # should not change if terminal
    assert record.terminal_resolution_status is True
    assert record.current_state == CommandState.COMPLETED


# b. timeout while queued
def test_timeout_while_queued(endpoint, publisher, gate, transport):
    endpoint.stop_worker()

    exec_id = "exec_timeout_queued"
    cmd = create_cmd(exec_id)
    endpoint.submit_command(cmd)

    # Timeout commits first
    record = gate.resolve_timeout(exec_id, 1)
    assert record.current_state == CommandState.FAULTED_UNKNOWN

    # Now start worker
    endpoint._start_worker()
    time.sleep(0.1)

    # The worker should have dequeued it, checked gate, and aborted
    events = transport.drain_normal()
    states = [e.observed_state for e in events if e.execution_id == exec_id]
    assert states == [] # No RUNNING event


# c. timeout vs claim race (using atomic claim boundary)
def test_timeout_vs_claim_race(endpoint, gate):
    exec_id = "exec_race"
    # Claim wins
    assert gate.claim_execution_ownership(exec_id, 1) is True

    exec_id_2 = "exec_race_2"
    gate.resolve_timeout(exec_id_2, 1)
    assert gate.claim_execution_ownership(exec_id_2, 1) is False # timeout won


# d. timeout after physical claim
def test_timeout_after_physical_claim(endpoint, publisher, gate, transport):
    exec_id = "exec_timeout_after_claim"
    cmd = create_cmd(exec_id)

    endpoint.submit_command(cmd)
    time.sleep(0.05) # wait for RUNNING

    events = transport.drain_normal()
    states = [e.observed_state for e in events if e.execution_id == exec_id]
    assert CommandState.RUNNING in states

    # Timeout strikes
    gate.resolve_timeout(exec_id, 1)

    # Let endpoint finish
    time.sleep(0.2)

    reconciler = TelemetryReconciler(transport, gate)
    reconciler.process_pending_events()

    record = gate.resolve_timeout(exec_id, 1)
    assert record.current_state == CommandState.FAULTED_UNKNOWN # Timeout wins
    assert record.timeout_status is True


# e. stop before claim
def test_stop_before_claim(endpoint, publisher, gate, transport):
    exec_id = "exec_stop_pre"
    cmd = create_cmd(exec_id)

    # We acquire the lock so we can queue it and request stop before the worker thread dequeues it
    endpoint._submit_lock.acquire()
    try:
        endpoint._command_queue.put(cmd)
        endpoint._stop_requests.add(exec_id)
    finally:
        endpoint._submit_lock.release()

    time.sleep(0.1)

    events = transport.drain_critical() + transport.drain_normal()
    states = [e.observed_state for e in events if e.execution_id == exec_id]
    assert CommandState.PREEMPTED in states


# g. stop after claim without confirmation
def test_stop_after_claim_unconfirmed(endpoint, publisher, gate, transport):
    exec_id = "exec_stop_unconfirmed"
    cmd = create_cmd(exec_id)
    endpoint.submit_command(cmd)
    time.sleep(0.02)

    endpoint._shutdown_event.set()
    time.sleep(0.1)

    events = transport.drain_critical() + transport.drain_normal()
    states = [e.observed_state for e in events if e.execution_id == exec_id]
    assert CommandState.FAULTED_UNKNOWN in states


# i. quarantine cannot return to READY
def test_quarantine_invariant(endpoint):
    endpoint._endpoint_state = EndpointState.QUARANTINED

    with pytest.raises(Exception):
        endpoint.submit_command(create_cmd("exec_quarantine"))

    endpoint.recover()
    assert endpoint.endpoint_state == EndpointState.READY


# j. duplicate event
def test_duplicate_event(gate):
    transport = TelemetryTransport()
    reconciler = TelemetryReconciler(transport, gate)
    event = ExecutionTelemetryEvent(
        event_id="e1", execution_id="exec_dup", event_sequence=1,
        observed_state=CommandState.RUNNING, timestamp_utc="2026-09-15T00:00:00Z",
        source_authority=EventSourceAuthority.ENDPOINT_ADAPTER,
        lifecycle_generation=1, endpoint_id="ep",
        session_id="s1", endpoint_lease_generation=1,
        command_sequence=1, event_type="STATE", source_origin="SimulatedPhysicalEndpoint",
        payload={}
    )
    gate.resolve_terminal_event(event)
    gate.resolve_terminal_event(event)
    r1 = gate._records[event.execution_id]
    assert r1.current_state == CommandState.RUNNING


# k. sequence collision
def test_sequence_collision(gate):
    transport = TelemetryTransport()
    reconciler = TelemetryReconciler(transport, gate)
    e1 = ExecutionTelemetryEvent(
        event_id="e1", execution_id="exec_col", event_sequence=1,
        observed_state=CommandState.COMPLETED, timestamp_utc="2026-09-15T00:00:00Z",
        source_authority=EventSourceAuthority.ENDPOINT_ADAPTER,
        lifecycle_generation=1, endpoint_id="ep",
        session_id="s1", endpoint_lease_generation=1,
        command_sequence=1, event_type="STATE", source_origin="SimulatedPhysicalEndpoint",
        payload={}
    )
    e2 = ExecutionTelemetryEvent(
        event_id="e2", execution_id="exec_col", event_sequence=1,
        observed_state=CommandState.FAILED, timestamp_utc="2026-09-15T00:00:00Z",
        source_authority=EventSourceAuthority.ENDPOINT_ADAPTER,
        lifecycle_generation=1, endpoint_id="ep",
        session_id="s1", endpoint_lease_generation=1,
        command_sequence=1, event_type="STATE", source_origin="SimulatedPhysicalEndpoint",
        payload={}
    )
    gate.resolve_terminal_event(e1)
    gate.resolve_terminal_event(e2)
    r1 = gate._records[e1.execution_id]
    assert r1.current_state == CommandState.FAULTED_UNKNOWN


# m. critical telemetry loss/non-blocking behavior
def test_non_blocking_telemetry(endpoint, publisher, gate, transport):
    endpoint._fail_on_publish = True
    exec_id = "exec_loss"
    endpoint.submit_command(create_cmd(exec_id))
    time.sleep(0.1)
    events = transport.drain_critical() + transport.drain_normal()
    assert len(events) == 0
    record = gate.resolve_timeout(exec_id, 1)
    assert record.current_state == CommandState.FAULTED_UNKNOWN


# n. worker terminal path cannot bypass Reconciler/Gate
def test_worker_path_bypass(endpoint, gate):
    exec_id = "exec_bypass"
    endpoint.submit_command(create_cmd(exec_id))
    time.sleep(0.1)
    record = gate.resolve_timeout(exec_id, 1)
    assert record.current_state == CommandState.FAULTED_UNKNOWN
