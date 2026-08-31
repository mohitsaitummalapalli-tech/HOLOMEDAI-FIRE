"""Adversarial stress testing, AST import audit, reentrancy guards, and capacity checks for M00.7."""

import ast
import os
import pytest

from holomed.devices.data.exceptions import (
    DataPayloadValidationError,
    DataProcessorCapacityError,
    DeviceDataCapacityError,
    DeviceDataLifecycleError,
    DeviceDataValidationError,
)
from holomed.devices.data.models import (
    DeviceData,
    DeviceDataKind,
    MAX_QUEUE_ITEMS,
    MAX_REGISTERED_PROCESSORS,
)
from holomed.devices.data.processor import DeviceDataProcessor
from holomed.devices.interfaces import RegistryAuthorityToken
from holomed.devices.models import DeviceState, DeviceType
from holomed.devices.registry import DeviceRegistry
from holomed.devices.simulated import SimulatedDevice
from tests.unit.devices.conftest import make_test_context

PROHIBITED_MODULES = {
    "socket",
    "asyncio",
    "threading",
    "multiprocessing",
    "subprocess",
    "cv2",
    "mediapipe",
    "pyaudio",
    "serial",
    "google.generativeai",
}


def test_ast_prohibited_imports_audit_data() -> None:
    """Verify zero prohibited imports across all python/holomed/devices/data/*.py files."""
    data_dir = os.path.join(os.path.dirname(__file__), "..", "..", "..", "..", "python", "holomed", "devices", "data")
    data_dir = os.path.abspath(data_dir)

    for root, _, files in os.walk(data_dir):
        for file in files:
            if file.endswith(".py"):
                file_path = os.path.join(root, file)
                with open(file_path, "r", encoding="utf-8") as f:
                    tree = ast.parse(f.read(), filename=file_path)

                for node in ast.walk(tree):
                    if isinstance(node, ast.Import):
                        for alias in node.names:
                            for prohibited in PROHIBITED_MODULES:
                                assert not (alias.name == prohibited or alias.name.startswith(f"{prohibited}.")), (
                                    f"Prohibited import '{alias.name}' in {file_path}"
                                )
                    elif isinstance(node, ast.ImportFrom):
                        if node.module:
                            for prohibited in PROHIBITED_MODULES:
                                assert not (node.module == prohibited or node.module.startswith(f"{prohibited}.")), (
                                    f"Prohibited from-import '{node.module}' in {file_path}"
                                )


def test_processor_registry_capacity_limit() -> None:
    token = RegistryAuthorityToken()
    registry = DeviceRegistry(token)
    processor = DeviceDataProcessor(registry=registry)

    # Kind has 6 items; duplicate kind rejected
    processor.register_processor(DeviceDataKind.OBSERVATION, lambda d: {})
    with pytest.raises(DataProcessorCapacityError, match="already registered"):
        processor.register_processor(DeviceDataKind.OBSERVATION, lambda d: {})


def test_reentrant_submit_rejected() -> None:
    token = RegistryAuthorityToken()
    registry = DeviceRegistry(token)
    device = SimulatedDevice("cam1", "USB:cam1", device_type=DeviceType.RGB_CAMERA)
    registry.register(device, token)
    device._state = DeviceState.ACTIVE

    processor = DeviceDataProcessor(registry=registry)
    ctx = make_test_context(epoch_id=1)
    processor.initialize(ctx)
    processor.start()

    # Processor attempting to submit recursively
    def evil_recursive_processor(data):
        item2 = DeviceData(
            device_id="cam1",
            physical_id="USB:cam1",
            device_type=DeviceType.RGB_CAMERA,
            kind=DeviceDataKind.STATUS,
            sequence_number=999,
            timestamp_utc="2026-08-31T12:00:00Z",
            payload={},
        )
        processor.submit(item2)
        return {"ok": True}

    processor.register_processor(DeviceDataKind.OBSERVATION, evil_recursive_processor)

    item1 = DeviceData(
        device_id="cam1",
        physical_id="USB:cam1",
        device_type=DeviceType.RGB_CAMERA,
        kind=DeviceDataKind.OBSERVATION,
        sequence_number=1,
        timestamp_utc="2026-08-31T12:00:00Z",
        payload={},
    )
    processor.submit(item1)

    # process_next runs evil processor which attempts submit() -> trapped as FAILED with DeviceDataLifecycleError
    res = processor.process_next()
    assert res is not None
    assert res.status.value == "FAILED"
    assert "Reentrant submit()" in res.error_message


def test_query_methods_return_immutable_views() -> None:
    token = RegistryAuthorityToken()
    registry = DeviceRegistry(token)
    processor = DeviceDataProcessor(registry=registry)

    stats = processor.get_processing_stats()
    # Cannot mutate mappingproxy
    with pytest.raises(TypeError):
        stats["submitted"] = 999  # type: ignore

    dev_stats = processor.get_device_stats("cam1")
    with pytest.raises(TypeError):
        dev_stats["submitted"] = 999  # type: ignore


def test_unregistered_devices_do_not_allocate_statistics() -> None:
    token = RegistryAuthorityToken()
    registry = DeviceRegistry(token)
    processor = DeviceDataProcessor(registry=registry)
    ctx = make_test_context(epoch_id=1)
    processor.initialize(ctx)
    processor.start()

    for i in range(10):
        item = DeviceData(
            device_id=f"ghost_{i}",
            physical_id="USB:ghost",
            device_type=DeviceType.RGB_CAMERA,
            kind=DeviceDataKind.OBSERVATION,
            sequence_number=1,
            timestamp_utc="2026-08-31T12:00:00Z",
            payload={},
        )
        with pytest.raises(Exception):
            processor.submit(item)

    # None were allocated in per-device stats (D154)
    assert len(processor._device_stats) == 0


def test_dispatcher_submit_command_and_query_routes() -> None:
    token = RegistryAuthorityToken()
    registry = DeviceRegistry(token)
    device = SimulatedDevice("cam1", "USB:cam1", device_type=DeviceType.RGB_CAMERA)
    registry.register(device, token)
    device._state = DeviceState.ACTIVE

    processor = DeviceDataProcessor(registry=registry)
    ctx = make_test_context(epoch_id=1)
    processor.initialize(ctx)
    processor.start()

    from holomed.protocol.builders import create_command, create_query
    from holomed.protocol.models import MessageType

    # Submit Command
    cmd_envelope = create_command(
        message_name="device.data.submit",
        source="client",
        payload={
            "device_id": "cam1",
            "physical_id": "USB:cam1",
            "device_type": DeviceType.RGB_CAMERA,
            "kind": "OBSERVATION",
            "sequence_number": 1,
            "payload": {"val": 100},
        },
    )
    res = processor.handle_submit_command(cmd_envelope)
    assert res.message_type == MessageType.RESPONSE
    assert res.payload["status"] == "ACCEPTED"
    assert res.payload["sequence_number"] == 1

    # Query Route
    q_envelope = create_query(
        message_name="device.data.query",
        source="client",
        payload={"action": "get_queue_depth"},
    )
    q_res = processor.handle_query(q_envelope)
    assert q_res.message_type == MessageType.RESPONSE
    assert q_res.payload["queue_depth"] == 1
