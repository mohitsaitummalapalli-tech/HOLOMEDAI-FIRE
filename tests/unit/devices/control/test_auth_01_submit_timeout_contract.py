"""Tests for the bounded execution contract of submit_command during AUTH-01 remediation."""

import pytest
import time
from unittest.mock import Mock, patch, MagicMock
from holomed.devices.control.manager import DeviceControlManager
from holomed.devices.models import PhysicalCommand, SubmissionStatus, PhysicalCommandResult
from holomed.devices.resolution import ExecutionResolutionGate

def create_test_manager():
    registry = Mock()
    registry.contains.return_value = True
    device = Mock()
    device.device_id = "cam1"
    device.current_epoch = 1
    device.capabilities = [Mock(capability_id="test", requires_physical_endpoint=True, target_endpoint_id="USB:1")]
    from holomed.devices.control.manager import IPhysicalEndpoint
    ep = Mock(spec=IPhysicalEndpoint)
    ep.endpoint_id = "USB:1"
    device.endpoints = [ep]

    req_cap = Mock(capability_id="test", requires_physical_endpoint=True, target_endpoint_id="USB:1")
    device.get_capability.return_value = req_cap

    registry.get.return_value = device
    registry.all_devices = [device]

    resolution_gate = ExecutionResolutionGate()
    manager = DeviceControlManager(registry=registry, resolution_gate=resolution_gate)
    manager._capacity_releaser = Mock()
    manager._capacity_admitter = Mock()
    manager._state = Mock()
    manager._state.name = "STARTED"

    lease_registry = Mock()
    lease_registry.issue_lease.return_value = Mock(endpoint_lease_generation=1)
    lease_registry.next_command_sequence.return_value = 1
    manager._lease_registry = lease_registry
    manager._admission_state = Mock()
    manager._admission_state.value = "READY"
    manager._admission_state == "READY" # Hack for equality

    manager._authoritative_epoch_provider = lambda: 1
    return manager, ep

def test_submit_command_reject_rollback_contract():
    manager, ep = create_test_manager()
    exec_id = "exec-reject"

    # Endpoint returns REJECTED boundedly
    ep.submit_command.return_value = PhysicalCommandResult(
        status=SubmissionStatus.REJECTED, details={}
    )

    from holomed.protocol.models import MessageEnvelope, MessageType
    from datetime import datetime, timezone
    envelope = MessageEnvelope(
        protocol_version="1.0",
        message_id="11111111-1111-1111-1111-111111111111",
        correlation_id="22222222-2222-2222-2222-222222222222",
        causation_id=None,
        message_type=MessageType.COMMAND,
        message_name="device.command",
        source="test",
        target="control",
        timestamp_utc=datetime.now(timezone.utc).isoformat(),
        payload={
            "device_id": "cam1",
            "command": "test",
            "parameters": {},
            "session_id": "session-1",
            "session_lifecycle_generation": 1,
            "execution_id": exec_id,
            "command_nonce": "nonce-1"
        },
        metadata={}
    )

    manager._idempotency = Mock()
    manager._idempotency.get.return_value = None
    manager._commands = {"test": Mock(required_capability_id="test")}
    manager._verifier = Mock()
    manager._verifier.validate_and_canonicalize_parameters.return_value = {}
    manager._verifier.validate_and_canonicalize_command_result.return_value = {}
    manager._secret_filter = Mock()
    manager._secret_filter.redact.side_effect = lambda x: str(x)

    # Mock admit_physical_command context manager
    admission_ctx = MagicMock()
    admission_ctx.__enter__.return_value = Mock(physical_operation_id="op-1", is_replay=False)
    manager.admit_physical_command = Mock(return_value=admission_ctx)
    manager._generate_execution_id = Mock(return_value=exec_id)
    manager._generate_nonce = Mock(return_value="nonce-1")
    manager._capacity_releaser = Mock()

    from holomed.devices.control.manager import AdmissionState
    with patch("holomed.devices.control.manager.DeviceControlManager._require_started"):
        with patch.object(manager, "_admission_state", AdmissionState.READY):
            response1 = manager.handle_command(envelope)

    if response1.message_type == MessageType.ERROR:
        print("REJECT TEST FAILED WITH ENVELOPE:", response1.payload)

    # Assert _active_commands tombstone does not leak
    assert exec_id not in manager._active_commands
    # Assert deadlines does not leak
    assert exec_id not in manager._deadlines
    # Assert resource rollback occurred
    manager._capacity_releaser.assert_called_once()


def test_submit_command_exception_rollback_contract():
    manager, ep = create_test_manager()
    exec_id = "exec-exception"

    # Endpoint raises exception boundedly
    ep.submit_command.side_effect = RuntimeError("Driver bounded failure")

    from holomed.protocol.models import MessageEnvelope, MessageType
    from datetime import datetime, timezone
    envelope = MessageEnvelope(
        protocol_version="1.0",
        message_id="33333333-3333-3333-3333-333333333333",
        correlation_id="44444444-4444-4444-4444-444444444444",
        causation_id=None,
        message_type=MessageType.COMMAND,
        message_name="device.command",
        source="test",
        target="control",
        timestamp_utc=datetime.now(timezone.utc).isoformat(),
        payload={
            "device_id": "cam1",
            "command": "test",
            "parameters": {},
            "session_id": "session-2",
            "session_lifecycle_generation": 2,
            "execution_id": exec_id,
            "command_nonce": "nonce-2"
        },
        metadata={}
    )

    manager._idempotency = Mock()
    manager._idempotency.get.return_value = None
    manager._commands = {"test": Mock(required_capability_id="test")}
    manager._verifier = Mock()
    manager._verifier.validate_and_canonicalize_parameters.return_value = {}
    manager._verifier.validate_and_canonicalize_command_result.return_value = {}
    manager._secret_filter = Mock()
    manager._secret_filter.redact.side_effect = lambda x: str(x)

    admission_ctx = MagicMock()
    admission_ctx.__enter__.return_value = Mock(physical_operation_id="op-2", is_replay=False)
    manager.admit_physical_command = Mock(return_value=admission_ctx)
    manager._generate_execution_id = Mock(return_value=exec_id)
    manager._generate_nonce = Mock(return_value="nonce-2")
    manager._capacity_releaser = Mock()

    from holomed.devices.control.manager import AdmissionState
    with patch("holomed.devices.control.manager.DeviceControlManager._require_started"):
        with patch.object(manager, "_admission_state", AdmissionState.READY):
            response = manager.handle_command(envelope)

    if response.payload.get("error_code") != "ERR_RUNTIMEERROR":
        print("EXCEPTION TEST FAILED WITH ENVELOPE:", response.payload)

    assert response.payload["error_code"] == "ERR_RUNTIMEERROR"

    # Assert _active_commands tombstone does not leak
    assert exec_id not in manager._active_commands
    # Assert deadlines does not leak
    assert exec_id not in manager._deadlines
    # Assert resource rollback occurred
    manager._capacity_releaser.assert_called_once()

    # Assert _active_commands tombstone does not leak
    assert exec_id not in manager._active_commands
    # Assert deadlines does not leak
    assert exec_id not in manager._deadlines
    # Assert resource rollback occurred
    manager._capacity_releaser.assert_called_once()
