"""Unit tests for M00.7 data payload deep freezing and limits."""

import pytest

from holomed.devices.data.exceptions import DataPayloadValidationError
from holomed.devices.data.models import DeviceData, DeviceDataKind
from holomed.devices.models import DeviceType


def test_payload_deep_freezing_and_nfc() -> None:
    # Non-NFC unicode e + acute
    non_nfc = "e\u0301"
    data = DeviceData(
        device_id="cam1",
        physical_id="USB:1",
        device_type=DeviceType.RGB_CAMERA,
        kind=DeviceDataKind.OBSERVATION,
        sequence_number=1,
        timestamp_utc="2026-08-31T12:00:00Z",
        payload={"name": non_nfc, "zero": -0.0},
    )
    # NFC normalization produces single codepoint \u00e9
    assert data.payload["name"] == "\u00e9"
    # -0.0 normalized to 0.0
    assert data.payload["zero"] == 0.0


def test_payload_nan_and_infinity_rejected() -> None:
    with pytest.raises(DataPayloadValidationError):
        DeviceData(
            device_id="cam1",
            physical_id="USB:1",
            device_type=DeviceType.RGB_CAMERA,
            kind=DeviceDataKind.OBSERVATION,
            sequence_number=1,
            timestamp_utc="2026-08-31T12:00:00Z",
            payload={"val": float("nan")},
        )

    with pytest.raises(DataPayloadValidationError):
        DeviceData(
            device_id="cam1",
            physical_id="USB:1",
            device_type=DeviceType.RGB_CAMERA,
            kind=DeviceDataKind.OBSERVATION,
            sequence_number=1,
            timestamp_utc="2026-08-31T12:00:00Z",
            payload={"val": float("inf")},
        )


def test_payload_cyclic_reference_rejected() -> None:
    cyclic_dict = {}
    cyclic_dict["self"] = cyclic_dict
    with pytest.raises(DataPayloadValidationError):
        DeviceData(
            device_id="cam1",
            physical_id="USB:1",
            device_type=DeviceType.RGB_CAMERA,
            kind=DeviceDataKind.OBSERVATION,
            sequence_number=1,
            timestamp_utc="2026-08-31T12:00:00Z",
            payload=cyclic_dict,
        )
