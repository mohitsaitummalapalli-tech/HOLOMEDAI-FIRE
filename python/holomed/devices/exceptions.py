"""HoloMed AI - Device abstraction and hardware integration exceptions."""

from __future__ import annotations

from typing import TYPE_CHECKING, Optional, Tuple

from holomed.common.exceptions import HoloMedError

if TYPE_CHECKING:
    from holomed.devices.models import DeviceShutdownFailureRecord


class DeviceError(HoloMedError):
    """Base exception for all device abstraction errors."""


class DeviceValidationError(DeviceError):
    """Raised when descriptor, capability, or parameter validation fails."""


class DeviceLifecycleError(DeviceError):
    """Raised when an illegal device lifecycle state transition is attempted."""


class DuplicateDeviceError(DeviceError):
    """Raised when registering a device with duplicate device_id or physical_id."""


class DeviceCapacityError(DeviceError):
    """Raised when device registry, batch size, or handle capacity is exceeded."""


class DeviceNotFoundError(DeviceError):
    """Raised when querying a device identifier that does not exist in registry."""


class DeviceFactoryError(DeviceError):
    """Raised when a device factory produces an invalid or stale device instance."""


class DeviceFactoryMissingError(DeviceError):
    """Raised when discovery requires a factory for an unregistered DeviceType."""


class DeviceDiscoveryError(DeviceError):
    """Raised when discovery iteration, ingestion, or snapshot reconstruction fails."""


class DeviceResourceOwnershipError(DeviceError):
    """Raised when a device attempts unauthorized access to another device's or manager's resource."""


class DeviceResourceIntegrityError(DeviceError):
    """Raised when internal resource accounting structures desynchronize."""


class DeviceSecurityError(DeviceError):
    """Raised when unauthorized registry mutation is attempted without authority token."""


class DeviceShutdownError(DeviceError):
    """Aggregated failure record raised when one or more devices or structural handles fail teardown."""

    def __init__(
        self,
        message: str,
        failures: Tuple[DeviceShutdownFailureRecord, ...],
    ) -> None:
        super().__init__(message)
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
