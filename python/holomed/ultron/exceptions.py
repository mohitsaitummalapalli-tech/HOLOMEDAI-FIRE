# -*- coding: utf-8 -*-
"""M04 Ultron Exception Hierarchy."""

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


class UltronError(DeviceError):
    """Base exception for all M04 Ultron errors."""


class UltronLifecycleError(UltronError, DeviceLifecycleError):
    """Raised when Ultron lifecycle transitions or states are violated."""


class UltronValidationError(UltronError, DeviceValidationError):
    """Raised when an observation, context, entity, or parameter violates validation."""


class UltronCapacityError(UltronError, DeviceCapacityError):
    """Raised when Ultron capacity limits are exceeded."""


class UltronEpochMismatchError(UltronError):
    """Raised when observation, context, or command epoch does not match active service epoch."""


class UltronSequenceError(UltronError):
    """Raised when observation sequence number decreases or duplicates within a session."""


class UltronReasoningError(UltronError):
    """Raised when reasoning evaluation fails or encounters internal inconsistencies."""


class UltronReasoningDepthError(UltronReasoningError):
    """Raised when recursive reasoning evaluation exceeds configured maximum depth."""


class UltronIntentConflictError(UltronError):
    """Raised when action intents mutually conflict or contradict established safety constraints."""


class UltronResourceIntegrityError(UltronError, DeviceResourceIntegrityError):
    """Raised when structural resource ownership or accounting is corrupted."""


class UltronShutdownError(UltronError, DeviceShutdownError):
    """Aggregated failure record raised when one or more Ultron structural handles fail teardown."""

    def __init__(
        self,
        message: str,
        failures: Tuple[DeviceShutdownFailureRecord, ...] | list[DeviceShutdownFailureRecord],
    ) -> None:
        norm_failures: Tuple[DeviceShutdownFailureRecord, ...] = tuple(failures)
        super().__init__(message, norm_failures)
