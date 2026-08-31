"""Unit tests for M00.6 query execution and state validation."""

import pytest

from holomed.devices.control.manager import DeviceControlManager
from holomed.devices.interfaces import RegistryAuthorityToken
from holomed.devices.models import DeviceState
from holomed.devices.registry import DeviceRegistry
from holomed.devices.simulated import SimulatedDevice
from holomed.protocol.builders import create_query
from holomed.protocol.models import MessageType
from tests.unit.devices.conftest import make_test_context


def test_query_execution_success() -> None:
    token = RegistryAuthorityToken()
    registry = DeviceRegistry(token)
    device = SimulatedDevice("cam1", "USB:1")
    registry.register(device, token)
    device._state = DeviceState.ACTIVE

    manager = DeviceControlManager(registry=registry)
    ctx = make_test_context(epoch_id=1)
    manager.initialize(ctx)
    manager.start()

    def get_info(dev, params):
        return {"vendor": "Acme", "temperature_c": 36.5}

    manager.register_query("camera.get_info", get_info)

    query_envelope = create_query(
        message_name="device.query",
        source="dashboard",
        payload={"device_id": "cam1", "query": "camera.get_info"},
    )

    response = manager.handle_query(query_envelope)

    assert response.message_type == MessageType.RESPONSE
    assert response.target == "dashboard"
    assert response.payload["vendor"] == "Acme"
    assert response.payload["temperature_c"] == 36.5


def test_query_device_not_found() -> None:
    token = RegistryAuthorityToken()
    registry = DeviceRegistry(token)
    manager = DeviceControlManager(registry=registry)
    ctx = make_test_context(epoch_id=1)
    manager.initialize(ctx)
    manager.start()

    manager.register_query("camera.info", lambda d, p: {})

    query_envelope = create_query(
        message_name="device.query",
        source="dashboard",
        payload={"device_id": "unknown_dev", "query": "camera.info"},
    )

    response = manager.handle_query(query_envelope)
    assert response.message_type == MessageType.ERROR
    assert response.payload["error_code"] == "ERR_DEVICE_NOT_FOUND"


def test_query_not_found() -> None:
    token = RegistryAuthorityToken()
    registry = DeviceRegistry(token)
    device = SimulatedDevice("cam1", "USB:1")
    registry.register(device, token)
    device._state = DeviceState.ACTIVE

    manager = DeviceControlManager(registry=registry)
    ctx = make_test_context(epoch_id=1)
    manager.initialize(ctx)
    manager.start()

    query_envelope = create_query(
        message_name="device.query",
        source="dashboard",
        payload={"device_id": "cam1", "query": "unknown.query"},
    )

    response = manager.handle_query(query_envelope)
    assert response.message_type == MessageType.ERROR
    assert response.payload["error_code"] == "ERR_QUERY_NOT_FOUND"
