"""M49.3.3.1 Tests: Telemetry Event Domain and Identity Schema."""

import pytest

from holomed.devices.exceptions import DeviceValidationError
from holomed.devices.models import (
    AuthoritativeExecutionRecord,
    CommandState,
    EventSourceAuthority,
    ExecutionTelemetryEvent,
)


def test_valid_telemetry_event_construction():
    """Test 1 & 7 & 9: valid telemetry event construction and execution identity."""
    event = ExecutionTelemetryEvent(
        evidence_generation=1,
        cryptographic_signature=None,
        fencing_challenge=None,
        event_id="evt_123",
        endpoint_id="ep_01",
        session_id="sess_01",
        lifecycle_generation=1,
        endpoint_lease_generation=2,
        execution_id="exec_abc",
        command_sequence=1,
        event_sequence=1,
        event_type="PROGRESS",
        observed_state=CommandState.RUNNING,
        source_authority=EventSourceAuthority.ENDPOINT_ADAPTER,
        source_origin="SimulatedPhysicalEndpoint",
        timestamp_utc="2026-09-14T10:00:00Z",
        payload={"progress": 50},
    )

    assert event.event_id == "evt_123"
    assert event.execution_id == "exec_abc"
    assert event.event_sequence == 1
    assert event.source_authority == EventSourceAuthority.ENDPOINT_ADAPTER


def test_event_immutability():
    """Test 2: event immutability."""
    event = ExecutionTelemetryEvent(
        evidence_generation=1,
        cryptographic_signature=None,
        fencing_challenge=None,
        event_id="evt_123",
        endpoint_id="ep_01",
        session_id="sess_01",
        lifecycle_generation=1,
        endpoint_lease_generation=2,
        execution_id="exec_abc",
        command_sequence=1,
        event_sequence=1,
        event_type="PROGRESS",
        observed_state=CommandState.RUNNING,
        source_authority=EventSourceAuthority.ENDPOINT_ADAPTER,
        source_origin="SimulatedPhysicalEndpoint",
        timestamp_utc="2026-09-14T10:00:00Z",
        payload={"progress": 50},
    )

    with pytest.raises(Exception):
        # frozen dataclass prevents setting attributes
        event.event_sequence = 2  # type: ignore


def test_nested_payload_integrity():
    """Test 3: nested payload integrity."""
    mutable_dict = {"progress": 50, "nested": {"a": 1}}

    event = ExecutionTelemetryEvent(
        evidence_generation=1,
        cryptographic_signature=None,
        fencing_challenge=None,
        event_id="evt_123",
        endpoint_id="ep_01",
        session_id="sess_01",
        lifecycle_generation=1,
        endpoint_lease_generation=2,
        execution_id="exec_abc",
        command_sequence=1,
        event_sequence=1,
        event_type="PROGRESS",
        observed_state=CommandState.RUNNING,
        source_authority=EventSourceAuthority.ENDPOINT_ADAPTER,
        source_origin="SimulatedPhysicalEndpoint",
        timestamp_utc="2026-09-14T10:00:00Z",
        payload=mutable_dict,
    )

    # Payload should be deep frozen by deep_freeze_parameter
    with pytest.raises(Exception):
        # Type error or attribute error on MappingProxyType
        event.payload["progress"] = 60  # type: ignore

    with pytest.raises(Exception):
        event.payload["nested"]["a"] = 2  # type: ignore


def test_valid_source_authority():
    """Test 4: valid source authority."""
    # Control Plane Timeout authority
    event = ExecutionTelemetryEvent(
        evidence_generation=1,
        cryptographic_signature=None,
        fencing_challenge=None,
        event_id="evt_123",
        endpoint_id="ep_01",
        session_id="sess_01",
        lifecycle_generation=1,
        endpoint_lease_generation=2,
        execution_id="exec_abc",
        command_sequence=1,
        event_sequence=1,
        event_type="TIMEOUT",
        observed_state=CommandState.FAULTED_UNKNOWN,
        source_authority=EventSourceAuthority.CONTROL_PLANE_TIMEOUT,
        source_origin="ResolutionGate",
        timestamp_utc="2026-09-14T10:00:00Z",
        payload={},
    )
    assert event.source_authority == EventSourceAuthority.CONTROL_PLANE_TIMEOUT


def test_simulated_adapter_cannot_claim_hardware_authority():
    """Test 5 & 6: simulated adapter cannot claim hardware authority (rejected)."""
    with pytest.raises(DeviceValidationError, match="Illegal authority combination"):
        ExecutionTelemetryEvent(
        evidence_generation=1,
        cryptographic_signature=None,
        fencing_challenge=None,
        event_id="evt_123",
            endpoint_id="ep_01",
            session_id="sess_01",
            lifecycle_generation=1,
            endpoint_lease_generation=2,
            execution_id="exec_abc",
            command_sequence=1,
            event_sequence=1,
            event_type="PROGRESS",
            observed_state=CommandState.RUNNING,
            source_authority=EventSourceAuthority.HARDWARE_DRIVER,
            source_origin="SimulatedPhysicalEndpoint",
            timestamp_utc="2026-09-14T10:00:00Z",
            payload={},
        )


def test_malformed_execution_identity_rejected():
    """Test 8: malformed execution identity rejected."""
    with pytest.raises(DeviceValidationError, match="execution_id must be a non-empty string"):
        ExecutionTelemetryEvent(
        evidence_generation=1,
        cryptographic_signature=None,
        fencing_challenge=None,
        event_id="evt_123",
            endpoint_id="ep_01",
            session_id="sess_01",
            lifecycle_generation=1,
            endpoint_lease_generation=2,
            execution_id="",
            command_sequence=1,
            event_sequence=1,
            event_type="PROGRESS",
            observed_state=CommandState.RUNNING,
            source_authority=EventSourceAuthority.ENDPOINT_ADAPTER,
            source_origin="SimulatedPhysicalEndpoint",
            timestamp_utc="2026-09-14T10:00:00Z",
            payload={},
        )


def test_invalid_event_sequence_rejected():
    """Test 10: invalid event sequence rejected."""
    with pytest.raises(DeviceValidationError, match="event_sequence must be an int >= 1"):
        ExecutionTelemetryEvent(
        evidence_generation=1,
        cryptographic_signature=None,
        fencing_challenge=None,
        event_id="evt_123",
            endpoint_id="ep_01",
            session_id="sess_01",
            lifecycle_generation=1,
            endpoint_lease_generation=2,
            execution_id="exec_abc",
            command_sequence=1,
            event_sequence=0,
            event_type="PROGRESS",
            observed_state=CommandState.RUNNING,
            source_authority=EventSourceAuthority.ENDPOINT_ADAPTER,
            source_origin="SimulatedPhysicalEndpoint",
            timestamp_utc="2026-09-14T10:00:00Z",
            payload={},
        )


def test_authoritative_execution_record_structure():
    """Test 11: authoritative execution record structure."""
    record = AuthoritativeExecutionRecord(
        execution_id="exec_abc",
        current_state=CommandState.RUNNING,
        latest_accepted_sequence=1,
        terminal_resolution_status=False,
        terminal_event_id=None,
        source_authority=None,
        lifecycle_generation=1,
        timeout_status=False,
        quarantine_consequence=False,
    )

    assert record.execution_id == "exec_abc"
    assert record.current_state == CommandState.RUNNING
    assert record.latest_accepted_sequence == 1
    assert record.terminal_resolution_status is False
    assert record.terminal_event_id is None


def test_record_mutation_prevented():
    """Test 12: record cannot be mutated through an unintended public path."""
    record = AuthoritativeExecutionRecord(
        execution_id="exec_abc",
        current_state=CommandState.RUNNING,
        latest_accepted_sequence=1,
        terminal_resolution_status=False,
        terminal_event_id=None,
        source_authority=None,
        lifecycle_generation=1,
        timeout_status=False,
        quarantine_consequence=False,
    )

    with pytest.raises(AttributeError):
        # We exposed no setters, only properties.
        record.current_state = CommandState.COMPLETED  # type: ignore
