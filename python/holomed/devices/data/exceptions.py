"""HoloMed AI - Device data plane and telemetry processing exceptions."""

from __future__ import annotations

from holomed.devices.exceptions import DeviceError, DeviceNotFoundError


class DeviceDataError(DeviceError):
    """Base exception for all device data plane and telemetry errors."""


class DeviceDataValidationError(DeviceDataError):
    """Raised when device data item structure or fields fail validation."""


class DeviceDataLifecycleError(DeviceDataError):
    """Raised when an operation is attempted in an invalid lifecycle state or violates reentrancy."""


class DeviceDataCapacityError(DeviceDataError):
    """Raised when the bounded data queue capacity (256) is exceeded."""


class DeviceDataNotFoundError(DeviceDataError, DeviceNotFoundError):
    """Raised when submitting data for a device not registered in DeviceRegistry."""


class DeviceIdentityMismatchError(DeviceDataError):
    """Raised when submitted physical_id or device_type does not match registered device."""


class DeviceStaleInstanceError(DeviceDataError):
    """Raised when submitted data refers to a retired or stale device session."""


class DeviceDataEpochMismatchError(DeviceDataError):
    """Raised when submitted data does not match the active runtime epoch."""


class DuplicateDataSequenceError(DeviceDataError):
    """Raised when a submitted data sequence number is identical to the last seen sequence number."""


class OutOfOrderDataError(DeviceDataError):
    """Raised when a submitted data sequence number is strictly less than the last seen sequence number."""


class DataProcessorError(DeviceDataError):
    """Raised when data processor registration or execution fails."""


class DataProcessorCapacityError(DeviceDataError):
    """Raised when registered processors exceed capacity (256) or duplicate registration is attempted."""


class DataPayloadValidationError(DeviceDataError):
    """Raised when data payload fails deep freezing, LSCU unit bounds, or size limits."""
