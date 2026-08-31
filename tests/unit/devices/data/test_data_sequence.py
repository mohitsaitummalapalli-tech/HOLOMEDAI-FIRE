"""Unit tests for M00.7 monotonic sequence enforcement and session resets."""

import pytest

from holomed.devices.data.exceptions import (
    DuplicateDataSequenceError,
    OutOfOrderDataError,
)
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


def test_monotonic_sequence_enforcement() -> None:
    token = RegistryAuthorityToken()
    registry = DeviceRegistry(token)
    device = SimulatedDevice("cam1", "USB:cam1", device_type=DeviceType.RGB_CAMERA)
    registry.register(device, token)
    device._state = DeviceState.ACTIVE

    processor = DeviceDataProcessor(registry=registry)
    ctx = make_test_context(epoch_id=1)
    processor.initialize(ctx)
    processor.start()

    # First item: 10 (valid)
    receipt1 = processor.submit(make_item("cam1", 10))
    assert receipt1.sequence_number == 10

    # Next item: 15 (valid strictly increasing)
    receipt2 = processor.submit(make_item("cam1", 15))
    assert receipt2.sequence_number == 15

    # Duplicate sequence: 15 -> DuplicateDataSequenceError
    with pytest.raises(DuplicateDataSequenceError, match="Duplicate sequence number 15"):
        processor.submit(make_item("cam1", 15))

    # Out-of-order sequence: 12 < 15 -> OutOfOrderDataError
    with pytest.raises(OutOfOrderDataError, match="Out-of-order sequence number 12 < 15"):
        processor.submit(make_item("cam1", 12))


def test_sequence_resets_on_device_session_reset() -> None:
    token = RegistryAuthorityToken()
    registry = DeviceRegistry(token)
    device = SimulatedDevice("cam1", "USB:cam1", device_type=DeviceType.RGB_CAMERA)
    registry.register(device, token)
    device._state = DeviceState.ACTIVE

    processor = DeviceDataProcessor(registry=registry)
    ctx = make_test_context(epoch_id=1)
    processor.initialize(ctx)
    processor.start()

    processor.submit(make_item("cam1", 50))
    assert processor.get_last_sequence("cam1") == 50

    # Device session reset (D148)
    processor.reset_device_sequence("cam1")
    assert processor.get_last_sequence("cam1") is None

    # Lower sequence number 5 now accepted as new session first item
    receipt = processor.submit(make_item("cam1", 5))
    assert receipt.sequence_number == 5
