"""HoloMed AI - Device command & query control plane exceptions."""

from __future__ import annotations

from holomed.devices.exceptions import DeviceError, DeviceNotFoundError


class DeviceControlError(DeviceError):
    """Base exception for all control-plane errors."""


class CommandNotFoundError(DeviceControlError):
    """Raised when a command is not registered in the control plane."""


class QueryNotFoundError(DeviceControlError):
    """Raised when a query is not registered in the control plane."""


class DeviceNotActiveError(DeviceControlError):
    """Raised when a device is not in ACTIVE state (or READY when allowed)."""


class DeviceStateError(DeviceControlError):
    """Raised when a device is in an invalid state for query or command execution."""


class CapabilityUnauthorizedError(DeviceControlError):
    """Raised when a target device lacks the required capability for a command."""


class DeviceCommandValidationError(DeviceControlError):
    """Raised when incoming command envelope, payload, or parameters fail validation."""


class DeviceQueryResultValidationError(DeviceControlError):
    """Raised when query result payload fails structure, size, or type validation."""


class DeviceCommandResultValidationError(DeviceControlError):
    """Raised when command result payload fails structure, size, or type validation."""


class IdempotencyConflictError(DeviceControlError):
    """Raised when a command is replayed with an identical message_id but conflicting payload."""


class ControlCapacityError(DeviceControlError):
    """Raised when registered commands, queries, or handler capacities are exceeded."""
