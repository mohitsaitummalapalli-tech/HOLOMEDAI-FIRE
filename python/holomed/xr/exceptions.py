# -*- coding: utf-8 -*-
"""M06 Headless XR Visualization & Spatial Presentation Exception Hierarchy."""

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


class XRError(DeviceError):
    """Base exception for all M06 XR visualization and presentation subsystem errors."""


class XRLifecycleError(XRError, DeviceLifecycleError):
    """Raised when XR service lifecycle rules, transitions, or reentrancy guards are violated."""


class XRValidationError(XRError, DeviceValidationError):
    """Raised when scene nodes, camera frustums, viewports, or projection parameters fail validation."""


class XRCapacityError(XRError, DeviceCapacityError):
    """Raised when scene node count, hierarchy depth, viewports, annotations, or event sinks exceed hard limits."""


class XREpochMismatchError(XRError):
    """Raised when visual updates, commands, or reset requests do not match the active service epoch."""


class XRHierarchyError(XRError):
    """Raised when scene graph hierarchy structure contains cycles, invalid parents, or depth violations."""


class XRProjectionError(XRError):
    """Raised when geometric projection encounters degenerate frustums or unrecoverable singularities."""


class XRResourceIntegrityError(XRError, DeviceResourceIntegrityError):
    """Raised when structural resource accounting or handle tracking is corrupted."""


class XRShutdownError(XRError, DeviceShutdownError):
    """Aggregated failure record raised when one or more XR structural handles fail teardown."""

    def __init__(
        self,
        message: str,
        failures: Tuple[DeviceShutdownFailureRecord, ...] | list[DeviceShutdownFailureRecord],
    ) -> None:
        norm_failures: Tuple[DeviceShutdownFailureRecord, ...] = tuple(failures)
        super().__init__(message, norm_failures)
