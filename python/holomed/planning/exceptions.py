# -*- coding: utf-8 -*-
"""C3-Compliant Exception Hierarchy for M12 Surgical Planning Subsystem."""

from __future__ import annotations

from typing import Any, Mapping, Optional, Sequence

from holomed.devices.models import DeviceShutdownFailureRecord
from holomed.platform.exceptions import PlatformError


class PlanningError(PlatformError):
    """Base exception for all M12 Preoperative Planning and Case operations."""

    def __init__(
        self,
        message: str,
        *,
        error_code: str = "ERR_PLANNING",
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


class PlanningLifecycleError(PlanningError):
    """Raised when service lifecycle state transitions or operations are violated."""

    def __init__(self, message: str, details: Optional[Mapping[str, Any]] = None) -> None:
        super().__init__(message, error_code="ERR_PLANNING_LIFECYCLE", details=details, recoverable=False)


class PlanningValidationError(PlanningError):
    """Raised when geometric constraints, patient case fields, or schemas fail validation."""

    def __init__(self, message: str, details: Optional[Mapping[str, Any]] = None) -> None:
        super().__init__(message, error_code="ERR_PLANNING_VALIDATION", details=details, recoverable=True)


class PlanningCapacityError(PlanningError):
    """Raised when active plans, trajectories, or exclusion zones exceed limits."""

    def __init__(self, message: str, details: Optional[Mapping[str, Any]] = None) -> None:
        super().__init__(message, error_code="ERR_PLANNING_CAPACITY", details=details, recoverable=False)


class PlanningLockError(PlanningError):
    """Raised when an attempt is made to mutate or overwrite a locked surgical plan."""

    def __init__(self, message: str, details: Optional[Mapping[str, Any]] = None) -> None:
        super().__init__(message, error_code="ERR_PLANNING_LOCKED", details=details, recoverable=False)


class PlanningVerificationError(PlanningError):
    """Raised when patient identity, procedure code, or laterality fails verification."""

    def __init__(self, message: str, details: Optional[Mapping[str, Any]] = None) -> None:
        super().__init__(message, error_code="ERR_PLANNING_VERIFICATION", details=details, recoverable=False)


class PlanningEpochMismatchError(PlanningError):
    """Raised when plan or verification cross-communicates across mismatched epochs."""

    def __init__(self, message: str, details: Optional[Mapping[str, Any]] = None) -> None:
        super().__init__(message, error_code="ERR_EPOCH_MISMATCH", details=details, recoverable=False)


class PlanningAuthorizationError(PlanningError):
    """Raised when an external client lacks authority to submit or lock a plan."""

    def __init__(self, message: str, details: Optional[Mapping[str, Any]] = None) -> None:
        super().__init__(message, error_code="ERR_AUTHORIZATION_FAILED", details=details, recoverable=False)


class PlanningShutdownError(PlanningError):
    """Raised when resource cleanup fails during PlanningService teardown."""

    def __init__(
        self,
        message: str,
        failures: Sequence[DeviceShutdownFailureRecord],
        details: Optional[Mapping[str, Any]] = None,
    ) -> None:
        super().__init__(message, error_code="ERR_PLANNING_SHUTDOWN", details=details, recoverable=False)
        self.failures = tuple(failures)
