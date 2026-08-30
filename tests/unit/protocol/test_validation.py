"""Unit tests for HoloMed protocol validation rules and exception handling."""

import pytest

from holomed.protocol.exceptions import (
    ProtocolValidationError,
    ProtocolVersionError,
)
from holomed.protocol.models import MessageType
from holomed.protocol.validation import (
    validate_envelope_dict,
    validate_error_code,
    validate_error_payload_dict,
    validate_identifier,
    validate_json_value,
    validate_protocol_version,
    validate_timestamp_utc,
    validate_uuid,
    validate_uuid_v4,
)


def get_valid_envelope_dict():
    """Return a baseline valid envelope dictionary."""
    return {
        "protocol_version": "1.0",
        "message_id": "10000000-0000-4000-8000-000000000001",
        "correlation_id": "10000000-0000-4000-8000-000000000001",
        "causation_id": None,
        "message_type": "COMMAND",
        "message_name": "device.camera.start",
        "source": "core.orchestrator",
        "target": "device.camera",
        "timestamp_utc": "2026-08-29T20:00:00.000000Z",
        "payload": {"fps": 60, "enabled": True, "name": "cam0", "scale": 1.5, "extra": None},
        "metadata": {"trace": "abc"},
    }


def test_validate_valid_envelope_dict():
    """Verify standard valid envelope dictionary passes validation."""
    data = get_valid_envelope_dict()
    validate_envelope_dict(data)


@pytest.mark.parametrize(
    "missing_field",
    [
        "protocol_version",
        "message_id",
        "correlation_id",
        "causation_id",
        "message_type",
        "message_name",
        "source",
        "target",
        "timestamp_utc",
        "payload",
        "metadata",
    ],
)
def test_missing_required_fields(missing_field: str):
    """Verify omission of any required field raises ProtocolValidationError."""
    data = get_valid_envelope_dict()
    del data[missing_field]
    with pytest.raises(ProtocolValidationError) as exc:
        validate_envelope_dict(data)
    assert f"Missing required envelope fields" in str(exc.value)


def test_unknown_top_level_envelope_fields_rejected():
    """Verify unknown envelope keys raise ProtocolValidationError."""
    data = get_valid_envelope_dict()
    data["unknown_extension"] = "not_allowed"
    with pytest.raises(ProtocolValidationError) as exc:
        validate_envelope_dict(data)
    assert "Unknown top-level envelope fields forbidden" in str(exc.value)


@pytest.mark.parametrize(
    "invalid_id",
    [
        "10000000-0000-1000-8000-000000000001",  # UUIDv1 instead of UUIDv4
        "10000000-0000-4000-8000-00000000000Z",  # Invalid hex char
        "10000000-0000-4000-8000-00000000000A",  # Uppercase hex
        "{10000000-0000-4000-8000-000000000001}",  # Braces
        "10000000000040008000000000000001",  # Compact without hyphens
        "",
        123,
    ],
)
def test_invalid_uuid_v4(invalid_id):
    """Verify non-UUIDv4 message_ids are rejected."""
    with pytest.raises(ProtocolValidationError):
        validate_uuid_v4(invalid_id, "message_id")  # type: ignore


@pytest.mark.parametrize(
    "invalid_ts",
    [
        "2026-08-29T20:00:00Z",  # Missing microseconds
        "2026-08-29T20:00:00.000Z",  # 3-digit milliseconds instead of 6-digit microseconds
        "2026-08-29T20:00:00.000000+00:00",  # Timezone offset instead of Z
        "2026-08-29 20:00:00.000000Z",  # Space instead of T
        "2026-02-31T20:00:00.000000Z",  # Impossible calendar date
        "2026-08-29T25:00:00.000000Z",  # Impossible hour
        "",
        None,
    ],
)
def test_invalid_timestamp_utc(invalid_ts):
    """Verify malformed and semantically invalid timestamps are rejected."""
    with pytest.raises(ProtocolValidationError):
        validate_timestamp_utc(invalid_ts)  # type: ignore


@pytest.mark.parametrize(
    "invalid_id",
    [
        "Device.Camera",  # Uppercase
        "device camera",  # Spaces
        "device-camera",  # Hyphens
        ".device.camera",  # Leading dot
        "device.camera.",  # Trailing dot
        "device..camera",  # Consecutive dots
        "a" * 129,  # Exceeds max length (128)
        "",  # Empty
    ],
)
def test_invalid_identifiers(invalid_id: str):
    """Verify invalid identifier syntax and length are rejected."""
    with pytest.raises(ProtocolValidationError):
        validate_identifier(invalid_id, "message_name")


@pytest.mark.parametrize(
    "invalid_code",
    [
        "err_timeout",  # Lowercase
        "TIMEOUT",  # Missing ERR_ prefix
        "ERR_invalid-code",  # Hyphens
        "ERR_" + ("A" * 61),  # Exceeds max length (64)
        "",
    ],
)
def test_invalid_error_code(invalid_code: str):
    """Verify invalid error code patterns are rejected."""
    with pytest.raises(ProtocolValidationError):
        validate_error_code(invalid_code)


@pytest.mark.parametrize(
    "invalid_version",
    [
        "01.0",  # Leading zero
        "1.00",  # Leading zero in minor
        "00.01",  # Leading zero in both
        "1",  # Missing minor
        "v1.0",  # Prefix
        "1.0.0",  # Patch segment not allowed
        "latest",
        "",
        None,
        123,
    ],
)
def test_invalid_protocol_version_syntax(invalid_version):
    """Verify malformed version strings raise ProtocolVersionError."""
    with pytest.raises(ProtocolVersionError):
        validate_protocol_version(invalid_version)  # type: ignore


def test_parse_protocol_version_success_and_errors():
    """Verify parse_protocol_version extracts major and minor integers accurately."""
    from holomed.protocol.validation import parse_protocol_version

    assert parse_protocol_version("1.0") == (1, 0)
    assert parse_protocol_version("1.5") == (1, 5)
    assert parse_protocol_version("2.10") == (2, 10)
    assert parse_protocol_version("0.1") == (0, 1)

    with pytest.raises(ProtocolVersionError):
        parse_protocol_version("01.0")
    with pytest.raises(ProtocolVersionError):
        parse_protocol_version("1.0.0")


@pytest.mark.parametrize(
    "valid_version,expected_major,expected_minor",
    [
        ("1.0", 1, 0),    # Equal to baseline
        ("1.1", 1, 1),    # Higher minor
        ("1.5", 1, 5),    # Higher minor
        ("1.99", 1, 99),  # Higher minor
    ],
)
def test_valid_and_forward_compatible_protocol_versions(valid_version: str, expected_major: int, expected_minor: int):
    """Verify current minor and forward-compatible higher minors under major 1 are accepted and parsed."""
    major, minor = validate_protocol_version(valid_version)
    assert major == expected_major
    assert minor == expected_minor


@pytest.mark.parametrize(
    "incompatible_version",
    [
        "0.1",
        "0.9",
        "2.0",
        "2.1",
        "3.0",
    ],
)
def test_incompatible_major_protocol_versions(incompatible_version: str):
    """Verify different major versions raise ProtocolVersionError."""
    with pytest.raises(ProtocolVersionError) as exc:
        validate_protocol_version(incompatible_version)
    assert "Incompatible protocol version major" in str(exc.value)


def test_wire_dictionary_rejects_enum_object_for_message_type():
    """Verify raw wire dictionary requires message_type to be a string, not enum instance."""
    data = get_valid_envelope_dict()
    data["message_type"] = MessageType.COMMAND  # Enum instead of str
    with pytest.raises(ProtocolValidationError) as exc:
        validate_envelope_dict(data)
    assert "message_type in wire dictionary must be a string" in str(exc.value)


def test_error_message_type_requires_valid_error_payload():
    """Verify message_type=ERROR strictly validates ErrorPayload structure."""
    data = get_valid_envelope_dict()
    data["message_type"] = "ERROR"
    # Invalid payload (missing error fields)
    data["payload"] = {"status": "failed"}

    with pytest.raises(ProtocolValidationError) as exc:
        validate_envelope_dict(data)
    assert "Missing required error fields" in str(exc.value)

    # Valid ErrorPayload
    data["payload"] = {
        "error_code": "ERR_FAILED",
        "error_message": "Operation failed",
        "details": {"reason": "timeout"},
        "recoverable": False,
    }
    validate_envelope_dict(data)


def test_json_typing_evaluates_bool_before_numeric():
    """Verify booleans are recognized as boolean JSON primitives, not integers."""
    validate_json_value(True, "test_bool")
    validate_json_value(False, "test_bool")
    validate_json_value(42, "test_int")
    validate_json_value(3.14, "test_float")

    # Non-finite float numbers
    with pytest.raises(ProtocolValidationError):
        validate_json_value(float("nan"), "test_nan")
    with pytest.raises(ProtocolValidationError):
        validate_json_value(float("inf"), "test_inf")
