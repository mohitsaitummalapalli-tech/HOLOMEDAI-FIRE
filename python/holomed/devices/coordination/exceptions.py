"""HoloMed AI - Device coordination subsystem exceptions."""

from __future__ import annotations

from typing import Tuple

from holomed.devices.exceptions import (
    DeviceCapacityError,
    DeviceError,
    DeviceLifecycleError,
    DeviceNotFoundError,
    DeviceResourceIntegrityError,
    DeviceShutdownError,
    DeviceValidationError,
)
from holomed.devices.models import DeviceShutdownFailureRecord


class DeviceCoordinationError(DeviceError):
    """Base exception for all device coordination errors."""


class DeviceCoordinationValidationError(DeviceCoordinationError, DeviceValidationError):
    """Raised when coordination parameters, status, or structures fail validation."""


class DeviceCoordinationLifecycleError(DeviceCoordinationError, DeviceLifecycleError):
    """Raised when lifecycle invariants, reentrancy, or invalid operations are attempted."""


class DeviceCoordinationCapacityError(DeviceCoordinationError, DeviceCapacityError):
    """Raised when snapshot size, tracked devices, or event buffer bounds are exceeded."""


class DeviceCoordinationDeviceNotFoundError(DeviceCoordinationError, DeviceNotFoundError):
    """Raised when observing or updating telemetry for an unregistered device."""


class DeviceCoordinationEpochMismatchError(DeviceCoordinationError):
    """Raised when an operation provides an epoch_id mismatching current coordination epoch."""


class DeviceCoordinationIdentityError(DeviceCoordinationError):
    """Raised when device identity or physical_id does not match registry registration."""


class DeviceCoordinationSequenceError(DeviceCoordinationError):
    """Raised when telemetry sequence numbers are duplicate, out-of-order, or invalid."""


class DeviceCoordinationSerializationError(DeviceCoordinationError):
    """Raised when payload serialization fails or exceeds maximum allowed byte bound."""


class DeviceCoordinationShutdownError(DeviceCoordinationError, DeviceShutdownError):
    """Aggregated failure record raised when coordinator resource cleanup fails during shutdown."""

    def __init__(
        self,
        message: str,
        failures: Tuple[DeviceShutdownFailureRecord, ...],
    ) -> None:
        DeviceCoordinationError.__init__(self, message)
        self.failures = failures

    def __str__(self) -> str:
        base = super().__str__()
        if not self.failures:
            return base
        details = ", ".join(
            f"[{f.execution_index}] device='{f.device_id}' type='{f.error_type}': {f.error_message}"
            for f in self.failures
        )
        return f"{base} (failures: {details})"


class DeviceCoordinationIntegrityError(DeviceCoordinationError, DeviceResourceIntegrityError):
    """Raised when coordinator internal accounting structures violate consistency invariants."""
