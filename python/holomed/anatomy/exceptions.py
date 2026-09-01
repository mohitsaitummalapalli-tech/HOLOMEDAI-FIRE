# -*- coding: utf-8 -*-
"""M05 Anatomy & Simulation Foundation Exception Hierarchy."""

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


class AnatomyError(DeviceError):
    """Base exception for all M05 Anatomy & Simulation subsystem errors."""


class AnatomyLifecycleError(AnatomyError, DeviceLifecycleError):
    """Raised when Anatomy service lifecycle rules or transitions are violated."""


class AnatomyValidationError(AnatomyError, DeviceValidationError):
    """Raised when an anatomical entity, geometry, coordinate, or parameter violates validation."""


class AnatomyCapacityError(AnatomyError, DeviceCapacityError):
    """Raised when anatomy node count, depth, solver steps, or event sink bounds are exceeded."""


class AnatomyEpochMismatchError(AnatomyError):
    """Raised when observation, simulation step, or command epoch does not match service epoch."""


class AnatomyHierarchyError(AnatomyError):
    """Raised when anatomy hierarchy structure violates tree invariants, depth limits, or contains cycles."""


class AnatomySolverError(AnatomyError):
    """Raised when constraint solver encounters unrecoverable mathematical degeneracies."""


class AnatomyResourceIntegrityError(AnatomyError, DeviceResourceIntegrityError):
    """Raised when structural resource accounting or handle tracking is corrupted."""


class AnatomyShutdownError(AnatomyError, DeviceShutdownError):
    """Aggregated failure record raised when one or more anatomy structural handles fail teardown."""

    def __init__(
        self,
        message: str,
        failures: Tuple[DeviceShutdownFailureRecord, ...] | list[DeviceShutdownFailureRecord],
    ) -> None:
        norm_failures: Tuple[DeviceShutdownFailureRecord, ...] = tuple(failures)
        super().__init__(message, norm_failures)
