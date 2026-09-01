# -*- coding: utf-8 -*-
"""C3-Compliant Exception Hierarchy for M09 Persistence Subsystem."""

from __future__ import annotations

from typing import Sequence, Tuple
from holomed.devices.exceptions import DeviceSecurityError
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


class PersistenceError(PlatformError):
    """Base exception for all persistence subsystem errors."""


class PersistenceLifecycleError(PersistenceError, PlatformLifecycleError):
    """Raised when an invalid lifecycle state transition or operation occurs."""


class PersistenceValidationError(PersistenceError, PlatformValidationError):
    """Raised when validation of persistence parameters, schemas, or bounds fails."""


class PersistenceCapacityError(PersistenceError, PlatformCapacityError):
    """Raised when storage bounds, entry counts, or file limits are exceeded."""


class PersistenceResourceIntegrityError(PersistenceError, PlatformResourceIntegrityError):
    """Raised when required structural handles or underlying storage resources fail."""


class PersistenceShutdownError(PersistenceError, PlatformShutdownError):
    """Raised when errors occur during persistence teardown and handle release."""

    def __init__(self, message: str, failures: Sequence[DeviceShutdownFailureRecord]) -> None:
        super().__init__(message, tuple(failures))


class PersistenceEpochMismatchError(PersistenceError, PlatformEpochMismatchError):
    """Raised when an operation targets an epoch differing from the active storage epoch."""


class PersistenceSequenceError(PersistenceError, PlatformSequenceError):
    """Raised when non-monotonic or duplicate sequence numbers are submitted."""


class PersistenceSecurityError(PersistenceError, DeviceSecurityError):
    """Raised when path traversal, invalid characters, or injection attempts occur."""


class PersistenceCorruptionError(PersistenceResourceIntegrityError):
    """Raised when journal entries fail SHA-256 or hash-chain verification."""


class PersistenceSchemaVersionError(PersistenceValidationError):
    """Raised when an unknown or incompatible schema version is encountered."""


class PersistenceReplayError(PersistenceResourceIntegrityError):
    """Raised when deterministic replay detects sequence or state divergence."""
