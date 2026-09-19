"""HoloMed AI - M49.3.5 Adversarial Model Validation."""

import pytest

from holomed.devices.models import (
    PhysicalCommand,
    EndpointLease,
    ExecutionTelemetryEvent,
    EventSourceAuthority,
    CommandState,
    AuthoritativeExecutionRecord,
)
from holomed.devices.exceptions import DeviceValidationError

def test_m49_3_5_physical_command_adversarial_forgery():
    """Verify that PhysicalCommand fields cannot be forged or bypassed."""
    valid_kwargs = {
        "device_epoch": 1,
        "controller_epoch": 1,
        "physical_operation_id": "op_123",
        "command_nonce": "nonce_abc",
        "endpoint_id": "ep_1",
        "session_id": "sess_1",
        "lifecycle_generation": 1,
        "endpoint_lease_generation": 1,
        "execution_id": "exec_1",
        "capability_scope": frozenset(["cap1"]),
        "command_sequence": 1,
        "operation": "actuate",
        "parameters": {},
    }

    # Negative epoch
    kwargs = valid_kwargs.copy()
    kwargs["device_epoch"] = -1
    with pytest.raises(DeviceValidationError, match="device_epoch must be an int >= 0"):
        PhysicalCommand(**kwargs)

    kwargs = valid_kwargs.copy()
    kwargs["controller_epoch"] = -1
    with pytest.raises(DeviceValidationError, match="controller_epoch must be an int >= 0"):
        PhysicalCommand(**kwargs)

    # Missing strings
    for field in ["physical_operation_id", "command_nonce"]:
        kwargs = valid_kwargs.copy()
        kwargs[field] = "   "
        with pytest.raises(DeviceValidationError, match="must be a non-empty string"):
            PhysicalCommand(**kwargs)


def test_m49_3_5_endpoint_lease_adversarial_forgery():
    """Verify EndpointLease immutable authorities."""
    valid_kwargs = {
        "device_epoch": 1,
        "controller_epoch": 1,
        "endpoint_id": "ep_1",
        "device_id": "dev_1",
        "session_id": "sess_1",
        "lifecycle_generation": 1,
        "endpoint_lease_generation": 1,
        "execution_id": "exec_1",
        "capability_scope": frozenset(["cap1"]),
    }

    # Wrong type
    kwargs = valid_kwargs.copy()
    kwargs["device_epoch"] = "1"
    with pytest.raises(DeviceValidationError, match="device_epoch must be an int >= 0"):
        EndpointLease(**kwargs)


def test_m49_3_5_execution_telemetry_adversarial_forgery():
    """Verify ExecutionTelemetryEvent validation."""
    valid_kwargs = {
        "evidence_generation": 1,
        "cryptographic_signature": "sig_abc",
        "fencing_challenge": "chal_xyz",
        "event_id": "ev_1",
        "endpoint_id": "ep_1",
        "session_id": "sess_1",
        "lifecycle_generation": 1,
        "endpoint_lease_generation": 1,
        "execution_id": "exec_1",
        "command_sequence": 1,
        "event_sequence": 1,
        "event_type": "telemetry",
        "observed_state": CommandState.RUNNING,
        "source_authority": EventSourceAuthority.ENDPOINT_ADAPTER,
        "source_origin": "origin_abc",
        "timestamp_utc": "2026-09-19T00:00:00Z",
        "payload": {},
    }

    # Zero evidence generation
    kwargs = valid_kwargs.copy()
    kwargs["evidence_generation"] = 0
    with pytest.raises(DeviceValidationError, match="evidence_generation must be an int >= 1"):
        ExecutionTelemetryEvent(**kwargs)

    # Empty signature when provided
    kwargs = valid_kwargs.copy()
    kwargs["cryptographic_signature"] = "   "
    with pytest.raises(DeviceValidationError, match="cryptographic_signature must be a non-empty string or None"):
        ExecutionTelemetryEvent(**kwargs)

    # Empty fencing challenge
    kwargs = valid_kwargs.copy()
    kwargs["fencing_challenge"] = "   "
    with pytest.raises(DeviceValidationError, match="fencing_challenge must be a non-empty string or None"):
        ExecutionTelemetryEvent(**kwargs)

def test_m49_3_5_authoritative_execution_record():
    """Verify AuthoritativeExecutionRecord physical_recovery_active property."""
    rec = AuthoritativeExecutionRecord(
        execution_id="exec_1",
        current_state=CommandState.RECOVERY_REQUIRED,
        latest_accepted_sequence=1,
        terminal_resolution_status=False,
        terminal_event_id=None,
        source_authority=None,
        lifecycle_generation=1,
        timeout_status=False,
        quarantine_consequence=False,
        physical_recovery_active=True
    )

    assert rec.physical_recovery_active is True
