# -*- coding: utf-8 -*-
"""C3-Compliant Exception Hierarchy for M10 Clinical Workflow Subsystem."""

from __future__ import annotations

from typing import Sequence
from holomed.devices.models import DeviceShutdownFailureRecord
from holomed.platform.exceptions import (
    PlatformCapacityError,
    PlatformEpochMismatchError,
    PlatformError,
    PlatformLifecycleError,
    PlatformResourceIntegrityError,
    PlatformSequenceError,
    PlatformShutdownError,
    PlatformValidationError,
)


class WorkflowError(PlatformError):
    """Base exception for all clinical workflow and safety interlock errors."""


class WorkflowLifecycleError(WorkflowError, PlatformLifecycleError):
    """Raised when invalid service lifecycle states or transition operations occur."""


class WorkflowValidationError(WorkflowError, PlatformValidationError):
    """Raised when validation of procedures, phases, or workflow parameters fails."""


class WorkflowCapacityError(WorkflowError, PlatformCapacityError):
    """Raised when workflow counts, transition limits, or event logs exceed capacity."""


class WorkflowResourceIntegrityError(WorkflowError, PlatformResourceIntegrityError):
    """Raised when internal workflow structural handles or registries are corrupted."""


class WorkflowShutdownError(WorkflowError, PlatformShutdownError):
    """Raised when errors occur during workflow teardown and resource release."""

    def __init__(self, message: str, failures: Sequence[DeviceShutdownFailureRecord]) -> None:
        super().__init__(message, tuple(failures))


class WorkflowEpochMismatchError(WorkflowError, PlatformEpochMismatchError):
    """Raised when an operation targets an epoch differing from the active workflow epoch."""


class WorkflowSessionError(WorkflowError, PlatformValidationError):
    """Raised when operations are submitted for an invalid, closed, or mismatched session."""


class WorkflowSequenceError(WorkflowError, PlatformSequenceError):
    """Raised when non-monotonic or duplicate sequence numbers are submitted."""


class WorkflowTransitionError(WorkflowError, PlatformValidationError):
    """Raised when an illegal or unauthorized phase transition is attempted."""


class WorkflowPrerequisiteError(WorkflowError, PlatformValidationError):
    """Raised when required prerequisites for a clinical phase have not been satisfied."""


class WorkflowConfirmationRequiredError(WorkflowError, PlatformValidationError):
    """Raised when a phase transition or action requires explicit human confirmation."""


class WorkflowConfirmationError(WorkflowError, PlatformValidationError):
    """Raised when confirmation requests or responses are stale, invalid, or rejected."""


class WorkflowSafetyInterlockError(WorkflowError, PlatformResourceIntegrityError):
    """Raised when an active safety interlock blocks a phase transition or tool action."""


class WorkflowAuthorizationError(WorkflowError, PlatformValidationError):
    """Raised when an unauthorized tool category or surgical actuation attempt is made."""


class WorkflowRecoveryError(WorkflowError, PlatformResourceIntegrityError):
    """Raised when workflow reconstruction from persistent journals fails or is ambiguous."""
