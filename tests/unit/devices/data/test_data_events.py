"""Unit tests for M00.7 data plane event emissions and sink failure isolation."""

import pytest

from holomed.devices.data.events import RecordingDeviceDataEventSink
from holomed.devices.data.models import DeviceData, DeviceDataKind
from holomed.devices.data.processor import DeviceDataProcessor
from holomed.devices.interfaces import RegistryAuthorityToken
from holomed.devices.models import DeviceState, DeviceType
from holomed.devices.registry import DeviceRegistry
from holomed.devices.simulated import SimulatedDevice
from tests.unit.devices.conftest import make_test_context


def make_item(device_id: str, seq: int) -> DeviceData:
    return DeviceData(
        device_id=device_id,
        physical_id=f"USB:{device_id}",
        device_type=DeviceType.RGB_CAMERA,
        kind=DeviceDataKind.OBSERVATION,
        sequence_number=seq,
        timestamp_utc="2026-08-31T12:00:00Z",
        payload={"val": seq},
    )


def test_event_emissions_for_accepted_and_processed() -> None:
    token = RegistryAuthorityToken()
    registry = DeviceRegistry(token)
    device = SimulatedDevice("cam1", "USB:cam1", device_type=DeviceType.RGB_CAMERA)
    registry.register(device, token)
    device._state = DeviceState.ACTIVE

    sink = RecordingDeviceDataEventSink()
    processor = DeviceDataProcessor(registry=registry, event_sink=sink)
    ctx = make_test_context(epoch_id=1)
    processor.initialize(ctx)
    processor.start()

    processor.register_processor(DeviceDataKind.OBSERVATION, lambda d: {"res": 1})

    processor.submit(make_item("cam1", 1))
    processor.process_next()

    topics = [e.message_name for e in sink.events]
    assert "device.data.accepted" in topics
    assert "device.data.processing.started" in topics
    assert "device.data.processed" in topics


def test_event_sink_failure_isolation() -> None:
    """D152: Event sink failures must not fail submission or processing."""
    token = RegistryAuthorityToken()
    registry = DeviceRegistry(token)
    device = SimulatedDevice("cam1", "USB:cam1", device_type=DeviceType.RGB_CAMERA)
    registry.register(device, token)
    device._state = DeviceState.ACTIVE

    class BrokenSink:
        def emit(self, event):
            raise RuntimeError("Database connection lost")

    processor = DeviceDataProcessor(registry=registry, event_sink=BrokenSink())
    ctx = make_test_context(epoch_id=1)
    processor.initialize(ctx)
    processor.start()

    processor.register_processor(DeviceDataKind.OBSERVATION, lambda d: {"res": 1})

    # Submission succeeds despite sink failure
    receipt = processor.submit(make_item("cam1", 1))
    assert receipt.sequence_number == 1
    assert processor._sink_errors_count == 1

    # Processing succeeds despite sink failure
    result = processor.process_next()
    assert result is not None
    assert result.status.value == "PROCESSED"
    assert processor._sink_errors_count == 3
