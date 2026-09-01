# -*- coding: utf-8 -*-
"""C3-Compliant Exception Hierarchy for M11 External Client Gateway."""

from __future__ import annotations

from typing import Any, Mapping, Optional, Sequence

from holomed.devices.models import DeviceShutdownFailureRecord
from holomed.platform.exceptions import PlatformError


class GatewayError(PlatformError):
    """Base exception for all M11 External Client Gateway operations."""

    def __init__(
        self,
        message: str,
        *,
        error_code: str = "ERR_GATEWAY",
        details: Optional[Mapping[str, Any]] = None,
        recoverable: bool = False,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.error_code = error_code
        self.details = dict(details) if details is not None else {}
        self.recoverable = recoverable

    def to_dict(self) -> dict[str, Any]:
        """Convert exception to standardized protocol-compatible error dict."""
        return {
            "error_code": self.error_code,
            "error_message": self.message,
            "details": dict(self.details),
            "recoverable": self.recoverable,
        }


class GatewayLifecycleError(GatewayError):
    """Raised when an operation violates GatewayService lifecycle state."""

    def __init__(self, message: str, details: Optional[Mapping[str, Any]] = None) -> None:
        super().__init__(message, error_code="ERR_GATEWAY_LIFECYCLE", details=details, recoverable=False)


class GatewayShutdownError(GatewayError):
    """Raised when resource cleanup fails during GatewayService shutdown."""

    def __init__(
        self,
        message: str,
        failures: Sequence[DeviceShutdownFailureRecord],
        details: Optional[Mapping[str, Any]] = None,
    ) -> None:
        super().__init__(message, error_code="ERR_GATEWAY_SHUTDOWN", details=details, recoverable=False)
        self.failures = tuple(failures)


class GatewayValidationError(GatewayError):
    """Raised when envelope, client ID, or payload validation fails."""

    def __init__(self, message: str, details: Optional[Mapping[str, Any]] = None) -> None:
        super().__init__(message, error_code="ERR_GATEWAY_VALIDATION", details=details, recoverable=True)


class GatewayCapacityError(GatewayError):
    """Raised when client limits or connection thresholds are exceeded."""

    def __init__(self, message: str, details: Optional[Mapping[str, Any]] = None) -> None:
        super().__init__(message, error_code="ERR_GATEWAY_CAPACITY", details=details, recoverable=False)


class GatewayTransportError(GatewayError):
    """Raised when low-level transport or socket IO encounters a failure."""

    def __init__(self, message: str, details: Optional[Mapping[str, Any]] = None) -> None:
        super().__init__(message, error_code="ERR_GATEWAY_TRANSPORT", details=details, recoverable=False)


class GatewayFramingError(GatewayError):
    """Raised when frame headers, boundaries, or sizes violate protocol rules."""

    def __init__(self, message: str, details: Optional[Mapping[str, Any]] = None) -> None:
        super().__init__(message, error_code="ERR_MALFORMED_FRAME", details=details, recoverable=False)


class GatewayAuthenticationError(GatewayError):
    """Raised when client handshake or credentials fail validation."""

    def __init__(self, message: str, details: Optional[Mapping[str, Any]] = None) -> None:
        super().__init__(message, error_code="ERR_AUTHENTICATION_FAILED", details=details, recoverable=False)


class GatewayAuthorizationError(GatewayError):
    """Raised when a client role attempts an unauthorized command."""

    def __init__(self, message: str, details: Optional[Mapping[str, Any]] = None) -> None:
        super().__init__(message, error_code="ERR_AUTHORIZATION_FAILED", details=details, recoverable=False)


class GatewayQueueOverflowError(GatewayError):
    """Raised when an egress queue is saturated strictly with critical messages."""

    def __init__(self, message: str, details: Optional[Mapping[str, Any]] = None) -> None:
        super().__init__(message, error_code="ERR_QUEUE_OVERFLOW", details=details, recoverable=False)


class GatewaySessionMismatchError(GatewayError):
    """Raised when client requests a session differing from its bound session."""

    def __init__(self, message: str, details: Optional[Mapping[str, Any]] = None) -> None:
        super().__init__(message, error_code="ERR_SESSION_MISMATCH", details=details, recoverable=False)


class GatewayEpochMismatchError(GatewayError):
    """Raised when client communicates across an expired or mismatched runtime epoch."""

    def __init__(self, message: str, details: Optional[Mapping[str, Any]] = None) -> None:
        super().__init__(message, error_code="ERR_EPOCH_MISMATCH", details=details, recoverable=False)
