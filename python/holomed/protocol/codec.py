"""HoloMed Protocol Serialization & Deserialization Engine."""

import json
from json.decoder import JSONArray, JSONObject, WHITESPACE
from typing import Any, Dict, List, Tuple, Union

from holomed.protocol.exceptions import (
    ProtocolDeserializationError,
    ProtocolSerializationError,
)
from holomed.protocol.models import MessageEnvelope, MessageType
from holomed.protocol.validation import (
    MAX_NESTING_DEPTH,
    MAX_WIRE_SIZE_BYTES,
    validate_envelope_dict,
)


def _reject_constant(val: str) -> None:
    """Rejection hook for non-standard JSON constants (NaN, Infinity, -Infinity)."""
    raise ProtocolDeserializationError(f"Forbidden non-finite JSON constant encountered: {val!r}")


def _duplicate_key_object_pairs_hook(pairs: List[Tuple[str, Any]]) -> Dict[str, Any]:
    """Dictionary constructor that rejects any duplicate JSON keys at all nesting depths."""
    result: Dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ProtocolDeserializationError(f"Duplicate JSON key forbidden: {key!r}")
        result[key] = value
    return result


class SafeJSONDecoder(json.JSONDecoder):
    """Depth-limiting and safety-enforcing JSONDecoder (standard library).

    Note: SafeJSONDecoder intentionally relies on CPython standard library JSON
    decoder internals (overriding parse_object/parse_array hooks and scanner construction)
    to enforce parser-level recursion depth protection without external dependencies.
    It is officially supported and tested against Python 3.14.x.
    """

    def __init__(self, *args: Any, max_depth: int = MAX_NESTING_DEPTH, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.max_depth = max_depth
        self.current_depth = 0

        orig_parse_object = self.parse_object
        orig_parse_array = self.parse_array

        def custom_parse_object(
            s_and_end: Tuple[str, int],
            strict: bool,
            scan_once: Any,
            object_hook: Any,
            object_pairs_hook: Any,
            memo: Any = None,
            _w: Any = WHITESPACE.match,
        ) -> Tuple[Dict[str, Any], int]:
            self.current_depth += 1
            if self.current_depth > self.max_depth:
                raise ProtocolDeserializationError(
                    f"Maximum JSON nesting depth of {self.max_depth} exceeded during parsing"
                )
            try:
                if memo is None:
                    return orig_parse_object(s_and_end, strict, scan_once, object_hook, object_pairs_hook)
                return orig_parse_object(s_and_end, strict, scan_once, object_hook, object_pairs_hook, memo)
            finally:
                self.current_depth -= 1

        def custom_parse_array(
            s_and_end: Tuple[str, int],
            scan_once: Any,
            _w: Any = WHITESPACE.match,
        ) -> Tuple[List[Any], int]:
            self.current_depth += 1
            if self.current_depth > self.max_depth:
                raise ProtocolDeserializationError(
                    f"Maximum JSON nesting depth of {self.max_depth} exceeded during parsing"
                )
            try:
                return orig_parse_array(s_and_end, scan_once)
            finally:
                self.current_depth -= 1

        self.parse_object = custom_parse_object
        self.parse_array = custom_parse_array
        self.scan_once = json.scanner.py_make_scanner(self)


def envelope_to_dict(envelope: MessageEnvelope) -> Dict[str, Any]:
    """Convert a MessageEnvelope instance to its canonical dictionary representation."""
    if not isinstance(envelope, MessageEnvelope):
        raise ProtocolSerializationError(f"Expected MessageEnvelope instance, got {type(envelope).__name__}")
    return envelope.to_dict()


def envelope_from_dict(data: Dict[str, Any]) -> MessageEnvelope:
    """Instantiate a MessageEnvelope from a dictionary after full validation."""
    validate_envelope_dict(data)

    mtype = MessageType(data["message_type"])

    return MessageEnvelope(
        protocol_version=data["protocol_version"],
        message_id=data["message_id"],
        correlation_id=data["correlation_id"],
        causation_id=data["causation_id"],
        message_type=mtype,
        message_name=data["message_name"],
        source=data["source"],
        target=data["target"],
        timestamp_utc=data["timestamp_utc"],
        payload=dict(data["payload"]),
        metadata=dict(data["metadata"]),
    )


def serialize_envelope(envelope: MessageEnvelope) -> str:
    """Deterministically serialize a MessageEnvelope to a compact JSON string."""
    if not isinstance(envelope, MessageEnvelope):
        raise ProtocolSerializationError(f"Expected MessageEnvelope instance, got {type(envelope).__name__}")

    data = envelope.to_dict()
    # Semantic & version validation errors are preserved directly
    validate_envelope_dict(data)

    try:
        serialized = json.dumps(
            data,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        )
    except (TypeError, ValueError) as e:
        raise ProtocolSerializationError(f"Serialization failed: {e}") from e

    encoded_bytes = serialized.encode("utf-8")
    if len(encoded_bytes) > MAX_WIRE_SIZE_BYTES:
        raise ProtocolSerializationError(
            f"Serialized message exceeds maximum wire size of {MAX_WIRE_SIZE_BYTES} bytes (actual={len(encoded_bytes)})"
        )
    return serialized


def serialize_envelope_bytes(envelope: MessageEnvelope) -> bytes:
    """Deterministically serialize a MessageEnvelope to UTF-8 bytes."""
    return serialize_envelope(envelope).encode("utf-8")


def deserialize_envelope(raw: Union[str, bytes]) -> MessageEnvelope:
    """Parse and strictly validate raw JSON string or UTF-8 bytes into a MessageEnvelope."""
    if isinstance(raw, bytes):
        if len(raw) > MAX_WIRE_SIZE_BYTES:
            raise ProtocolDeserializationError(
                f"Message payload exceeds maximum wire size of {MAX_WIRE_SIZE_BYTES} bytes (actual={len(raw)})"
            )
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError as e:
            raise ProtocolDeserializationError(f"Invalid UTF-8 byte stream: {e}") from e
    elif isinstance(raw, str):
        encoded = raw.encode("utf-8")
        if len(encoded) > MAX_WIRE_SIZE_BYTES:
            raise ProtocolDeserializationError(
                f"Message payload exceeds maximum wire size of {MAX_WIRE_SIZE_BYTES} bytes (actual={len(encoded)})"
            )
        text = raw
    else:
        raise ProtocolDeserializationError(f"Expected str or bytes for deserialization, got {type(raw).__name__}")

    decoder = SafeJSONDecoder(
        max_depth=MAX_NESTING_DEPTH,
        object_pairs_hook=_duplicate_key_object_pairs_hook,
        parse_constant=_reject_constant,
    )

    try:
        data = decoder.decode(text)
    except ProtocolDeserializationError:
        raise
    except json.JSONDecodeError as e:
        raise ProtocolDeserializationError(f"Malformed JSON document: {e}") from e

    if not isinstance(data, dict):
        raise ProtocolDeserializationError(f"Envelope must be a JSON object, got {type(data).__name__}")

    return envelope_from_dict(data)
