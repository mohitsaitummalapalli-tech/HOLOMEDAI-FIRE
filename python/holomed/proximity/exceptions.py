# -*- coding: utf-8 -*-
"""C3-Compliant Exception Hierarchy for M15 Proximity Monitoring Subsystem."""

from __future__ import annotations

from typing import Any, Mapping, Optional, Sequence

from holomed.devices.models import DeviceShutdownFailureRecord
from holomed.platform.exceptions import PlatformError


class ProximityError(PlatformError):
    """Base exception for all M15 proximity monitoring operations."""

    def __init__(
        self,
        message: str,
        *,
        error_code: str = "ERR_PROXIMITY",
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


class ProximityLifecycleError(ProximityError):
    """Raised when service lifecycle state transitions or operations are violated."""

    def __init__(self, message: str, details: Optional[Mapping[str, Any]] = None) -> None:
        super().__init__(message, error_code="ERR_PROX_LIFECYCLE", details=details, recoverable=False)


class ProximityValidationError(ProximityError):
    """Raised when geometry, identifiers, or parameters fail validation."""

    def __init__(self, message: str, details: Optional[Mapping[str, Any]] = None) -> None:
        super().__init__(message, error_code="ERR_PROX_VALIDATION", details=details, recoverable=True)


class ProximityCapacityError(ProximityError):
    """Raised when zone counts or session limits are exceeded."""

    def __init__(self, message: str, details: Optional[Mapping[str, Any]] = None) -> None:
        super().__init__(message, error_code="ERR_PROX_CAPACITY", details=details, recoverable=False)


class ProximityRegistrationError(ProximityError):
    """Raised when M13 registration is not verified or missing."""

    def __init__(self, message: str, details: Optional[Mapping[str, Any]] = None) -> None:
        super().__init__(message, error_code="ERR_PROX_REGISTRATION", details=details, recoverable=False)


class ProximitySequenceError(ProximityError):
    """Raised when telemetry sequence numbers are non-monotonic or replayed."""

    def __init__(self, message: str, details: Optional[Mapping[str, Any]] = None) -> None:
        super().__init__(message, error_code="ERR_PROX_SEQUENCE", details=details, recoverable=False)


class ProximityInterlockError(ProximityError):
    """Raised when a safety interlock is triggered from proximity evaluation."""

    def __init__(self, message: str, details: Optional[Mapping[str, Any]] = None) -> None:
        super().__init__(message, error_code="ERR_PROX_INTERLOCK", details=details, recoverable=False)


class ProximityShutdownError(ProximityError):
    """Raised when resource cleanup fails during ProximityService teardown."""

    def __init__(
        self,
        message: str,
        failures: Sequence[DeviceShutdownFailureRecord],
        details: Optional[Mapping[str, Any]] = None,
    ) -> None:
        super().__init__(message, error_code="ERR_PROX_SHUTDOWN", details=details, recoverable=False)
        self.failures = tuple(failures)
