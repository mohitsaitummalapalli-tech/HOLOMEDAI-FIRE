"""Unit tests for M00.7 data plane models, receipts, and results."""

import pytest

from holomed.devices.data.exceptions import (
    DataPayloadValidationError,
    DeviceDataValidationError,
)
from holomed.devices.data.models import (
    DeviceData,
    DeviceDataKind,
    ProcessingReceipt,
    ProcessingResult,
    ProcessingStatus,
)
from holomed.devices.models import DeviceType


def test_device_data_valid_construction() -> None:
    data = DeviceData(
        device_id="cam1",
        physical_id="USB:1",
        device_type=DeviceType.RGB_CAMERA,
        kind=DeviceDataKind.OBSERVATION,
        sequence_number=10,
        timestamp_utc="2026-08-31T12:00:00.000000Z",
        payload={"frame_id": 1, "fps": 30.0},
        correlation_id="11111111-1111-4111-8111-111111111111",
    )
    assert data.device_id == "cam1"
    assert data.sequence_number == 10
    assert data.kind == DeviceDataKind.OBSERVATION
    assert data.payload["frame_id"] == 1


def test_device_data_invalid_fields() -> None:
    # Negative sequence number
    with pytest.raises(DeviceDataValidationError, match="sequence_number must be int in range"):
        DeviceData(
            device_id="cam1",
            physical_id="USB:1",
            device_type=DeviceType.RGB_CAMERA,
            kind=DeviceDataKind.OBSERVATION,
            sequence_number=-1,
            timestamp_utc="2026-08-31T12:00:00Z",
            payload={},
        )

    # Invalid correlation ID
    with pytest.raises(DeviceDataValidationError, match="valid lowercase UUIDv4"):
        DeviceData(
            device_id="cam1",
            physical_id="USB:1",
            device_type=DeviceType.RGB_CAMERA,
            kind=DeviceDataKind.OBSERVATION,
            sequence_number=1,
            timestamp_utc="2026-08-31T12:00:00Z",
            payload={},
            correlation_id="not-a-uuid",
        )


def test_processing_result_error_code_format() -> None:
    # Valid ERR_ code
    res = ProcessingResult(
        device_id="cam1",
        sequence_number=1,
        status=ProcessingStatus.FAILED,
        payload={},
        timestamp_utc="2026-08-31T12:00:00Z",
        error_code="ERR_DEVICE_NOT_FOUND",
        error_message="Device missing",
    )
    assert res.error_code == "ERR_DEVICE_NOT_FOUND"

    # Invalid error code without ERR_ prefix
    with pytest.raises(DeviceDataValidationError, match="error_code must match pattern"):
        ProcessingResult(
            device_id="cam1",
            sequence_number=1,
            status=ProcessingStatus.FAILED,
            payload={},
            timestamp_utc="2026-08-31T12:00:00Z",
            error_code="INVALID_CODE",
        )
