"""HoloMed AI - Device data plane models, constants, and data items."""

from __future__ import annotations

import enum
import re
import uuid
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, Callable, Mapping, Optional

from holomed.devices.data.exceptions import DataPayloadValidationError, DeviceDataValidationError
from holomed.devices.models import (
    DeviceType,
    MAX_CAPABILITY_CONTAINER_WIDTH,
    MAX_CAPABILITY_PARAMETER_DEPTH,
    MAX_CAPABILITY_STRING_LENGTH,
    MAX_CAPABILITY_TOTAL_NODES,
    MAX_CAPABILITY_TOTAL_UNITS,
    MAX_DEVICE_ID_LENGTH,
    MAX_SAFE_INTEGER,
    deep_freeze_parameter,
)

# -----------------------------------------------------------------------------
# Bounds & Constants
# -----------------------------------------------------------------------------
MAX_QUEUE_ITEMS: int = 256
MAX_REGISTERED_PROCESSORS: int = 256
MAX_TRACKED_DEVICE_STATS: int = 256

MAX_DATA_PAYLOAD_BYTES: int = 65536
MAX_DATA_PAYLOAD_DEPTH: int = MAX_CAPABILITY_PARAMETER_DEPTH  # 8
MAX_DATA_CONTAINER_WIDTH: int = MAX_CAPABILITY_CONTAINER_WIDTH  # 64
MAX_DATA_TOTAL_NODES: int = MAX_CAPABILITY_TOTAL_NODES  # 256
MAX_DATA_TOTAL_UNITS: int = MAX_CAPABILITY_TOTAL_UNITS  # 16384
MAX_DATA_STRING_LENGTH: int = MAX_CAPABILITY_STRING_LENGTH  # 1024


class DeviceDataKind(str, enum.Enum):
    """Categorization of device data observations and payloads."""

    OBSERVATION = "OBSERVATION"
    TELEMETRY = "TELEMETRY"
    STATUS = "STATUS"
    MEASUREMENT = "MEASUREMENT"
    DIAGNOSTIC = "DIAGNOSTIC"
    RESULT = "RESULT"


class ProcessingStatus(str, enum.Enum):
    """Lifecycle status of a device data item processing pipeline."""

    ACCEPTED = "ACCEPTED"
    PROCESSED = "PROCESSED"
    REJECTED = "REJECTED"
    FAILED = "FAILED"


def validate_uuid_v4_str(val: str, field_name: str) -> None:
    """Validate that val is a valid canonical lowercase UUIDv4 string."""
    if not isinstance(val, str):
        raise DeviceDataValidationError(f"{field_name} must be a string, got {type(val).__name__}")
    try:
        parsed = uuid.UUID(val, version=4)
        if str(parsed) != val.lower():
            raise ValueError()
    except Exception:
        raise DeviceDataValidationError(f"{field_name} must be a valid lowercase UUIDv4 string, got {val!r}")


@dataclass(frozen=True)
class DeviceData:
    """Immutable, canonical data item representing an observation or telemetry unit."""

    device_id: str
    physical_id: str
    device_type: DeviceType
    kind: DeviceDataKind
    sequence_number: int
    timestamp_utc: str
    payload: Mapping[str, Any]
    correlation_id: Optional[str] = None

    def __post_init__(self) -> None:
        if type(self.device_id) is not str or not (1 <= len(self.device_id) <= MAX_DEVICE_ID_LENGTH):
            raise DeviceDataValidationError(f"Invalid device_id syntax or length: {self.device_id!r}")
        if type(self.physical_id) is not str or not self.physical_id:
            raise DeviceDataValidationError(f"Invalid physical_id: {self.physical_id!r}")
        if not isinstance(self.device_type, DeviceType):
            raise DeviceDataValidationError(f"device_type must be a DeviceType enum, got {type(self.device_type).__name__}")
        if not isinstance(self.kind, DeviceDataKind):
            raise DeviceDataValidationError(f"kind must be a DeviceDataKind enum, got {type(self.kind).__name__}")

        if type(self.sequence_number) is not int or self.sequence_number < 0 or self.sequence_number > MAX_SAFE_INTEGER:
            raise DeviceDataValidationError(
                f"sequence_number must be int in range [0, {MAX_SAFE_INTEGER}], got {self.sequence_number!r}"
            )

        if type(self.timestamp_utc) is not str or not self.timestamp_utc:
            raise DeviceDataValidationError("timestamp_utc must be a non-empty string")

        if self.correlation_id is not None:
            validate_uuid_v4_str(self.correlation_id, "correlation_id")

        if not isinstance(self.payload, (dict, MappingProxyType)):
            raise DataPayloadValidationError(f"payload must be a mapping, got {type(self.payload).__name__}")

        # Deep freeze and canonicalize payload using M00.5 canonicalizer
        stats = {"nodes": 0, "units": 0}
        try:
            frozen = deep_freeze_parameter(dict(self.payload), depth=0, seen_ids=set(), stats=stats)
        except Exception as e:
            raise DataPayloadValidationError(f"DeviceData payload validation failed: {e}") from e

        object.__setattr__(self, "payload", frozen)


@dataclass(frozen=True)
class ProcessingReceipt:
    """Synchronous receipt returned upon successful queue submission."""

    device_id: str
    sequence_number: int
    status: ProcessingStatus
    queue_depth: int
    timestamp_utc: str


@dataclass(frozen=True)
class ProcessingResult:
    """Immutable outcome produced by processing a single DeviceData item."""

    device_id: str
    sequence_number: int
    status: ProcessingStatus
    payload: Mapping[str, Any]
    timestamp_utc: str
    correlation_id: Optional[str] = None
    error_code: Optional[str] = None
    error_message: Optional[str] = None

    def __post_init__(self) -> None:
        if self.error_code is not None:
            if type(self.error_code) is not str or not re.match(r"^ERR_[A-Z0-9_]+$", self.error_code):
                raise DeviceDataValidationError(f"error_code must match pattern '^ERR_[A-Z0-9_]+$', got {self.error_code!r}")

        if not isinstance(self.payload, (dict, MappingProxyType)):
            raise DataPayloadValidationError(f"payload must be a mapping, got {type(self.payload).__name__}")

        stats = {"nodes": 0, "units": 0}
        try:
            frozen = deep_freeze_parameter(dict(self.payload), depth=0, seen_ids=set(), stats=stats)
        except Exception as e:
            raise DataPayloadValidationError(f"ProcessingResult payload validation failed: {e}") from e

        object.__setattr__(self, "payload", frozen)


# Data Processor Callable
DataProcessor = Callable[[DeviceData], Mapping[str, Any]]
