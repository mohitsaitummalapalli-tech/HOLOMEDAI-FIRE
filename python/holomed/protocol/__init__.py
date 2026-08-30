"""HoloMed AI - Protocol definitions, schemas, and codec."""

from holomed.protocol.builders import (
    create_command,
    create_error_response,
    create_event,
    create_query,
    create_response,
)
from holomed.protocol.codec import (
    deserialize_envelope,
    envelope_from_dict,
    envelope_to_dict,
    serialize_envelope,
    serialize_envelope_bytes,
)
from holomed.protocol.exceptions import (
    ProtocolDeserializationError,
    ProtocolError,
    ProtocolSerializationError,
    ProtocolValidationError,
    ProtocolVersionError,
)
from holomed.protocol.models import (
    CURRENT_PROTOCOL_VERSION,
    ErrorPayload,
    JSONObject,
    JSONPrimitive,
    JSONValue,
    MessageEnvelope,
    MessageType,
)
from holomed.protocol.validation import (
    MAX_ERROR_CODE_LENGTH,
    MAX_ERROR_MESSAGE_LENGTH,
    MAX_IDENTIFIER_LENGTH,
    MAX_NESTING_DEPTH,
    MAX_WIRE_SIZE_BYTES,
    REQUIRED_ENVELOPE_FIELDS,
    REQUIRED_ERROR_FIELDS,
    parse_protocol_version,
    validate_envelope,
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

__all__ = [
    # Models & Enums
    "CURRENT_PROTOCOL_VERSION",
    "MessageType",
    "ErrorPayload",
    "MessageEnvelope",
    "JSONPrimitive",
    "JSONValue",
    "JSONObject",
    # Exceptions
    "ProtocolError",
    "ProtocolValidationError",
    "ProtocolVersionError",
    "ProtocolSerializationError",
    "ProtocolDeserializationError",
    # Builders
    "create_command",
    "create_query",
    "create_event",
    "create_response",
    "create_error_response",
    # Codec
    "serialize_envelope",
    "serialize_envelope_bytes",
    "deserialize_envelope",
    "envelope_to_dict",
    "envelope_from_dict",
    # Validation & Constants
    "MAX_IDENTIFIER_LENGTH",
    "MAX_ERROR_CODE_LENGTH",
    "MAX_ERROR_MESSAGE_LENGTH",
    "MAX_NESTING_DEPTH",
    "MAX_WIRE_SIZE_BYTES",
    "REQUIRED_ENVELOPE_FIELDS",
    "REQUIRED_ERROR_FIELDS",
    "parse_protocol_version",
    "validate_envelope",
    "validate_envelope_dict",
    "validate_protocol_version",
    "validate_uuid_v4",
    "validate_uuid",
    "validate_timestamp_utc",
    "validate_identifier",
    "validate_error_code",
    "validate_json_value",
    "validate_error_payload_dict",
]
