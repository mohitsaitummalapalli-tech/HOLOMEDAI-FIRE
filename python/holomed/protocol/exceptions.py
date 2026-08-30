"""HoloMed Protocol Exception Hierarchy."""


class ProtocolError(Exception):
    """Base exception for all HoloMed protocol errors."""


class ProtocolValidationError(ProtocolError):
    """Raised when a message envelope or payload violates structural or semantic validation rules."""


class ProtocolVersionError(ProtocolError):
    """Raised when a message specifies a malformed, unsupported, or incompatible protocol version."""


class ProtocolSerializationError(ProtocolError):
    """Raised when an envelope cannot be deterministically serialized to JSON/bytes."""


class ProtocolDeserializationError(ProtocolError):
    """Raised when raw input cannot be parsed due to syntax errors, duplicate keys, non-finite numbers, or safety limits."""
