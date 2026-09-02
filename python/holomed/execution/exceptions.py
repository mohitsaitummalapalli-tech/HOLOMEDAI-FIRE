# -*- coding: utf-8 -*-
"""Exception Hierarchy for M19 Navigation Execution & Coordination Gateway."""

from __future__ import annotations

from typing import Any, Mapping, Optional

from holomed.devices.models import DeviceShutdownFailureRecord
from holomed.platform.exceptions import PlatformError


class ExecutionError(PlatformError):
    """Base exception for all M19 execution gateway errors."""

    def __init__(self, message: str, details: Optional[Mapping[str, Any]] = None) -> None:
        super().__init__(message)
        self.details = details or {}


class ExecutionLifecycleError(ExecutionError):
    """Raised when service lifecycle or state transitions are violated."""
    pass


class ExecutionValidationError(ExecutionError):
    """Raised when execution request parameters or bindings fail validation."""
    pass


class ExecutionCapacityError(ExecutionError):
    """Raised when active execution session capacity is exceeded."""
    pass


class ExecutionBlockedError(ExecutionError):
    """Raised when a protected execution request is blocked by safety gate or workflow."""
    pass


class ExecutionGeometryError(ExecutionError):
    """Raised when underlying navigation geometry evaluation encounters a failure."""
    pass


class ExecutionAuthorizationError(ExecutionError):
    """Raised when an uncoordinated direct execution call or invalid capability is detected."""
    pass


class ExecutionSequenceError(ExecutionError):
    """Raised when an execution request violates sequence monotonicity."""
    pass


class ExecutionShutdownError(ExecutionError):
    """Raised when service teardown encounters resource deallocation failures."""

    def __init__(self, message: str, failures: list[DeviceShutdownFailureRecord]) -> None:
        super().__init__(message)
        self.failures = failures
