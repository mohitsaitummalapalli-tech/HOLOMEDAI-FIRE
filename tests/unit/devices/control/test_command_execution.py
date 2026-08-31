"""Unit tests for M00.6 command execution, routing, error containment, and secret redaction."""

import pytest

from holomed.configuration.models import SecretString
from holomed.devices.control.exceptions import (
    CapabilityUnauthorizedError,
    CommandNotFoundError,
    DeviceNotFoundError,
)
from holomed.devices.control.manager import DeviceControlManager
from holomed.devices.interfaces import RecordingDeviceEventSink, RegistryAuthorityToken
from holomed.devices.models import CapabilityCategory, DeviceCapability, DeviceState
from holomed.devices.registry import DeviceRegistry
from holomed.devices.simulated import SimulatedDevice
from holomed.protocol.builders import create_command
from holomed.protocol.models import MessageType
from holomed.runtime.logging import SecretFilter
from tests.unit.devices.conftest import make_test_context


def test_command_execution_success_and_response_envelope() -> None:
    token = RegistryAuthorityToken()
    registry = DeviceRegistry(token)
    device = SimulatedDevice("cam1", "USB:1")
    registry.register(device, token)
    device._state = DeviceState.ACTIVE

    sink = RecordingDeviceEventSink()
    manager = DeviceControlManager(registry=registry, event_sink=sink)
    ctx = make_test_context(epoch_id=1)
    manager.initialize(ctx)
    manager.start()

    executed_params = []

    def capture_handler(dev, params):
        executed_params.append(dict(params))
        return {"captured": True, "frame_id": 42}

    manager.register_command("camera.capture", capture_handler)

    cmd_envelope = create_command(
        message_name="device.command",
        source="cli_client",
        payload={"device_id": "cam1", "command": "camera.capture", "parameters": {"resolution": "1080p"}},
    )

    response = manager.handle_command(cmd_envelope)

    # Invariant enforcement
    assert response.message_type == MessageType.RESPONSE
    assert response.target == "cli_client"
    assert response.correlation_id == cmd_envelope.correlation_id
    assert response.causation_id == cmd_envelope.message_id
    assert response.payload["captured"] is True
    assert response.payload["frame_id"] == 42
    assert executed_params[0]["resolution"] == "1080p"

    # Verify audit event emitted
    assert len(sink.events) == 1
    assert sink.events[0].message_name == "device.command.executed"
    assert sink.events[0].payload["device_id"] == "cam1"


def test_command_handler_exception_redacted_in_error_response() -> None:
    token = RegistryAuthorityToken()
    registry = DeviceRegistry(token)
    device = SimulatedDevice("cam1", "USB:1")
    registry.register(device, token)
    device._state = DeviceState.ACTIVE

    filter_secrets = SecretFilter([SecretString("top_secret_credential_xyz")])
    manager = DeviceControlManager(registry=registry, secret_filter=filter_secrets)
    ctx = make_test_context(epoch_id=1)
    manager.initialize(ctx)
    manager.start()

    def crashing_handler(dev, params):
        raise RuntimeError("Hardware probe failed with token top_secret_credential_xyz in bus")

    manager.register_command("camera.crash", crashing_handler)

    cmd_envelope = create_command(
        message_name="device.command",
        source="client",
        payload={"device_id": "cam1", "command": "camera.crash"},
    )

    response = manager.handle_command(cmd_envelope)
    assert response.message_type == MessageType.ERROR
    assert "top_secret_credential_xyz" not in response.payload["error_message"]
    assert "<redacted>" in response.payload["error_message"]


def test_command_device_not_found() -> None:
    token = RegistryAuthorityToken()
    registry = DeviceRegistry(token)
    manager = DeviceControlManager(registry=registry)
    ctx = make_test_context(epoch_id=1)
    manager.initialize(ctx)
    manager.start()

    manager.register_command("tool.reset", lambda d, p: {})

    cmd_envelope = create_command(
        message_name="device.command",
        source="client",
        payload={"device_id": "nonexistent", "command": "tool.reset"},
    )

    response = manager.handle_command(cmd_envelope)
    assert response.message_type == MessageType.ERROR
    assert response.payload["error_code"] == "ERR_DEVICE_NOT_FOUND"


def test_command_not_found() -> None:
    token = RegistryAuthorityToken()
    registry = DeviceRegistry(token)
    device = SimulatedDevice("cam1", "USB:1")
    registry.register(device, token)
    device._state = DeviceState.ACTIVE

    manager = DeviceControlManager(registry=registry)
    ctx = make_test_context(epoch_id=1)
    manager.initialize(ctx)
    manager.start()

    cmd_envelope = create_command(
        message_name="device.command",
        source="client",
        payload={"device_id": "cam1", "command": "unregistered.command"},
    )

    response = manager.handle_command(cmd_envelope)
    assert response.message_type == MessageType.ERROR
    assert response.payload["error_code"] == "ERR_COMMAND_NOT_FOUND"


def test_command_idempotency_replay_and_conflict() -> None:
    token = RegistryAuthorityToken()
    registry = DeviceRegistry(token)
    device = SimulatedDevice("cam1", "USB:1")
    registry.register(device, token)
    device._state = DeviceState.ACTIVE

    manager = DeviceControlManager(registry=registry)
    ctx = make_test_context(epoch_id=1)
    manager.initialize(ctx)
    manager.start()

    exec_counts = 0

    def counting_handler(dev, params):
        nonlocal exec_counts
        exec_counts += 1
        return {"exec_count": exec_counts}

    manager.register_command("tool.count", counting_handler)

    cmd_envelope = create_command(
        message_name="device.command",
        source="client",
        payload={"device_id": "cam1", "command": "tool.count", "parameters": {"a": 1}},
    )

    # 1. First execution
    res1 = manager.handle_command(cmd_envelope)
    assert res1.message_type == MessageType.RESPONSE
    assert res1.payload["exec_count"] == 1
    assert exec_counts == 1

    # 2. Replay same envelope with same payload -> cached response returned, handler not called again
    res2 = manager.handle_command(cmd_envelope)
    assert res2.message_type == MessageType.RESPONSE
    assert res2.payload["exec_count"] == 1
    assert exec_counts == 1

    # 3. Same message_id with conflicting payload -> ERR_IDEMPOTENCY_CONFLICT
    from holomed.protocol.models import MessageEnvelope
    conflicting_envelope = MessageEnvelope(
        protocol_version=cmd_envelope.protocol_version,
        message_id=cmd_envelope.message_id,
        correlation_id=cmd_envelope.correlation_id,
        causation_id=None,
        message_type=MessageType.COMMAND,
        message_name="device.command",
        source="client",
        target=None,
        timestamp_utc=cmd_envelope.timestamp_utc,
        payload={"device_id": "cam1", "command": "tool.count", "parameters": {"a": 2}},
        metadata={},
    )
    res3 = manager.handle_command(conflicting_envelope)
    assert res3.message_type == MessageType.ERROR
    assert res3.payload["error_code"] == "ERR_IDEMPOTENCY_CONFLICT"
    assert exec_counts == 1


def test_command_event_sink_failure_does_not_break_execution() -> None:
    token = RegistryAuthorityToken()
    registry = DeviceRegistry(token)
    device = SimulatedDevice("cam1", "USB:1")
    registry.register(device, token)
    device._state = DeviceState.ACTIVE

    class CrashingEventSink:
        def emit(self, event):
            raise RuntimeError("Database sink offline")

    manager = DeviceControlManager(registry=registry, event_sink=CrashingEventSink())
    ctx = make_test_context(epoch_id=1)
    manager.initialize(ctx)
    manager.start()

    manager.register_command("tool.ping", lambda d, p: {"pong": True})

    cmd_envelope = create_command(
        message_name="device.command",
        source="client",
        payload={"device_id": "cam1", "command": "tool.ping"},
    )

    response = manager.handle_command(cmd_envelope)
    assert response.message_type == MessageType.RESPONSE
    assert response.payload["pong"] is True
    assert manager._sink_errors_count == 1

