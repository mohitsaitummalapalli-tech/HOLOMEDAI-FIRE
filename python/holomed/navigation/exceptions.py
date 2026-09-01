# -*- coding: utf-8 -*-
"""C3-Compliant Exception Hierarchy for M14 Navigation Subsystem."""

from __future__ import annotations

from typing import Any, Mapping, Optional, Sequence

from holomed.devices.models import DeviceShutdownFailureRecord
from holomed.platform.exceptions import PlatformError


class NavigationError(PlatformError):
    """Base exception for all M14 surgical navigation operations."""

    def __init__(
        self,
        message: str,
        *,
        error_code: str = "ERR_NAVIGATION",
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


class NavigationLifecycleError(NavigationError):
    """Raised when service lifecycle state transitions or operations are violated."""

    def __init__(self, message: str, details: Optional[Mapping[str, Any]] = None) -> None:
        super().__init__(message, error_code="ERR_NAV_LIFECYCLE", details=details, recoverable=False)


class NavigationValidationError(NavigationError):
    """Raised when coordinates, quaternions, or identifiers fail validation."""

    def __init__(self, message: str, details: Optional[Mapping[str, Any]] = None) -> None:
        super().__init__(message, error_code="ERR_NAV_VALIDATION", details=details, recoverable=True)


class NavigationDegeneracyError(NavigationError):
    """Raised when trajectory vectors or quaternion norms are geometrically degenerate."""

    def __init__(self, message: str, details: Optional[Mapping[str, Any]] = None) -> None:
        super().__init__(message, error_code="ERR_NAV_DEGENERATE", details=details, recoverable=False)


class NavigationStalenessError(NavigationError):
    """Raised when telemetry timestamp exceeds MAX_ALLOWED_POSE_AGE_MS or exhibits future skew."""

    def __init__(self, message: str, details: Optional[Mapping[str, Any]] = None) -> None:
        super().__init__(message, error_code="ERR_NAV_STALE", details=details, recoverable=True)


class NavigationSequenceError(NavigationError):
    """Raised when telemetry sequence numbers are non-monotonic, duplicate, or replayed."""

    def __init__(self, message: str, details: Optional[Mapping[str, Any]] = None) -> None:
        super().__init__(message, error_code="ERR_NAV_SEQUENCE", details=details, recoverable=False)


class NavigationFrameMismatchError(NavigationError):
    """Raised when tool pose coordinate frame fails to match patient tracker frame."""

    def __init__(self, message: str, details: Optional[Mapping[str, Any]] = None) -> None:
        super().__init__(message, error_code="ERR_NAV_FRAME_MISMATCH", details=details, recoverable=False)


class NavigationRegistrationMismatchError(NavigationError):
    """Raised when navigation operations attempt to bind to unverified or missing registration."""

    def __init__(self, message: str, details: Optional[Mapping[str, Any]] = None) -> None:
        super().__init__(message, error_code="ERR_NAV_REGISTRATION_MISMATCH", details=details, recoverable=False)


class NavigationCapacityError(NavigationError):
    """Raised when active sessions or tracked instruments exceed configured limits."""

    def __init__(self, message: str, details: Optional[Mapping[str, Any]] = None) -> None:
        super().__init__(message, error_code="ERR_NAV_CAPACITY", details=details, recoverable=False)


class NavigationAuthorizationError(NavigationError):
    """Raised when an external client role is unauthorized to submit poses or trigger navigation."""

    def __init__(self, message: str, details: Optional[Mapping[str, Any]] = None) -> None:
        super().__init__(message, error_code="ERR_NAV_AUTHORIZATION", details=details, recoverable=False)


class NavigationShutdownError(NavigationError):
    """Raised when resource cleanup fails during NavigationService teardown."""

    def __init__(
        self,
        message: str,
        failures: Sequence[DeviceShutdownFailureRecord],
        details: Optional[Mapping[str, Any]] = None,
    ) -> None:
        super().__init__(message, error_code="ERR_NAV_SHUTDOWN", details=details, recoverable=False)
        self.failures = tuple(failures)
