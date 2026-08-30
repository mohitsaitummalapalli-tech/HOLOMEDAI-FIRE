"""HoloMed Protocol Validation Engine (Standard Library Implementation)."""

from datetime import datetime
import math
import re
from typing import Any, Dict, List, Optional, Set, Tuple

from holomed.protocol.exceptions import (
    ProtocolValidationError,
    ProtocolVersionError,
)
from holomed.protocol.models import (
    CURRENT_PROTOCOL_VERSION,
    ErrorPayload,
    MessageEnvelope,
    MessageType,
)

# Constants & Regular Expressions
UUID_V4_PATTERN = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$")
UUID_PATTERN = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
TIMESTAMP_UTC_PATTERN = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{6}Z$")
IDENTIFIER_PATTERN = re.compile(r"^[a-z0-9_]+(\.[a-z0-9_]+)*$")
ERROR_CODE_PATTERN = re.compile(r"^ERR_[A-Z0-9_]+$")
VERSION_PATTERN = re.compile(r"^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)$")

MAX_IDENTIFIER_LENGTH = 128
MAX_ERROR_CODE_LENGTH = 64
MAX_ERROR_MESSAGE_LENGTH = 1024
MAX_NESTING_DEPTH = 32
MAX_WIRE_SIZE_BYTES = 1048576  # 1 MiB

REQUIRED_ENVELOPE_FIELDS: Set[str] = {
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
}

REQUIRED_ERROR_FIELDS: Set[str] = {
    "error_code",
    "error_message",
    "details",
    "recoverable",
}


def parse_protocol_version(version: str) -> Tuple[int, int]:
    """Parse and validate canonical 'MAJOR.MINOR' version syntax without leading zeroes.

    Returns:
        Tuple of (major: int, minor: int)

    Raises:
        ProtocolVersionError: If the version is not a string or violates canonical syntax.
    """
    if not isinstance(version, str):
        raise ProtocolVersionError(f"protocol_version must be a string, got {type(version).__name__}")

    match = VERSION_PATTERN.match(version)
    if not match:
        raise ProtocolVersionError(
            f"Invalid protocol_version syntax: {version!r}. Must match 'MAJOR.MINOR' without leading zeroes."
        )

    return int(match.group(1)), int(match.group(2))


def validate_protocol_version(version: str) -> Tuple[int, int]:
    """Validate protocol version string syntax and compatibility.

    Compatibility rules:
    - Must match canonical 'MAJOR.MINOR' grammar without leading zeroes.
    - Major version must strictly match CURRENT_PROTOCOL_VERSION major.
    - Same major with minor <= current minor is fully compatible.
    - Same major with minor > current minor is accepted forward-compatibly
      provided the message adheres to the known envelope contract.
    - Different major version is rejected as incompatible.

    Returns:
        Tuple of (major: int, minor: int) upon successful validation.

    Raises:
        ProtocolVersionError: If version syntax or major compatibility is invalid.
    """
    major, minor = parse_protocol_version(version)
    current_major, current_minor = parse_protocol_version(CURRENT_PROTOCOL_VERSION)

    if major != current_major:
        raise ProtocolVersionError(
            f"Incompatible protocol version major {major}. Supported major version is {current_major}."
        )

    # Minor version is parsed and available. Both minor <= current_minor and minor > current_minor
    # under the same major are allowed to proceed to known-envelope validation.
    return major, minor


def validate_uuid_v4(val: str, field_name: str) -> None:
    """Validate that val is a canonical lowercase UUID version 4 string."""
    if not isinstance(val, str):
        raise ProtocolValidationError(f"{field_name} must be a string, got {type(val).__name__}")
    if not UUID_V4_PATTERN.match(val):
        raise ProtocolValidationError(
            f"{field_name} must be a canonical lowercase UUIDv4 (8-4-4-4-12 hex with version 4), got {val!r}"
        )


def validate_uuid(val: Optional[str], field_name: str, nullable: bool = False) -> None:
    """Validate that val is a canonical lowercase UUID string (any valid version)."""
    if val is None:
        if nullable:
            return
        raise ProtocolValidationError(f"{field_name} cannot be null")
    if not isinstance(val, str):
        raise ProtocolValidationError(f"{field_name} must be a string, got {type(val).__name__}")
    if not UUID_PATTERN.match(val):
        raise ProtocolValidationError(
            f"{field_name} must be a canonical lowercase UUID (8-4-4-4-12 hex), got {val!r}"
        )


def validate_timestamp_utc(val: str) -> None:
    """Validate ISO 8601 UTC timestamp format and calendar/clock semantics."""
    if not isinstance(val, str):
        raise ProtocolValidationError(f"timestamp_utc must be a string, got {type(val).__name__}")
    if not TIMESTAMP_UTC_PATTERN.match(val):
        raise ProtocolValidationError(
            f"timestamp_utc must match exact format 'YYYY-MM-DDTHH:MM:SS.ffffffZ', got {val!r}"
        )

    try:
        # Strict calendar parsing
        datetime.strptime(val, "%Y-%m-%dT%H:%M:%S.%fZ")
    except ValueError as e:
        raise ProtocolValidationError(f"timestamp_utc contains invalid calendar date or time: {val!r}") from e


def validate_identifier(val: Optional[str], field_name: str, nullable: bool = False) -> None:
    """Validate dot-separated lowercase identifier grammar and length."""
    if val is None:
        if nullable:
            return
        raise ProtocolValidationError(f"{field_name} cannot be null")
    if not isinstance(val, str):
        raise ProtocolValidationError(f"{field_name} must be a string, got {type(val).__name__}")
    if len(val) == 0:
        raise ProtocolValidationError(f"{field_name} cannot be empty")
    if len(val) > MAX_IDENTIFIER_LENGTH:
        raise ProtocolValidationError(
            f"{field_name} exceeds maximum length of {MAX_IDENTIFIER_LENGTH} characters (len={len(val)})"
        )
    if not IDENTIFIER_PATTERN.match(val):
        raise ProtocolValidationError(
            f"{field_name} must be lowercase dot-separated identifier ([a-z0-9_]+(.[a-z0-9_]+)*), got {val!r}"
        )


def validate_error_code(val: str) -> None:
    """Validate machine-readable error code syntax and length."""
    if not isinstance(val, str):
        raise ProtocolValidationError(f"error_code must be a string, got {type(val).__name__}")
    if len(val) > MAX_ERROR_CODE_LENGTH:
        raise ProtocolValidationError(
            f"error_code exceeds maximum length of {MAX_ERROR_CODE_LENGTH} characters (len={len(val)})"
        )
    if not ERROR_CODE_PATTERN.match(val):
        raise ProtocolValidationError(
            f"error_code must match pattern '^ERR_[A-Z0-9_]+$', got {val!r}"
        )


def validate_json_value(val: Any, path: str = "root", current_depth: int = 1, max_depth: int = MAX_NESTING_DEPTH) -> None:
    """Validate that val contains only standard JSON types and obeys max nesting depth."""
    if current_depth > max_depth:
        raise ProtocolValidationError(
            f"Maximum JSON nesting depth of {max_depth} exceeded at path '{path}' (depth={current_depth})"
        )

    if val is None:
        return

    # Critical: Check bool before int/float since bool is a subclass of int in Python
    if isinstance(val, bool):
        return

    if isinstance(val, (int, float)):
        if isinstance(val, float) and (math.isnan(val) or math.isinf(val)):
            raise ProtocolValidationError(f"Non-finite floating-point number (NaN/Infinity) forbidden at '{path}'")
        return

    if isinstance(val, str):
        return

    if isinstance(val, list):
        for idx, item in enumerate(val):
            validate_json_value(item, f"{path}[{idx}]", current_depth + 1, max_depth)
        return

    if isinstance(val, dict):
        for k, v in val.items():
            if not isinstance(k, str):
                raise ProtocolValidationError(f"Dictionary key at '{path}' must be a string, got {type(k).__name__}")
            validate_json_value(v, f"{path}.{k}", current_depth + 1, max_depth)
        return

    raise ProtocolValidationError(
        f"Unsupported non-JSON type {type(val).__name__} encountered at '{path}'"
    )


def validate_error_payload_dict(payload: Dict[str, Any], path: str = "payload") -> None:
    """Validate dictionary against ErrorPayload specification."""
    if not isinstance(payload, dict):
        raise ProtocolValidationError(f"{path} must be a dictionary for ERROR message_type")

    keys = set(payload.keys())
    missing = REQUIRED_ERROR_FIELDS - keys
    if missing:
        raise ProtocolValidationError(f"Missing required error fields in {path}: {sorted(missing)}")

    extra = keys - REQUIRED_ERROR_FIELDS
    if extra:
        raise ProtocolValidationError(f"Unknown additional fields in {path}: {sorted(extra)}")

    validate_error_code(payload["error_code"])

    msg = payload["error_message"]
    if not isinstance(msg, str) or len(msg) == 0:
        raise ProtocolValidationError("error_message must be a non-empty string")
    if len(msg) > MAX_ERROR_MESSAGE_LENGTH:
        raise ProtocolValidationError(f"error_message exceeds maximum length of {MAX_ERROR_MESSAGE_LENGTH} characters")

    details = payload["details"]
    if not isinstance(details, dict):
        raise ProtocolValidationError("error details must be a dictionary object")
    validate_json_value(details, f"{path}.details", current_depth=2, max_depth=MAX_NESTING_DEPTH)

    rec = payload["recoverable"]
    if not isinstance(rec, bool):
        raise ProtocolValidationError(f"recoverable must be a boolean, got {type(rec).__name__}")


def validate_envelope_dict(data: Dict[str, Any]) -> None:
    """Strictly validate a raw dictionary against the HoloMed MessageEnvelope specification."""
    if not isinstance(data, dict):
        raise ProtocolValidationError(f"Envelope must be a dictionary/object, got {type(data).__name__}")

    keys = set(data.keys())
    missing = REQUIRED_ENVELOPE_FIELDS - keys
    if missing:
        raise ProtocolValidationError(f"Missing required envelope fields: {sorted(missing)}")

    extra = keys - REQUIRED_ENVELOPE_FIELDS
    if extra:
        raise ProtocolValidationError(f"Unknown top-level envelope fields forbidden: {sorted(extra)}")

    # 1. Protocol Version
    validate_protocol_version(data["protocol_version"])

    # 2. Message ID
    validate_uuid_v4(data["message_id"], "message_id")

    # 3. Correlation ID
    validate_uuid(data["correlation_id"], "correlation_id", nullable=False)

    # 4. Causation ID
    validate_uuid(data["causation_id"], "causation_id", nullable=True)

    # 5. Message Type
    mtype_raw = data["message_type"]
    if type(mtype_raw) is not str:
        raise ProtocolValidationError(
            f"message_type in wire dictionary must be a string, got {type(mtype_raw).__name__}"
        )
    try:
        mtype = MessageType(mtype_raw)
    except ValueError as e:
        raise ProtocolValidationError(
            f"Unknown message_type: {mtype_raw!r}. Must be one of {[t.value for t in MessageType]}"
        ) from e

    # 6. Message Name
    validate_identifier(data["message_name"], "message_name", nullable=False)

    # 7. Source
    validate_identifier(data["source"], "source", nullable=False)

    # 8. Target
    validate_identifier(data["target"], "target", nullable=True)

    # 9. Timestamp UTC
    validate_timestamp_utc(data["timestamp_utc"])

    # 10. Payload
    payload = data["payload"]
    if not isinstance(payload, dict):
        raise ProtocolValidationError(f"payload must be a JSON object (dict), got {type(payload).__name__}")
    validate_json_value(payload, "payload", current_depth=2, max_depth=MAX_NESTING_DEPTH)

    # Conditional Error Schema Validation
    if mtype == MessageType.ERROR:
        validate_error_payload_dict(payload, "payload")

    # 11. Metadata
    metadata = data["metadata"]
    if not isinstance(metadata, dict):
        raise ProtocolValidationError(f"metadata must be a JSON object (dict), got {type(metadata).__name__}")
    validate_json_value(metadata, "metadata", current_depth=2, max_depth=MAX_NESTING_DEPTH)


def validate_envelope(envelope: MessageEnvelope) -> None:
    """Validate an instantiated MessageEnvelope instance."""
    if not isinstance(envelope, MessageEnvelope):
        raise ProtocolValidationError(f"Expected MessageEnvelope instance, got {type(envelope).__name__}")
    validate_envelope_dict(envelope.to_dict())
