# -*- coding: utf-8 -*-
"""C3-Compliant Exception Hierarchy for M16 Drift & Landmark Monitoring Subsystem."""

from __future__ import annotations

from typing import Any, Mapping, Optional, Sequence

from holomed.devices.models import DeviceShutdownFailureRecord
from holomed.platform.exceptions import PlatformError


class DriftError(PlatformError):
    """Base exception for all M16 registration drift operations."""

    def __init__(
        self,
        message: str,
        *,
        error_code: str = "ERR_DRIFT",
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


class DriftLifecycleError(DriftError):
    """Raised when service lifecycle state transitions or operations are violated."""

    def __init__(self, message: str, details: Optional[Mapping[str, Any]] = None) -> None:
        super().__init__(message, error_code="ERR_DRIFT_LIFECYCLE", details=details, recoverable=False)


class DriftValidationError(DriftError):
    """Raised when geometry, identifiers, timestamps, or tolerances fail validation."""

    def __init__(self, message: str, details: Optional[Mapping[str, Any]] = None) -> None:
        super().__init__(message, error_code="ERR_DRIFT_VALIDATION", details=details, recoverable=True)


class DriftCapacityError(DriftError):
    """Raised when landmark counts or session limits are exceeded."""

    def __init__(self, message: str, details: Optional[Mapping[str, Any]] = None) -> None:
        super().__init__(message, error_code="ERR_DRIFT_CAPACITY", details=details, recoverable=False)


class DriftRegistrationError(DriftError):
    """Raised when M13 registration is unverified, missing, or mismatched."""

    def __init__(self, message: str, details: Optional[Mapping[str, Any]] = None) -> None:
        super().__init__(message, error_code="ERR_DRIFT_REGISTRATION", details=details, recoverable=False)


class DriftSequenceError(DriftError):
    """Raised when telemetry sequence numbers are non-monotonic or replayed."""

    def __init__(self, message: str, details: Optional[Mapping[str, Any]] = None) -> None:
        super().__init__(message, error_code="ERR_DRIFT_SEQUENCE", details=details, recoverable=False)


class DriftEpochMismatchError(DriftError):
    """Raised when observation epoch does not match registration epoch."""

    def __init__(self, message: str, details: Optional[Mapping[str, Any]] = None) -> None:
        super().__init__(message, error_code="ERR_DRIFT_EPOCH_MISMATCH", details=details, recoverable=False)


class DriftInterlockError(DriftError):
    """Raised when a safety interlock is triggered from drift evaluation."""

    def __init__(self, message: str, details: Optional[Mapping[str, Any]] = None) -> None:
        super().__init__(message, error_code="ERR_DRIFT_INTERLOCK", details=details, recoverable=False)


class DriftShutdownError(DriftError):
    """Raised when resource cleanup fails during DriftService teardown."""

    def __init__(
        self,
        message: str,
        failures: Sequence[DeviceShutdownFailureRecord],
        details: Optional[Mapping[str, Any]] = None,
    ) -> None:
        super().__init__(message, error_code="ERR_DRIFT_SHUTDOWN", details=details, recoverable=False)
        self.failures = tuple(failures)
