# -*- coding: utf-8 -*-
"""M02 Audio Subsystem Exception Hierarchy.

All exceptions derive from AudioError, which inherits from DeviceError (M00.5),
ensuring clean C3 method resolution order (MRO) when paired with DeviceLifecycleError,
DeviceCapacityError, DeviceValidationError, and DeviceShutdownError (D193).
"""

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


class AudioError(DeviceError):
    """Base exception for all M02 audio subsystem errors."""


class AudioLifecycleError(AudioError, DeviceLifecycleError):
    """Raised when an operation violates the AudioService lifecycle state machine."""


class AudioCapacityError(AudioError, DeviceCapacityError):
    """Raised when an audio buffer, track table, or event sink reaches capacity."""


class AudioValidationError(AudioError, DeviceValidationError):
    """Raised when audio metadata, parameters, or geometry fail domain validation."""


class AudioFrameValidationError(AudioValidationError):
    """Raised when raw PCM audio frames or sample values are malformed or non-finite."""


class AudioSequenceError(AudioError):
    """Raised when a non-monotonic or duplicate sequence number is detected within a session."""


class AudioEpochMismatchError(AudioError):
    """Raised when an ingested audio chunk's epoch does not match RuntimeContext.epoch_id."""


class AudioDeviceIdentityError(AudioError):
    """Raised when an audio chunk fails device registry or physical ID verification."""


class AudioPipelineError(AudioError):
    """Raised when an unrecoverable fault occurs during audio processing pipeline execution."""


class AudioResourceIntegrityError(AudioError, DeviceResourceIntegrityError):
    """Raised when internal structural resource handles or buffer invariants are corrupted."""


class AudioShutdownError(AudioError, DeviceShutdownError):
    """Raised when one or more structural resources fail to release cleanly during teardown."""

    def __init__(self, message: str, failures: Sequence[DeviceShutdownFailureRecord]) -> None:
        super().__init__(message, failures)
        self.failures: tuple[DeviceShutdownFailureRecord, ...] = tuple(failures)
