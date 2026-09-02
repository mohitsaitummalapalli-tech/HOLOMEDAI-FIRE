# -*- coding: utf-8 -*-
"""Exception Hierarchy for M17 Spatial Recovery Orchestration."""

from __future__ import annotations

from typing import Any, Mapping, Optional

from holomed.devices.models import DeviceShutdownFailureRecord
from holomed.platform.exceptions import PlatformError


class RecoveryError(PlatformError):
    """Base exception for all M17 spatial recovery errors."""

    def __init__(self, message: str, details: Optional[Mapping[str, Any]] = None) -> None:
        super().__init__(message)
        self.details = details or {}


class RecoveryLifecycleError(RecoveryError):
    """Raised when service lifecycle or state transitions are violated."""
    pass


class RecoveryValidationError(RecoveryError):
    """Raised when candidate models, authorization, or geometries fail validation."""
    pass


class RecoveryCapacityError(RecoveryError):
    """Raised when active session or history capacity limits are exceeded."""
    pass


class RecoveryAccuracyError(RecoveryError):
    """Raised when candidate registration solver FRE exceeds maximum allowed threshold."""
    pass


class RecoveryVerificationError(RecoveryError):
    """Raised when candidate physical checkpoint drift exceeds maximum allowed threshold."""
    pass


class RecoveryAuthorizationError(RecoveryError):
    """Raised when operator authorization is missing, stale, or malformed."""
    pass


class RecoveryActivationError(RecoveryError):
    """Raised when M13 activation or downstream service rebinding fails."""
    pass


class RecoveryConsistencyError(RecoveryError):
    """Raised when post-activation multi-service consistency verification fails."""
    pass


class RecoveryShutdownError(RecoveryError):
    """Raised when service teardown encounters resource deallocation failures."""

    def __init__(self, message: str, failures: list[DeviceShutdownFailureRecord]) -> None:
        super().__init__(message)
        self.failures = failures
