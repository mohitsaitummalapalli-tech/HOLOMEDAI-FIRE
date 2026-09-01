# -*- coding: utf-8 -*-
"""M03 Gesture Subsystem Exception Hierarchy.

All exceptions derive from GestureError, which inherits from DeviceError (M00.5),
ensuring clean C3 method resolution order (MRO) when paired with DeviceLifecycleError,
DeviceCapacityError, DeviceValidationError, DeviceResourceIntegrityError, and
DeviceShutdownError.
"""

from __future__ import annotations

from typing import Sequence

from holomed.devices.exceptions import (
    DeviceCapacityError,
    DeviceError,
    DeviceLifecycleError,
    DeviceResourceIntegrityError,
    DeviceShutdownError,
    DeviceValidationError,
)
from holomed.devices.models import DeviceShutdownFailureRecord


class GestureError(DeviceError):
    """Base exception for all M03 gesture and hand tracking errors."""


class GestureLifecycleError(GestureError, DeviceLifecycleError):
    """Raised when an operation violates the GestureService or state machine lifecycle."""


class GestureCapacityError(GestureError, DeviceCapacityError):
    """Raised when track tables, history buffers, or event sinks reach capacity."""


class GestureValidationError(GestureError, DeviceValidationError):
    """Raised when hand observation metadata, parameters, or geometry fail domain validation."""


class GestureEpochMismatchError(GestureError):
    """Raised when an ingested hand observation's epoch does not match RuntimeContext.epoch_id."""


class GestureSequenceError(GestureError):
    """Raised when a non-monotonic or duplicate sequence number is detected within a session."""


class GesturePipelineError(GestureError):
    """Raised when an unrecoverable fault occurs during gesture pipeline execution."""


class GestureResourceIntegrityError(GestureError, DeviceResourceIntegrityError):
    """Raised when internal structural resource handles or buffer invariants are corrupted."""


class GestureShutdownError(GestureError, DeviceShutdownError):
    """Raised when one or more structural resources fail to release cleanly during teardown."""

    def __init__(self, message: str, failures: Sequence[DeviceShutdownFailureRecord]) -> None:
        super().__init__(message, tuple(failures))
        self.failures: tuple[DeviceShutdownFailureRecord, ...] = tuple(failures)
