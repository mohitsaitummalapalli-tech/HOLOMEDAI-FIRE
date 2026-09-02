# -*- coding: utf-8 -*-
"""Exception Hierarchy for M18 Unified Cross-Service Safety Gate."""

from __future__ import annotations

from typing import Any, Mapping, Optional

from holomed.devices.models import DeviceShutdownFailureRecord
from holomed.platform.exceptions import PlatformError


class SafetyGateError(PlatformError):
    """Base exception for all M18 safety gate errors."""

    def __init__(self, message: str, details: Optional[Mapping[str, Any]] = None) -> None:
        super().__init__(message)
        self.details = details or {}


class SafetyGateLifecycleError(SafetyGateError):
    """Raised when service lifecycle or state transitions are violated."""
    pass


class SafetyGateValidationError(SafetyGateError):
    """Raised when gate request parameters fail validation."""
    pass


class SafetyGateCapacityError(SafetyGateError):
    """Raised when active session capacity is exceeded."""
    pass


class SafetyGateEvaluationError(SafetyGateError):
    """Raised when safety gate evaluation encounters an unexpected runtime error."""
    pass


class SafetyGateShutdownError(SafetyGateError):
    """Raised when service teardown encounters resource deallocation failures."""

    def __init__(self, message: str, failures: list[DeviceShutdownFailureRecord]) -> None:
        super().__init__(message)
        self.failures = failures
