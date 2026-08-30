"""Unit tests for HoloMed protocol deterministic codec and serialization fidelity."""

import pytest

from holomed.protocol.builders import create_command
from holomed.protocol.codec import (
    deserialize_envelope,
    envelope_from_dict,
    envelope_to_dict,
    serialize_envelope,
    serialize_envelope_bytes,
)
from holomed.protocol.exceptions import (
    ProtocolDeserializationError,
    ProtocolSerializationError,
)
from holomed.protocol.models import MessageEnvelope, MessageType


def test_deterministic_serialization_key_ordering():
    """Verify dictionaries with different key insertion orders produce identical serialized bytes."""
    envelope1 = MessageEnvelope(
        protocol_version="1.0",
        message_id="10000000-0000-4000-8000-000000000001",
        correlation_id="10000000-0000-4000-8000-000000000001",
        causation_id=None,
        message_type=MessageType.COMMAND,
        message_name="device.camera.start",
        source="core.orchestrator",
        target="device.camera",
        timestamp_utc="2026-08-29T20:00:00.000000Z",
        payload={"z_key": 1, "a_key": 2, "m_key": {"beta": 10, "alpha": 20}},
        metadata={"k2": "v2", "k1": "v1"},
    )

    envelope2 = MessageEnvelope(
        protocol_version="1.0",
        message_id="10000000-0000-4000-8000-000000000001",
        correlation_id="10000000-0000-4000-8000-000000000001",
        causation_id=None,
        message_type=MessageType.COMMAND,
        message_name="device.camera.start",
        source="core.orchestrator",
        target="device.camera",
        timestamp_utc="2026-08-29T20:00:00.000000Z",
        payload={"a_key": 2, "m_key": {"alpha": 20, "beta": 10}, "z_key": 1},
        metadata={"k1": "v1", "k2": "v2"},
    )

    bytes1 = serialize_envelope_bytes(envelope1)
    bytes2 = serialize_envelope_bytes(envelope2)

    assert bytes1 == bytes2
    # Ensure no trailing newline
    assert not bytes1.endswith(b"\n")
    assert not bytes1.endswith(b"\r")


def test_round_trip_serialization_fidelity():
    """Verify serialize -> deserialize yields an identical MessageEnvelope."""
    cmd = create_command(
        message_name="device.camera.start",
        source="core.orchestrator",
        target="device.camera",
        payload={"fps": 60, "resolution": [1920, 1080], "metadata_flag": True},
        metadata={"session_id": "sess_999"},
    )

    serialized_str = serialize_envelope(cmd)
    serialized_bytes = serialize_envelope_bytes(cmd)

    deserialized_from_str = deserialize_envelope(serialized_str)
    deserialized_from_bytes = deserialize_envelope(serialized_bytes)

    assert cmd == deserialized_from_str
    assert cmd == deserialized_from_bytes


def test_utf8_unicode_preservation_without_escaping():
    """Verify Unicode strings are preserved in UTF-8 without backslash u escaping."""
    cmd = create_command(
        message_name="device.camera.start",
        source="core.orchestrator",
        payload={"label": "カメラ", "symbol": "αβγ", "emoji": "🧠"},
    )

    serialized = serialize_envelope(cmd)
    assert "カメラ" in serialized
    assert "αβγ" in serialized
    assert "🧠" in serialized
    assert "\\u" not in serialized

    deserialized = deserialize_envelope(serialized)
    assert deserialized.payload["label"] == "カメラ"
    assert deserialized.payload["emoji"] == "🧠"


def test_rejection_of_nan_and_infinity_during_serialization():
    """Verify NaN and Infinity in envelope raise ProtocolValidationError during validation."""
    from holomed.protocol.exceptions import ProtocolValidationError

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
        payload={"invalid_val": float("nan")},
        metadata={},
    )

    with pytest.raises(ProtocolValidationError):
        serialize_envelope(envelope)


@pytest.mark.parametrize(
    "raw_json",
    [
        '{"protocol_version":"1.0","message_id":"10000000-0000-4000-8000-000000000001","correlation_id":"10000000-0000-4000-8000-000000000001","causation_id":null,"message_type":"COMMAND","message_name":"device.camera.start","source":"core.orchestrator","target":null,"timestamp_utc":"2026-08-29T20:00:00.000000Z","payload":{"val":NaN},"metadata":{}}',
        '{"protocol_version":"1.0","message_id":"10000000-0000-4000-8000-000000000001","correlation_id":"10000000-0000-4000-8000-000000000001","causation_id":null,"message_type":"COMMAND","message_name":"device.camera.start","source":"core.orchestrator","target":null,"timestamp_utc":"2026-08-29T20:00:00.000000Z","payload":{"val":Infinity},"metadata":{}}',
        '{"protocol_version":"1.0","message_id":"10000000-0000-4000-8000-000000000001","correlation_id":"10000000-0000-4000-8000-000000000001","causation_id":null,"message_type":"COMMAND","message_name":"device.camera.start","source":"core.orchestrator","target":null,"timestamp_utc":"2026-08-29T20:00:00.000000Z","payload":{"val":-Infinity},"metadata":{}}',
    ],
)
def test_rejection_of_nan_and_infinity_during_deserialization(raw_json: str):
    """Verify NaN, Infinity, -Infinity in raw input raise ProtocolDeserializationError via parse_constant."""
    with pytest.raises(ProtocolDeserializationError) as exc:
        deserialize_envelope(raw_json)
    assert "Forbidden non-finite JSON constant" in str(exc.value)


def test_serialize_envelope_preserves_validation_and_version_exceptions():
    """Verify serialize_envelope propagates ProtocolValidationError and ProtocolVersionError directly."""
    from holomed.protocol.exceptions import ProtocolValidationError, ProtocolVersionError

    # Invalid timestamp
    invalid_ts_env = MessageEnvelope(
        protocol_version="1.0",
        message_id="10000000-0000-4000-8000-000000000001",
        correlation_id="10000000-0000-4000-8000-000000000001",
        causation_id=None,
        message_type=MessageType.COMMAND,
        message_name="device.camera.start",
        source="core.orchestrator",
        target=None,
        timestamp_utc="INVALID_TIMESTAMP",
        payload={},
        metadata={},
    )
    with pytest.raises(ProtocolValidationError):
        serialize_envelope(invalid_ts_env)

    # Incompatible version major
    incompatible_ver_env = MessageEnvelope(
        protocol_version="2.0",
        message_id="10000000-0000-4000-8000-000000000001",
        correlation_id="10000000-0000-4000-8000-000000000001",
        causation_id=None,
        message_type=MessageType.COMMAND,
        message_name="device.camera.start",
        source="core.orchestrator",
        target=None,
        timestamp_utc="2026-08-29T20:00:00.000000Z",
        payload={},
        metadata={},
    )
    with pytest.raises(ProtocolVersionError):
        serialize_envelope(incompatible_ver_env)


def test_exact_wire_size_boundary_acceptance_and_rejection():
    """Verify serialized UTF-8 message of exactly 1,048,576 bytes is accepted, while 1,048,577 bytes is rejected."""
    from holomed.protocol.validation import MAX_WIRE_SIZE_BYTES

    # 1. Base envelope calculation
    base_env = MessageEnvelope(
        protocol_version="1.0",
        message_id="10000000-0000-4000-8000-000000000001",
        correlation_id="10000000-0000-4000-8000-000000000001",
        causation_id=None,
        message_type=MessageType.COMMAND,
        message_name="device.camera.start",
        source="core.orchestrator",
        target="device.camera",
        timestamp_utc="2026-08-29T20:00:00.000000Z",
        payload={"pad": ""},
        metadata={},
    )
    base_bytes = serialize_envelope_bytes(base_env)
    pad_len_exact = MAX_WIRE_SIZE_BYTES - len(base_bytes)

    # 2. Exactly 1,048,576 bytes
    exact_env = MessageEnvelope(
        protocol_version="1.0",
        message_id="10000000-0000-4000-8000-000000000001",
        correlation_id="10000000-0000-4000-8000-000000000001",
        causation_id=None,
        message_type=MessageType.COMMAND,
        message_name="device.camera.start",
        source="core.orchestrator",
        target="device.camera",
        timestamp_utc="2026-08-29T20:00:00.000000Z",
        payload={"pad": "A" * pad_len_exact},
        metadata={},
    )
    exact_bytes = serialize_envelope_bytes(exact_env)
    assert len(exact_bytes) == MAX_WIRE_SIZE_BYTES == 1048576

    # Verify deserialization accepts exact 1 MiB bytes
    deserialized_exact = deserialize_envelope(exact_bytes)
    assert deserialized_exact.payload["pad"] == "A" * pad_len_exact

    # 3. Exactly 1,048,577 bytes serialization
    overflow_env = MessageEnvelope(
        protocol_version="1.0",
        message_id="10000000-0000-4000-8000-000000000001",
        correlation_id="10000000-0000-4000-8000-000000000001",
        causation_id=None,
        message_type=MessageType.COMMAND,
        message_name="device.camera.start",
        source="core.orchestrator",
        target="device.camera",
        timestamp_utc="2026-08-29T20:00:00.000000Z",
        payload={"pad": "A" * (pad_len_exact + 1)},
        metadata={},
    )
    with pytest.raises(ProtocolSerializationError) as exc:
        serialize_envelope_bytes(overflow_env)
    assert "exceeds maximum wire size of 1048576 bytes (actual=1048577)" in str(exc.value)

    # 4. Exactly 1,048,577 bytes deserialization
    overflow_bytes = exact_bytes + b" "
    assert len(overflow_bytes) == 1048577
    with pytest.raises(ProtocolDeserializationError) as exc:
        deserialize_envelope(overflow_bytes)
    assert "exceeds maximum wire size of 1048576 bytes (actual=1048577)" in str(exc.value)
