# -*- coding: utf-8 -*-
"""M07 Tool Orchestration & Clinical Agent Exception Hierarchy."""

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


class ToolError(DeviceError):
    """Base exception for all M07 Tool Orchestration subsystem errors."""


class ToolLifecycleError(ToolError, DeviceLifecycleError):
    """Raised when Tool service or registry lifecycle rules, locks, or transitions are violated."""


class ToolValidationError(ToolError, DeviceValidationError):
    """Raised when tool descriptors, invocation contexts, or parameters fail schema validation."""


class ToolCapacityError(ToolError, DeviceCapacityError):
    """Raised when tool registry count, session count, history buffers, or event sinks exceed hard limits."""


class ToolResourceIntegrityError(ToolError, DeviceResourceIntegrityError):
    """Raised when structural resource accounting or handle tracking is corrupted."""


class ToolShutdownError(ToolError, DeviceShutdownError):
    """Aggregated failure record raised when one or more tool structural handles fail teardown."""

    def __init__(
        self,
        message: str,
        failures: Tuple[DeviceShutdownFailureRecord, ...] | list[DeviceShutdownFailureRecord],
    ) -> None:
        norm_failures: Tuple[DeviceShutdownFailureRecord, ...] = tuple(failures)
        super().__init__(message, norm_failures)


class ToolEpochMismatchError(ToolError):
    """Raised when a tool invocation or reset request does not match the active service epoch."""


class ToolDuplicateError(ToolError):
    """Raised when registering a tool with an already registered tool_id."""


class ToolSequenceError(ToolError):
    """Raised when invocation sequence numbers are non-monotonic, decreasing, or duplicated per session."""


class ToolRecursionError(ToolError):
    """Raised when tool invocation chains exceed maximum depth or form recursive cycles."""


class ToolSafetyError(ToolError):
    """Raised when an invocation or registration attempts an unsafe or prohibited clinical/surgical action."""


class ToolSecurityError(ToolError):
    """Raised when an invocation or parameter attempts code injection, sandbox escape, or prohibited execution."""


class ToolAuthorizationError(ToolError):
    """Raised when an invocation lacks an active, valid execution capability."""
