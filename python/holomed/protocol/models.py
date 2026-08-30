"""HoloMed Protocol Core Models and Enums."""

from dataclasses import dataclass
from enum import Enum
from typing import Any, Dict, List, Optional, Union

CURRENT_PROTOCOL_VERSION: str = "1.0"

JSONPrimitive = Union[str, int, float, bool, None]
JSONValue = Union[JSONPrimitive, List[Any], Dict[str, Any]]
JSONObject = Dict[str, JSONValue]


class MessageType(str, Enum):
    """Enumeration of standard HoloMed message classifications."""

    COMMAND = "COMMAND"
    QUERY = "QUERY"
    EVENT = "EVENT"
    RESPONSE = "RESPONSE"
    ERROR = "ERROR"


@dataclass(frozen=True)
class ErrorPayload:
    """Standardized error payload representation for error envelopes.

    Note: @dataclass(frozen=True) enforces top-level attribute immutability;
    nested dictionary structures (e.g., details) are not recursively frozen.
    """

    error_code: str
    error_message: str
    details: Dict[str, Any]
    recoverable: bool

    def to_dict(self) -> Dict[str, Any]:
        """Convert ErrorPayload instance to dictionary representation."""
        return {
            "error_code": self.error_code,
            "error_message": self.error_message,
            "details": dict(self.details),
            "recoverable": self.recoverable,
        }


@dataclass(frozen=True)
class MessageEnvelope:
    """Canonical message envelope format for all HoloMed inter-subsystem messages.

    Note: @dataclass(frozen=True) enforces top-level attribute immutability;
    nested dictionary and list structures in payload and metadata are not
    recursively frozen.
    """

    protocol_version: str
    message_id: str
    correlation_id: str
    causation_id: Optional[str]
    message_type: MessageType
    message_name: str
    source: str
    target: Optional[str]
    timestamp_utc: str
    payload: Dict[str, Any]
    metadata: Dict[str, Any]

    def to_dict(self) -> Dict[str, Any]:
        """Convert MessageEnvelope to canonical dictionary representation with string message_type."""
        return {
            "protocol_version": self.protocol_version,
            "message_id": self.message_id,
            "correlation_id": self.correlation_id,
            "causation_id": self.causation_id,
            "message_type": self.message_type.value,
            "message_name": self.message_name,
            "source": self.source,
            "target": self.target,
            "timestamp_utc": self.timestamp_utc,
            "payload": dict(self.payload),
            "metadata": dict(self.metadata),
        }
