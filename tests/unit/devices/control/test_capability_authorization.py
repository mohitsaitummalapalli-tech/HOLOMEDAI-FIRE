"""Unit tests for M00.6 capability authorization and state verification."""

import pytest

from holomed.devices.control.exceptions import (
    CapabilityUnauthorizedError,
    DeviceNotActiveError,
    DeviceStateError,
)
from holomed.devices.control.models import DeviceCommandDefinition, DeviceQueryDefinition
from holomed.devices.control.verifier import CommandVerifier
from holomed.devices.models import CapabilityCategory, DeviceCapability, DeviceState
from holomed.devices.simulated import SimulatedDevice


def test_command_requires_active_by_default() -> None:
    verifier = CommandVerifier()
    cmd = DeviceCommandDefinition("tool.calibrate", lambda d, p: {}, allow_ready=False)
    device = SimulatedDevice("tool1", "USB:1")

    # UNREGISTERED -> not active
    with pytest.raises(DeviceNotActiveError, match="requires ACTIVE"):
        verifier.verify_command_authorization(device, cmd)

    # READY -> not active when allow_ready=False
    device._state = DeviceState.READY
    with pytest.raises(DeviceNotActiveError, match="requires ACTIVE"):
        verifier.verify_command_authorization(device, cmd)

    # ACTIVE -> authorized
    device._state = DeviceState.ACTIVE
    verifier.verify_command_authorization(device, cmd)


def test_command_allows_ready_when_configured() -> None:
    verifier = CommandVerifier()
    cmd = DeviceCommandDefinition("tool.prepare", lambda d, p: {}, allow_ready=True)
    device = SimulatedDevice("tool1", "USB:1")

    device._state = DeviceState.READY
    verifier.verify_command_authorization(device, cmd)

    device._state = DeviceState.ACTIVE
    verifier.verify_command_authorization(device, cmd)

    device._state = DeviceState.STOPPED
    with pytest.raises(DeviceNotActiveError, match="requires READY or ACTIVE"):
        verifier.verify_command_authorization(device, cmd)


def test_command_capability_authorization() -> None:
    verifier = CommandVerifier()
    cmd = DeviceCommandDefinition(
        "camera.zoom",
        lambda d, p: {},
        required_capability_id="camera.ptz",
    )
    # Device lacks capability
    dev_without_cap = SimulatedDevice("cam1", "USB:1")
    dev_without_cap._state = DeviceState.ACTIVE

    with pytest.raises(CapabilityUnauthorizedError, match="lacks required capability 'camera.ptz'"):
        verifier.verify_command_authorization(dev_without_cap, cmd)

    # Device possesses capability
    cap = DeviceCapability("camera.ptz", CapabilityCategory.CONTROL, {})
    dev_with_cap = SimulatedDevice("cam2", "USB:2", capabilities=(cap,))
    dev_with_cap._state = DeviceState.ACTIVE
    verifier.verify_command_authorization(dev_with_cap, cmd)


def test_query_state_authorization() -> None:
    verifier = CommandVerifier()
    query = DeviceQueryDefinition(
        "camera.info",
        lambda d, p: {},
        allowed_states=(DeviceState.REGISTERED, DeviceState.READY, DeviceState.ACTIVE),
    )
    device = SimulatedDevice("cam1", "USB:1")

    # In REGISTERED
    device._state = DeviceState.REGISTERED
    verifier.verify_query_authorization(device, query)

    # In ACTIVE
    device._state = DeviceState.ACTIVE
    verifier.verify_query_authorization(device, query)

    # In FAILED -> rejected
    device._state = DeviceState.FAILED
    with pytest.raises(DeviceStateError, match="not permitted for query"):
        verifier.verify_query_authorization(device, query)
