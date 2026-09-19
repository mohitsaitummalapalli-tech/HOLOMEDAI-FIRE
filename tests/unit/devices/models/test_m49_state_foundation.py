import pytest
from holomed.devices.models import (
    CommandState,
    EndpointState,
    SubmissionStatus,
    PhysicalCommandResult,
)
from holomed.devices.exceptions import DeviceValidationError

def test_command_state_terminal_immutability():
    # Verify the terminal states exist
    terminals = {
        CommandState.COMPLETED,
        CommandState.FAILED,
        CommandState.PREEMPTED,
        CommandState.INTERLOCKED,
        CommandState.FAULTED_UNKNOWN,
    }

    # Ensure they are distinct strings and correct Enum values
    assert len(terminals) == 5
    assert CommandState.FAULTED_UNKNOWN.value == "FAULTED_UNKNOWN"
    assert CommandState.INTERLOCKED.value == "INTERLOCKED"

def test_endpoint_state_semantics():
    # Ensure EndpointState clearly defines READY and QUARANTINED
    assert EndpointState.READY.value == "READY"
    assert EndpointState.QUARANTINED.value == "QUARANTINED"
    expected_members = {
        'READY', 'QUARANTINED', 'PHYSICALLY_ISOLATED',
        'REINITIALIZATION_REQUIRED', 'REINITIALIZATION_PREPARE'
    }
    assert {e.name for e in EndpointState} == expected_members
    assert len(EndpointState) == 5

def test_submission_status_strict_values():
    # Ensure SubmissionStatus enforces exact non-blocking values
    assert SubmissionStatus.ACCEPTED.value == "ACCEPTED"
    assert SubmissionStatus.QUEUE_FULL.value == "QUEUE_FULL"
    assert SubmissionStatus.WORKER_UNAVAILABLE.value == "WORKER_UNAVAILABLE"
    assert SubmissionStatus.SHUTTING_DOWN.value == "SHUTTING_DOWN"
    assert SubmissionStatus.DUPLICATE_REJECTED.value == "DUPLICATE_REJECTED"

def test_physical_command_result_admits_submission_status():
    result = PhysicalCommandResult(
        status=SubmissionStatus.ACCEPTED,
        details={"queue": 1}
    )
    assert result.status == SubmissionStatus.ACCEPTED
    assert result.details["queue"] == 1

def test_physical_command_result_rejects_arbitrary_string():
    with pytest.raises(DeviceValidationError, match="status must be SubmissionStatus"):
        PhysicalCommandResult(status="ACCEPTED", details={})  # type: ignore

def test_physical_command_result_rejects_invalid_type():
    with pytest.raises(DeviceValidationError, match="status must be SubmissionStatus"):
        PhysicalCommandResult(status=123, details={})  # type: ignore
