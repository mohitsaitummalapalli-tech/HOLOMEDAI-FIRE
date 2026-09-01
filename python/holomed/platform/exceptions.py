# -*- coding: utf-8 -*-
"""M08 Master Application Supervisor & Platform Exception Hierarchy."""

from __future__ import annotations

from typing import Tuple

from holomed.devices.exceptions import (
    DeviceCapacityError,
    DeviceError,
    DeviceLifecycleError,
    DeviceResourceIntegrityError,
    DeviceShutdownError,
    DeviceValidationError,
)
from holomed.devices.models import DeviceShutdownFailureRecord


class PlatformError(DeviceError):
    """Base exception for all M08 Master Application Supervisor subsystem errors."""


class PlatformLifecycleError(PlatformError, DeviceLifecycleError):
    """Raised when platform supervisor lifecycle transitions, locks, or states are violated."""


class PlatformValidationError(PlatformError, DeviceValidationError):
    """Raised when platform descriptors, cycle requests, or parameters fail schema validation."""


class PlatformCapacityError(PlatformError, DeviceCapacityError):
    """Raised when session counts, cycle history buffers, or event sinks exceed hard limits."""


class PlatformResourceIntegrityError(PlatformError, DeviceResourceIntegrityError):
    """Raised when structural resource accounting or handle tracking is corrupted."""


class PlatformShutdownError(PlatformError, DeviceShutdownError):
    """Aggregated failure record raised when one or more platform structural handles fail teardown."""

    def __init__(
        self,
        message: str,
        failures: Tuple[DeviceShutdownFailureRecord, ...] | list[DeviceShutdownFailureRecord],
    ) -> None:
        norm_failures: Tuple[DeviceShutdownFailureRecord, ...] = tuple(failures)
        super().__init__(message, norm_failures)


class PlatformEpochMismatchError(PlatformError):
    """Raised when a cycle execution, session, or reset request does not match the active epoch."""


class PlatformSequenceError(PlatformError):
    """Raised when cycle sequence numbers are non-monotonic, decreasing, or duplicated per session."""


class PlatformEpochMigrationError(PlatformError):
    """Raised when the fail-stop epoch migration protocol fails during preparation, reset, or verification."""


class PlatformCycleError(PlatformError):
    """Raised when unrecoverable phase failure or transaction abort occurs during an execution cycle."""


class PlatformSecurityError(PlatformError):
    """Raised when a cycle or external invocation attempts sandbox escape or prohibited execution."""
