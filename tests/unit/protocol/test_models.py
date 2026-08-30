"""Unit tests for HoloMed protocol models, immutability, and enums."""

from dataclasses import FrozenInstanceError
import pytest

from holomed.protocol.models import (
    CURRENT_PROTOCOL_VERSION,
    ErrorPayload,
    MessageEnvelope,
    MessageType,
)


def test_protocol_version_constant():
    """Verify default protocol version is 1.0."""
    assert CURRENT_PROTOCOL_VERSION == "1.0"


def test_message_type_enum():
    """Verify all 5 message types are defined as expected string values."""
    assert MessageType.COMMAND.value == "COMMAND"
    assert MessageType.QUERY.value == "QUERY"
    assert MessageType.EVENT.value == "EVENT"
    assert MessageType.RESPONSE.value == "RESPONSE"
    assert MessageType.ERROR.value == "ERROR"
    assert len(MessageType) == 5


def test_error_payload_immutability_and_to_dict():
    """Verify ErrorPayload is frozen and converts to expected dictionary."""
    payload = ErrorPayload(
        error_code="ERR_TEST_FAILURE",
        error_message="Test failure occurred",
        details={"code": 123},
        recoverable=True,
    )

    assert payload.error_code == "ERR_TEST_FAILURE"
    assert payload.error_message == "Test failure occurred"
    assert payload.details == {"code": 123}
    assert payload.recoverable is True

    # Immutability check
    with pytest.raises(FrozenInstanceError):
        payload.error_code = "ERR_MUTATED"  # type: ignore

    d = payload.to_dict()
    assert d == {
        "error_code": "ERR_TEST_FAILURE",
        "error_message": "Test failure occurred",
        "details": {"code": 123},
        "recoverable": True,
    }


def test_message_envelope_immutability_and_to_dict():
    """Verify MessageEnvelope is frozen and converts to expected dictionary."""
    envelope = MessageEnvelope(
        protocol_version="1.0",
        message_id="10000000-0000-4000-8000-000000000001",
        correlation_id="10000000-0000-4000-8000-000000000001",
        causation_id=None,
        message_type=MessageType.COMMAND,
        message_name="device.camera.start",
        source="core.orchestrator",
        target="device.camera",
        timestamp_utc="2026-08-29T20:00:00.000000Z",
        payload={"fps": 60},
        metadata={"trace_id": "123"},
    )

    assert envelope.protocol_version == "1.0"
    assert envelope.message_type == MessageType.COMMAND
    assert envelope.causation_id is None

    # Immutability check
    with pytest.raises(FrozenInstanceError):
        envelope.message_name = "device.camera.stop"  # type: ignore

    d = envelope.to_dict()
    assert d["message_type"] == "COMMAND"
    assert d["payload"] == {"fps": 60}
    assert d["target"] == "device.camera"
