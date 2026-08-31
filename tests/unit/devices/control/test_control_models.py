"""Unit tests for M00.6 control plane models, definitions, and limits."""

import pytest

from holomed.devices.control.exceptions import DeviceCommandValidationError
from holomed.devices.control.models import (
    DeviceCommandDefinition,
    DeviceQueryDefinition,
    IdempotencyRecord,
    MAX_REGISTERED_COMMANDS,
    MAX_REGISTERED_QUERIES,
)
from holomed.devices.models import DeviceState


def test_command_definition_validation() -> None:
    def dummy_handler(device, params):
        return {}

    # Valid definition
    cmd = DeviceCommandDefinition(
        command_name="camera.capture",
        handler=dummy_handler,
        required_capability_id="camera.stream",
        allow_ready=True,
    )
    assert cmd.command_name == "camera.capture"
    assert cmd.allow_ready is True

    # Invalid name (spaces, uppercase)
    with pytest.raises(DeviceCommandValidationError, match="Invalid command_name syntax"):
        DeviceCommandDefinition(command_name="Camera Capture", handler=dummy_handler)

    # Empty name
    with pytest.raises(DeviceCommandValidationError, match="Invalid command_name syntax"):
        DeviceCommandDefinition(command_name="", handler=dummy_handler)

    # Non-callable handler
    with pytest.raises(DeviceCommandValidationError, match="must be callable"):
        DeviceCommandDefinition(command_name="camera.zoom", handler="not_a_function")  # type: ignore


def test_query_definition_validation() -> None:
    def dummy_handler(device, params):
        return {}

    # Valid definition
    query = DeviceQueryDefinition(
        query_name="camera.status",
        handler=dummy_handler,
        allowed_states=(DeviceState.READY, DeviceState.ACTIVE),
    )
    assert query.query_name == "camera.status"
    assert DeviceState.READY in query.allowed_states

    # Invalid query name
    with pytest.raises(DeviceCommandValidationError, match="Invalid query_name syntax"):
        DeviceQueryDefinition(query_name="status?!", handler=dummy_handler)

    # Invalid state in allowed_states
    with pytest.raises(DeviceCommandValidationError, match="Invalid state in allowed_states"):
        DeviceQueryDefinition(query_name="camera.info", handler=dummy_handler, allowed_states=("READY",))  # type: ignore
