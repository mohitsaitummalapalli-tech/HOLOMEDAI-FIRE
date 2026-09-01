"""HoloMed AI - Device Orchestration Exception Hierarchy."""

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


class DeviceOrchestrationError(DeviceError):
    """Base exception for all M00.9 device orchestration errors."""


class DeviceOrchestrationLifecycleError(DeviceOrchestrationError, DeviceLifecycleError):
    """Raised on invalid orchestration lifecycle transitions or operations outside STARTED."""


class DeviceOrchestrationCapacityError(DeviceOrchestrationError, DeviceCapacityError):
    """Raised when event history or counter limits are exceeded."""


class DeviceOrchestrationIntegrityError(DeviceOrchestrationError, DeviceResourceIntegrityError):
    """Raised when cross-plane resource or state integrity audit fails."""


class DeviceOrchestrationValidationError(DeviceOrchestrationError, DeviceValidationError):
    """Raised on invalid parameters or envelope schemas."""


class DeviceOrchestrationBridgeError(DeviceOrchestrationError):
    """Raised when cross-plane event translation fails fatally."""


class DeviceOrchestrationSerializationError(DeviceOrchestrationValidationError):
    """Raised when payload serialization exceeds wire boundaries or is malformed."""


class DeviceOrchestrationShutdownError(DeviceOrchestrationError, DeviceShutdownError):
    """Raised when structural resource release fails during teardown."""

    def __init__(self, message: str, failures: Sequence[DeviceShutdownFailureRecord]) -> None:
        super().__init__(message, failures)
        self.failures = tuple(failures)
