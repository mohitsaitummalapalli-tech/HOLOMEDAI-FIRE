"""Unit tests for M00.7 DeviceDataProcessor execution, validation, and queries."""

import pytest

from holomed.configuration.models import SecretString
from holomed.devices.data.exceptions import (
    DataProcessorCapacityError,
    DeviceDataNotFoundError,
    DeviceDataValidationError,
    DeviceIdentityMismatchError,
)
from holomed.devices.data.models import (
    DeviceData,
    DeviceDataKind,
    ProcessingStatus,
)
from holomed.devices.data.processor import DeviceDataProcessor
from holomed.devices.interfaces import RegistryAuthorityToken
from holomed.devices.models import DeviceState, DeviceType
from holomed.devices.registry import DeviceRegistry
from holomed.devices.simulated import SimulatedDevice
from holomed.runtime.logging import SecretFilter
from tests.unit.devices.conftest import make_test_context


def make_item(device_id: str, seq: int, kind: DeviceDataKind = DeviceDataKind.OBSERVATION, payload=None) -> DeviceData:
    return DeviceData(
        device_id=device_id,
        physical_id=f"USB:{device_id}",
        device_type=DeviceType.RGB_CAMERA,
        kind=kind,
        sequence_number=seq,
        timestamp_utc="2026-08-31T12:00:00Z",
        payload=payload or {"data": seq},
        correlation_id="11111111-1111-4111-8111-111111111111",
    )


def test_submit_and_process_successful_pipeline() -> None:
    token = RegistryAuthorityToken()
    registry = DeviceRegistry(token)
    device = SimulatedDevice("cam1", "USB:cam1", device_type=DeviceType.RGB_CAMERA)
    registry.register(device, token)
    device._state = DeviceState.ACTIVE

    processor = DeviceDataProcessor(registry=registry)
    ctx = make_test_context(epoch_id=1)
    processor.initialize(ctx)
    processor.start()

    def observation_handler(data):
        return {"processed_seq": data.sequence_number, "status": "ok"}

    processor.register_processor(DeviceDataKind.OBSERVATION, observation_handler)

    item = make_item("cam1", 1)
    receipt = processor.submit(item)
    assert receipt.status == ProcessingStatus.ACCEPTED
    assert processor.get_queue_depth() == 1

    result = processor.process_next()
    assert result is not None
    assert result.status == ProcessingStatus.PROCESSED
    assert result.device_id == "cam1"
    assert result.sequence_number == 1
    assert result.correlation_id == item.correlation_id
    assert result.payload["processed_seq"] == 1
    assert processor.get_queue_depth() == 0


def test_device_validation_and_identity_mismatch() -> None:
    token = RegistryAuthorityToken()
    registry = DeviceRegistry(token)
    device = SimulatedDevice("cam1", "USB:cam1", device_type=DeviceType.RGB_CAMERA)
    registry.register(device, token)
    device._state = DeviceState.ACTIVE

    processor = DeviceDataProcessor(registry=registry)
    ctx = make_test_context(epoch_id=1)
    processor.initialize(ctx)
    processor.start()

    # Unknown device -> DeviceDataNotFoundError
    unknown_item = DeviceData(
        device_id="nonexistent",
        physical_id="USB:unknown",
        device_type=DeviceType.RGB_CAMERA,
        kind=DeviceDataKind.OBSERVATION,
        sequence_number=1,
        timestamp_utc="2026-08-31T12:00:00Z",
        payload={},
    )
    with pytest.raises(DeviceDataNotFoundError, match="is not registered"):
        processor.submit(unknown_item)

    # Physical ID mismatch -> DeviceIdentityMismatchError
    mismatched_physical = DeviceData(
        device_id="cam1",
        physical_id="USB:wrong_port",
        device_type=DeviceType.RGB_CAMERA,
        kind=DeviceDataKind.OBSERVATION,
        sequence_number=1,
        timestamp_utc="2026-08-31T12:00:00Z",
        payload={},
    )
    with pytest.raises(DeviceIdentityMismatchError, match="Physical ID mismatch"):
        processor.submit(mismatched_physical)

    # Device type mismatch -> DeviceIdentityMismatchError
    mismatched_type = DeviceData(
        device_id="cam1",
        physical_id="USB:cam1",
        device_type=DeviceType.IMU_SENSOR,
        kind=DeviceDataKind.OBSERVATION,
        sequence_number=1,
        timestamp_utc="2026-08-31T12:00:00Z",
        payload={},
    )
    with pytest.raises(DeviceIdentityMismatchError, match="Device type mismatch"):
        processor.submit(mismatched_type)


def test_processor_exception_isolated_and_secret_redacted() -> None:
    token = RegistryAuthorityToken()
    registry = DeviceRegistry(token)
    device = SimulatedDevice("cam1", "USB:cam1", device_type=DeviceType.RGB_CAMERA)
    registry.register(device, token)
    device._state = DeviceState.ACTIVE

    filter_secrets = SecretFilter([SecretString("telemetry_secret_key_123")])
    processor = DeviceDataProcessor(registry=registry, secret_filter=filter_secrets)
    ctx = make_test_context(epoch_id=1)
    processor.initialize(ctx)
    processor.start()

    def crashing_processor(data):
        raise RuntimeError("Parsing failed with telemetry_secret_key_123 in buffer")

    processor.register_processor(DeviceDataKind.DIAGNOSTIC, crashing_processor)

    item = make_item("cam1", 1, kind=DeviceDataKind.DIAGNOSTIC)
    processor.submit(item)

    res = processor.process_next()
    assert res is not None
    assert res.status == ProcessingStatus.FAILED
    assert res.error_code == "ERR_PROCESSING_FAILURE"
    assert "telemetry_secret_key_123" not in res.error_message
    assert "<redacted>" in res.error_message
