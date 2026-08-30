"""HoloMed Protocol Semantic Builders & Factory Functions."""

from datetime import datetime, timezone
from typing import Any, Dict, Optional
import uuid

from holomed.protocol.exceptions import ProtocolValidationError
from holomed.protocol.models import (
    CURRENT_PROTOCOL_VERSION,
    ErrorPayload,
    MessageEnvelope,
    MessageType,
)
from holomed.protocol.validation import validate_envelope


def _generate_uuid_v4() -> str:
    """Generate a lowercase canonical UUIDv4 string."""
    return str(uuid.uuid4())


def _generate_timestamp_utc() -> str:
    """Generate current UTC ISO 8601 timestamp with 6-digit microsecond precision and Z suffix."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _normalize_dict_input(val: Optional[Dict[str, Any]], field_name: str) -> Dict[str, Any]:
    """Explicitly normalize and validate dictionary inputs without implicit truthiness coercion."""
    if val is None:
        return {}
    if isinstance(val, dict):
        return dict(val)
    raise ProtocolValidationError(f"{field_name} must be a dictionary or None, got {type(val).__name__}")


def create_command(
    message_name: str,
    source: str,
    target: Optional[str] = None,
    payload: Optional[Dict[str, Any]] = None,
    metadata: Optional[Dict[str, Any]] = None,
    protocol_version: str = CURRENT_PROTOCOL_VERSION,
) -> MessageEnvelope:
    """Construct and validate a root COMMAND envelope."""
    msg_id = _generate_uuid_v4()
    envelope = MessageEnvelope(
        protocol_version=protocol_version,
        message_id=msg_id,
        correlation_id=msg_id,
        causation_id=None,
        message_type=MessageType.COMMAND,
        message_name=message_name,
        source=source,
        target=target,
        timestamp_utc=_generate_timestamp_utc(),
        payload=_normalize_dict_input(payload, "payload"),
        metadata=_normalize_dict_input(metadata, "metadata"),
    )
    validate_envelope(envelope)
    return envelope


def create_query(
    message_name: str,
    source: str,
    target: Optional[str] = None,
    payload: Optional[Dict[str, Any]] = None,
    metadata: Optional[Dict[str, Any]] = None,
    protocol_version: str = CURRENT_PROTOCOL_VERSION,
) -> MessageEnvelope:
    """Construct and validate a root QUERY envelope."""
    msg_id = _generate_uuid_v4()
    envelope = MessageEnvelope(
        protocol_version=protocol_version,
        message_id=msg_id,
        correlation_id=msg_id,
        causation_id=None,
        message_type=MessageType.QUERY,
        message_name=message_name,
        source=source,
        target=target,
        timestamp_utc=_generate_timestamp_utc(),
        payload=_normalize_dict_input(payload, "payload"),
        metadata=_normalize_dict_input(metadata, "metadata"),
    )
    validate_envelope(envelope)
    return envelope


def create_event(
    message_name: str,
    source: str,
    target: Optional[str] = None,
    payload: Optional[Dict[str, Any]] = None,
    metadata: Optional[Dict[str, Any]] = None,
    parent: Optional[MessageEnvelope] = None,
    protocol_version: str = CURRENT_PROTOCOL_VERSION,
) -> MessageEnvelope:
    """Construct and validate an EVENT envelope (root or derived from a parent envelope)."""
    msg_id = _generate_uuid_v4()
    if parent is not None:
        correlation_id = parent.correlation_id
        causation_id = parent.message_id
    else:
        correlation_id = msg_id
        causation_id = None

    envelope = MessageEnvelope(
        protocol_version=protocol_version,
        message_id=msg_id,
        correlation_id=correlation_id,
        causation_id=causation_id,
        message_type=MessageType.EVENT,
        message_name=message_name,
        source=source,
        target=target,
        timestamp_utc=_generate_timestamp_utc(),
        payload=_normalize_dict_input(payload, "payload"),
        metadata=_normalize_dict_input(metadata, "metadata"),
    )
    validate_envelope(envelope)
    return envelope


def create_response(
    request: MessageEnvelope,
    responder_source: str,
    payload: Optional[Dict[str, Any]] = None,
    metadata: Optional[Dict[str, Any]] = None,
    message_name: Optional[str] = None,
) -> MessageEnvelope:
    """Construct and validate a RESPONSE envelope directly correlated to an incoming request.

    Invariant enforcement (caller cannot override):
    - target = request.source
    - correlation_id = request.correlation_id
    - causation_id = request.message_id
    """
    if not isinstance(request, MessageEnvelope):
        raise ProtocolValidationError(f"Expected MessageEnvelope for request, got {type(request).__name__}")

    name = message_name or f"{request.message_name}.response"
    msg_id = _generate_uuid_v4()

    envelope = MessageEnvelope(
        protocol_version=request.protocol_version,
        message_id=msg_id,
        correlation_id=request.correlation_id,
        causation_id=request.message_id,
        message_type=MessageType.RESPONSE,
        message_name=name,
        source=responder_source,
        target=request.source,
        timestamp_utc=_generate_timestamp_utc(),
        payload=_normalize_dict_input(payload, "payload"),
        metadata=_normalize_dict_input(metadata, "metadata"),
    )
    validate_envelope(envelope)
    return envelope


def create_error_response(
    request: MessageEnvelope,
    responder_source: str,
    error_code: str,
    error_message: str,
    details: Optional[Dict[str, Any]] = None,
    recoverable: bool = False,
    metadata: Optional[Dict[str, Any]] = None,
    message_name: Optional[str] = None,
) -> MessageEnvelope:
    """Construct and validate an ERROR envelope directly correlated to an incoming request.

    Invariant enforcement (caller cannot override):
    - target = request.source
    - correlation_id = request.correlation_id
    - causation_id = request.message_id
    - payload conforms to ErrorPayload
    """
    if not isinstance(request, MessageEnvelope):
        raise ProtocolValidationError(f"Expected MessageEnvelope for request, got {type(request).__name__}")

    name = message_name or f"{request.message_name}.error"
    msg_id = _generate_uuid_v4()

    error_payload = ErrorPayload(
        error_code=error_code,
        error_message=error_message,
        details=_normalize_dict_input(details, "details"),
        recoverable=recoverable,
    )

    envelope = MessageEnvelope(
        protocol_version=request.protocol_version,
        message_id=msg_id,
        correlation_id=request.correlation_id,
        causation_id=request.message_id,
        message_type=MessageType.ERROR,
        message_name=name,
        source=responder_source,
        target=request.source,
        timestamp_utc=_generate_timestamp_utc(),
        payload=error_payload.to_dict(),
        metadata=_normalize_dict_input(metadata, "metadata"),
    )
    validate_envelope(envelope)
    return envelope
