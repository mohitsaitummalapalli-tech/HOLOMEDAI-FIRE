"""Exception hierarchy for the HoloMed AI Spatial Vision subsystem (M01).

Implements strict multiple-inheritance patterns ensuring all vision exceptions
can be caught by domain-specific VisionError handlers, general HoloMedError
handlers, and standard M00 device lifecycle/capacity/shutdown handlers.
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


class VisionError(DeviceError):
    """Root domain exception for all spatial vision and tracking subsystem errors."""


class VisionLifecycleError(VisionError, DeviceLifecycleError):
    """Raised on invalid lifecycle transitions or operations outside STARTED."""


class VisionCapacityError(VisionError, DeviceCapacityError):
    """Raised when frame buffer pool, landmark counts, or tracking tables exceed bounds."""


class VisionValidationError(VisionError, DeviceValidationError):
    """Raised on malformed descriptors, dimensions, coordinates, or quaternion errors."""


class VisionSessionMismatchError(VisionValidationError):
    """Raised when envelope session_id does not match payload session_id."""


class VisionSequenceError(VisionValidationError):
    """Raised on non-monotonic or replayed sequence numbers."""


class VisionEpochMismatchError(VisionValidationError):
    """Raised when envelope epoch_id does not match payload epoch_id or service epoch."""


class VisionFrameValidationError(VisionValidationError):
    """Raised on corrupted, oversized, or invalid frame pixel buffers."""


class VisionPipelineError(VisionError):
    """Raised on unhandled algorithmic failure during vision pipeline stage execution."""


class VisionResourceIntegrityError(VisionError, DeviceResourceIntegrityError):
    """Raised on resource accounting violations or buffer slot leak detection."""


class VisionShutdownError(VisionError, DeviceShutdownError):
    """Raised when structural resource release fails during vision service teardown."""

    def __init__(self, message: str, failures: Sequence[DeviceShutdownFailureRecord]) -> None:
        super().__init__(message, tuple(failures))
        self.failures = tuple(failures)
