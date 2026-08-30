"""Unit tests for HoloMed protocol edge cases, safety bounds, and duplicate key rejection."""

import pytest

from holomed.protocol.builders import create_command, create_event
from holomed.protocol.codec import (
    deserialize_envelope,
    serialize_envelope,
    serialize_envelope_bytes,
)
from holomed.protocol.exceptions import (
    ProtocolDeserializationError,
    ProtocolSerializationError,
    ProtocolValidationError,
)
from holomed.protocol.validation import (
    MAX_NESTING_DEPTH,
    MAX_WIRE_SIZE_BYTES,
    validate_envelope,
)


def test_duplicate_key_at_top_level_rejected():
    """Verify duplicate keys at envelope root raise ProtocolDeserializationError."""
    json_str = (
        '{"protocol_version":"1.0","protocol_version":"1.0",'
        '"message_id":"10000000-0000-4000-8000-000000000001",'
        '"correlation_id":"10000000-0000-4000-8000-000000000001",'
        '"causation_id":null,"message_type":"COMMAND","message_name":"device.camera.start",'
        '"source":"core.orchestrator","target":null,'
        '"timestamp_utc":"2026-08-29T20:00:00.000000Z","payload":{},"metadata":{}}'
    )
    with pytest.raises(ProtocolDeserializationError) as exc:
        deserialize_envelope(json_str)
    assert "Duplicate JSON key forbidden: 'protocol_version'" in str(exc.value)


def test_duplicate_key_in_nested_payload_rejected():
    """Verify duplicate keys within nested payload objects raise ProtocolDeserializationError."""
    json_str = (
        '{"protocol_version":"1.0",'
        '"message_id":"10000000-0000-4000-8000-000000000001",'
        '"correlation_id":"10000000-0000-4000-8000-000000000001",'
        '"causation_id":null,"message_type":"COMMAND","message_name":"device.camera.start",'
        '"source":"core.orchestrator","target":null,'
        '"timestamp_utc":"2026-08-29T20:00:00.000000Z",'
        '"payload":{"fps":30,"fps":60},"metadata":{}}'
    )
    with pytest.raises(ProtocolDeserializationError) as exc:
        deserialize_envelope(json_str)
    assert "Duplicate JSON key forbidden: 'fps'" in str(exc.value)


def test_nesting_depth_bounds():
    """Verify nesting depth of exactly 32 is accepted, while depth 33 is rejected (builder & validator)."""
    # Depth calculation:
    # Depth 1: Envelope root object
    # Depth 2: payload object
    # Depth 3..31: 29 nested dictionaries
    # Depth 32: leaf primitive 'val' (1 + 1 + 29 + 1 = 32)
    valid_payload = {"val": 1}
    for _ in range(29):
        valid_payload = {"nested": valid_payload}

    cmd_valid = create_command(
        message_name="device.camera.start",
        source="core.orchestrator",
        payload=valid_payload,
    )
    validate_envelope(cmd_valid)

    # Nesting one more level pushes leaf primitive to depth 33 (1 + 1 + 30 + 1 = 33)
    invalid_payload = {"nested": valid_payload}
    with pytest.raises(ProtocolValidationError) as exc:
        create_command(
            message_name="device.camera.start",
            source="core.orchestrator",
            payload=invalid_payload,
        )
    assert "Maximum JSON nesting depth of 32 exceeded" in str(exc.value)


def test_nesting_depth_applies_to_metadata_and_error_details():
    """Verify depth limit of 32 applies equally to metadata and error details."""
    from holomed.protocol.builders import create_error_response

    valid_nested = {"val": 1}
    for _ in range(29):
        valid_nested = {"nested": valid_nested}

    # Metadata at depth 32 is valid
    cmd_meta_valid = create_command(
        message_name="device.camera.start",
        source="core.orchestrator",
        metadata=valid_nested,
    )
    validate_envelope(cmd_meta_valid)

    # Metadata at depth 33 is rejected
    invalid_nested = {"nested": valid_nested}
    with pytest.raises(ProtocolValidationError):
        create_command(
            message_name="device.camera.start",
            source="core.orchestrator",
            metadata=invalid_nested,
        )

    # Error details at depth 33 is rejected
    with pytest.raises(ProtocolValidationError):
        create_error_response(
            request=cmd_meta_valid,
            responder_source="device.camera",
            error_code="ERR_DEPTH_TEST",
            error_message="Test message",
            details=invalid_nested,
        )


def test_parser_depth_rejection_during_deserialization():
    """Verify SafeJSONDecoder rejects deeply nested raw JSON at depth 33 before building objects."""
    # Construct raw JSON document with depth 32 vs depth 33
    doc32 = '{"val":1}'
    for _ in range(31):
        doc32 = f'{{"nested":{doc32}}}'

    # Build valid envelope string containing depth 32 structure
    # Envelope (depth 1) -> payload (depth 2) -> 29 dicts (depth 3..31) -> {"val": 1} (depth 32)
    valid_env_json = (
        '{"causation_id":null,"correlation_id":"10000000-0000-4000-8000-000000000001",'
        '"message_id":"10000000-0000-4000-8000-000000000001","message_name":"device.camera.start",'
        '"message_type":"COMMAND","metadata":{},"payload":'
        f'{doc32},'
        '"protocol_version":"1.0","source":"core.orchestrator","target":null,"timestamp_utc":"2026-08-29T20:00:00.000000Z"}'
    )
    # Depth 33 in raw JSON (root is depth 1 + 32 levels = 33)
    with pytest.raises(ProtocolDeserializationError) as exc:
        deserialize_envelope(valid_env_json)
    assert "Maximum JSON nesting depth of 32 exceeded during parsing" in str(exc.value)


def test_wire_size_limit_bounds():
    """Verify wire payloads up to 1 MiB are allowed, and payloads exceeding 1 MiB are rejected."""
    # 1. Payload size ~ 500 KiB (within 1 MiB limit)
    safe_data = "x" * 500000
    cmd_safe = create_command(
        message_name="device.camera.start",
        source="core.orchestrator",
        payload={"data": safe_data},
    )
    serialized = serialize_envelope_bytes(cmd_safe)
    assert len(serialized) <= MAX_WIRE_SIZE_BYTES
    deserialized = deserialize_envelope(serialized)
    assert deserialized.payload["data"] == safe_data

    # 2. Payload size exceeding 1 MiB (1,048,576 bytes)
    overflow_data = "x" * (MAX_WIRE_SIZE_BYTES + 100)
    cmd_overflow = create_command(
        message_name="device.camera.start",
        source="core.orchestrator",
        payload={"data": overflow_data},
    )

    with pytest.raises(ProtocolSerializationError) as exc:
        serialize_envelope_bytes(cmd_overflow)
    assert "exceeds maximum wire size of 1048576 bytes" in str(exc.value)

    # Direct deserialization size check
    overflow_bytes = b"x" * (MAX_WIRE_SIZE_BYTES + 100)
    with pytest.raises(ProtocolDeserializationError) as exc:
        deserialize_envelope(overflow_bytes)
    assert "exceeds maximum wire size of 1048576 bytes" in str(exc.value)


def test_null_target_event_handling():
    """Verify unaddressed broadcast events with target=None serialize cleanly."""
    evt = create_event(
        message_name="device.camera.frame_ready",
        source="device.camera",
        target=None,
        payload={"frame_id": 10},
    )

    serialized = serialize_envelope(evt)
    assert '"target":null' in serialized

    deserialized = deserialize_envelope(serialized)
    assert deserialized.target is None
