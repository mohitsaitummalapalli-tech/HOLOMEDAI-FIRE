# -*- coding: utf-8 -*-
"""C3-Compliant Exception Hierarchy for M13 Surgical Registration Subsystem."""

from __future__ import annotations

from typing import Any, Mapping, Optional, Sequence

from holomed.devices.models import DeviceShutdownFailureRecord
from holomed.platform.exceptions import PlatformError


class RegistrationError(PlatformError):
    """Base exception for all M13 Spatial Registration operations."""

    def __init__(
        self,
        message: str,
        *,
        error_code: str = "ERR_REGISTRATION",
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


class RegistrationLifecycleError(RegistrationError):
    """Raised when service lifecycle state transitions or operations are violated."""

    def __init__(self, message: str, details: Optional[Mapping[str, Any]] = None) -> None:
        super().__init__(message, error_code="ERR_REGISTRATION_LIFECYCLE", details=details, recoverable=False)


class RegistrationValidationError(RegistrationError):
    """Raised when coordinates, fiducial IDs, or schemas fail validation."""

    def __init__(self, message: str, details: Optional[Mapping[str, Any]] = None) -> None:
        super().__init__(message, error_code="ERR_REGISTRATION_VALIDATION", details=details, recoverable=True)


class RegistrationCapacityError(RegistrationError):
    """Raised when active registrations or fiducials exceed canonical limits."""

    def __init__(self, message: str, details: Optional[Mapping[str, Any]] = None) -> None:
        super().__init__(message, error_code="ERR_REGISTRATION_CAPACITY", details=details, recoverable=False)


class RegistrationDegeneracyError(RegistrationError):
    """Raised when fiducial point clouds are collinear or geometrically ill-conditioned."""

    def __init__(self, message: str, details: Optional[Mapping[str, Any]] = None) -> None:
        super().__init__(message, error_code="ERR_REGISTRATION_DEGENERATE", details=details, recoverable=False)


class RegistrationAccuracyError(RegistrationError):
    """Raised when computed FRE exceeds the explicit platform safety threshold (1.5 mm)."""

    def __init__(self, message: str, details: Optional[Mapping[str, Any]] = None) -> None:
        super().__init__(message, error_code="ERR_REGISTRATION_ACCURACY", details=details, recoverable=False)


class RegistrationPlanMismatchError(RegistrationError):
    """Raised when registration attempts to bind to an unlocked or missing surgical plan."""

    def __init__(self, message: str, details: Optional[Mapping[str, Any]] = None) -> None:
        super().__init__(message, error_code="ERR_PLAN_MISMATCH", details=details, recoverable=False)


class RegistrationVerificationError(RegistrationError):
    """Raised when WHO timeout verification fails or registration drift is detected."""

    def __init__(self, message: str, details: Optional[Mapping[str, Any]] = None) -> None:
        super().__init__(message, error_code="ERR_VERIFICATION_FAILED", details=details, recoverable=False)


class RegistrationEpochMismatchError(RegistrationError):
    """Raised when registration operations cross-communicate across mismatched epochs."""

    def __init__(self, message: str, details: Optional[Mapping[str, Any]] = None) -> None:
        super().__init__(message, error_code="ERR_EPOCH_MISMATCH", details=details, recoverable=False)


class RegistrationAuthorizationError(RegistrationError):
    """Raised when an external client role is unauthorized to submit or solve registration."""

    def __init__(self, message: str, details: Optional[Mapping[str, Any]] = None) -> None:
        super().__init__(message, error_code="ERR_AUTHORIZATION_FAILED", details=details, recoverable=False)


class RegistrationShutdownError(RegistrationError):
    """Raised when resource cleanup fails during RegistrationService teardown."""

    def __init__(
        self,
        message: str,
        failures: Sequence[DeviceShutdownFailureRecord],
        details: Optional[Mapping[str, Any]] = None,
    ) -> None:
        super().__init__(message, error_code="ERR_REGISTRATION_SHUTDOWN", details=details, recoverable=False)
        self.failures = tuple(failures)
