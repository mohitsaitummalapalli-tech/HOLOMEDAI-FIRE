"""HoloMed AI - Device coordination models, constants, and serialization engine."""

from __future__ import annotations

import enum
import json
import math
import unicodedata
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple, Union

from holomed.devices.coordination.exceptions import (
    DeviceCoordinationCapacityError,
    DeviceCoordinationSerializationError,
    DeviceCoordinationValidationError,
)
from holomed.devices.models import (
    MAX_DEVICE_ID_LENGTH,
    MAX_SAFE_INTEGER,
    DeviceState,
    DeviceType,
)
from holomed.runtime.models import HealthStatus

# -----------------------------------------------------------------------------
# Authoritative Constants & Bounds
# -----------------------------------------------------------------------------
MAX_DEVICE_SNAPSHOT_SIZE: int = 256
MAX_TELEMETRY_DEVICES: int = 256
MAX_RECORDED_COORDINATION_EVENTS: int = 1000
MAX_COORDINATION_PAYLOAD_BYTES: int = 65536
MAX_COORDINATION_ERROR_MESSAGE_LENGTH: int = 1024


class TelemetryStatus(str, enum.Enum):
    """Canonical processing status for device telemetry aggregation."""

    SUBMITTED = "SUBMITTED"
    PROCESSED = "PROCESSED"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    REJECTED = "REJECTED"


@dataclass(frozen=True)
class DeviceObservation:
    """Immutable, bounded snapshot observation of a single registered device."""

    device_id: str
    physical_id: str
    device_type: DeviceType
    state: DeviceState
    health_status: HealthStatus
    health_message: str
    observed_at_utc: str
    epoch_id: int

    def __post_init__(self) -> None:
        if type(self.device_id) is not str or not (1 <= len(self.device_id) <= MAX_DEVICE_ID_LENGTH):
            raise DeviceCoordinationValidationError(f"Invalid device_id syntax or length: {self.device_id!r}")
        if type(self.physical_id) is not str or not self.physical_id:
            raise DeviceCoordinationValidationError(f"Invalid physical_id: {self.physical_id!r}")
        if not isinstance(self.device_type, DeviceType):
            raise DeviceCoordinationValidationError(f"device_type must be a DeviceType enum, got {type(self.device_type).__name__}")
        if not isinstance(self.state, DeviceState):
            raise DeviceCoordinationValidationError(f"state must be a DeviceState enum, got {type(self.state).__name__}")
        if not isinstance(self.health_status, HealthStatus):
            raise DeviceCoordinationValidationError(f"health_status must be a HealthStatus enum, got {type(self.health_status).__name__}")

        if type(self.health_message) is not str:
            raise DeviceCoordinationValidationError(f"health_message must be str, got {type(self.health_message).__name__}")
        norm_msg = unicodedata.normalize("NFC", self.health_message)
        if len(norm_msg) > MAX_COORDINATION_ERROR_MESSAGE_LENGTH:
            raise DeviceCoordinationValidationError(
                f"health_message exceeds maximum length ({MAX_COORDINATION_ERROR_MESSAGE_LENGTH})"
            )
        object.__setattr__(self, "health_message", norm_msg)

        if type(self.observed_at_utc) is not str or not self.observed_at_utc:
            raise DeviceCoordinationValidationError("observed_at_utc must be a non-empty ISO 8601 string")

        if type(self.epoch_id) is not int or self.epoch_id < 0 or self.epoch_id > MAX_SAFE_INTEGER:
            raise DeviceCoordinationValidationError(f"epoch_id must be non-negative safe int, got {self.epoch_id!r}")

    def to_dict(self) -> Dict[str, Any]:
        """Convert observation to a primitive JSON-serializable dictionary."""
        return {
            "device_id": self.device_id,
            "physical_id": self.physical_id,
            "device_type": self.device_type.name,
            "state": self.state.name,
            "health_status": self.health_status.name,
            "health_message": self.health_message,
            "observed_at_utc": self.observed_at_utc,
            "epoch_id": self.epoch_id,
        }


@dataclass(frozen=True)
class DeviceSnapshot:
    """Authoritative, deeply immutable aggregate snapshot of device observations."""

    epoch_id: int
    captured_at_utc: str
    observations: Tuple[DeviceObservation, ...]

    def __post_init__(self) -> None:
        if type(self.epoch_id) is not int or self.epoch_id < 0 or self.epoch_id > MAX_SAFE_INTEGER:
            raise DeviceCoordinationValidationError(f"epoch_id must be non-negative safe int, got {self.epoch_id!r}")
        if type(self.captured_at_utc) is not str or not self.captured_at_utc:
            raise DeviceCoordinationValidationError("captured_at_utc must be a non-empty ISO 8601 string")

        if not isinstance(self.observations, (tuple, list)):
            raise DeviceCoordinationValidationError(f"observations must be a sequence, got {type(self.observations).__name__}")
        if len(self.observations) > MAX_DEVICE_SNAPSHOT_SIZE:
            raise DeviceCoordinationCapacityError(
                f"DeviceSnapshot observations count ({len(self.observations)}) exceeds maximum ({MAX_DEVICE_SNAPSHOT_SIZE})"
            )

        for obs in self.observations:
            if not isinstance(obs, DeviceObservation):
                raise DeviceCoordinationValidationError(f"Observation element must be DeviceObservation, got {type(obs).__name__}")

        # Canonical ordering: (device_type.name ASC, device_id ASC)
        sorted_obs = sorted(self.observations, key=lambda o: (o.device_type.name, o.device_id))
        object.__setattr__(self, "observations", tuple(sorted_obs))

    def to_dict(self) -> Dict[str, Any]:
        """Convert snapshot to a primitive JSON-serializable dictionary."""
        return {
            "epoch_id": self.epoch_id,
            "captured_at_utc": self.captured_at_utc,
            "observations": [obs.to_dict() for obs in self.observations],
        }


@dataclass(frozen=True)
class DeviceTelemetryRecord:
    """Immutable per-device aggregated telemetry statistics record."""

    device_id: str
    physical_id: str
    epoch_id: int
    submitted_items: int
    processed_items: int
    completed_items: int
    failed_items: int
    rejected_items: int
    last_sequence_number: int
    last_observation_timestamp: str

    def __post_init__(self) -> None:
        if type(self.device_id) is not str or not (1 <= len(self.device_id) <= MAX_DEVICE_ID_LENGTH):
            raise DeviceCoordinationValidationError(f"Invalid device_id: {self.device_id!r}")
        if type(self.physical_id) is not str or not self.physical_id:
            raise DeviceCoordinationValidationError(f"Invalid physical_id: {self.physical_id!r}")
        if type(self.epoch_id) is not int or self.epoch_id < 0 or self.epoch_id > MAX_SAFE_INTEGER:
            raise DeviceCoordinationValidationError(f"epoch_id must be non-negative safe int, got {self.epoch_id!r}")

        for name, val in (
            ("submitted_items", self.submitted_items),
            ("processed_items", self.processed_items),
            ("completed_items", self.completed_items),
            ("failed_items", self.failed_items),
            ("rejected_items", self.rejected_items),
            ("last_sequence_number", self.last_sequence_number),
        ):
            if type(val) is not int or not (0 <= val <= MAX_SAFE_INTEGER):
                raise DeviceCoordinationValidationError(f"{name} must be int in range [0, {MAX_SAFE_INTEGER}], got {val!r}")

        if type(self.last_observation_timestamp) is not str or not self.last_observation_timestamp:
            raise DeviceCoordinationValidationError("last_observation_timestamp must be non-empty string")

    def to_dict(self) -> Dict[str, Any]:
        """Convert telemetry record to a primitive dictionary."""
        return {
            "device_id": self.device_id,
            "physical_id": self.physical_id,
            "epoch_id": self.epoch_id,
            "submitted_items": self.submitted_items,
            "processed_items": self.processed_items,
            "completed_items": self.completed_items,
            "failed_items": self.failed_items,
            "rejected_items": self.rejected_items,
            "last_sequence_number": self.last_sequence_number,
            "last_observation_timestamp": self.last_observation_timestamp,
        }


def convert_for_json_serialization(val: Any) -> Any:
    """Recursively convert nested immutable structures into standard JSON types.

    Enforces:
    - MappingProxyType / dict -> dict with sorted keys
    - tuple / list / set / frozenset -> list
    - float NaN / Infinity rejection
    - float -0.0 normalization to +0.0
    - str Unicode NFC normalization
    """
    if val is None or isinstance(val, (bool, int)):
        return val

    if isinstance(val, float):
        if math.isnan(val) or math.isinf(val):
            raise DeviceCoordinationSerializationError("NaN and Infinite floats forbidden in serialized coordination payload")
        return 0.0 if val == 0.0 else val

    if isinstance(val, str):
        return unicodedata.normalize("NFC", val)

    if isinstance(val, (dict, MappingProxyType)):
        clean_dict: Dict[str, Any] = {}
        for k in sorted(val.keys()):
            if not isinstance(k, str):
                raise DeviceCoordinationSerializationError(f"Dictionary key must be string, got {type(k).__name__}")
            clean_dict[unicodedata.normalize("NFC", k)] = convert_for_json_serialization(val[k])
        return clean_dict

    if isinstance(val, (list, tuple, set, frozenset)):
        return [convert_for_json_serialization(item) for item in val]

    if hasattr(val, "to_dict") and callable(val.to_dict):
        return convert_for_json_serialization(val.to_dict())

    raise DeviceCoordinationSerializationError(f"Unsupported non-JSON type encountered: {type(val).__name__}")


def serialize_coordination_payload(data: Any) -> str:
    """Deterministically serialize payload to JSON enforcing MAX_COORDINATION_PAYLOAD_BYTES (64 KiB)."""
    try:
        converted = convert_for_json_serialization(data)
        serialized = json.dumps(converted, separators=(",", ":"), sort_keys=True, ensure_ascii=True)
    except DeviceCoordinationSerializationError:
        raise
    except Exception as e:
        raise DeviceCoordinationSerializationError(f"JSON serialization failed: {e}") from e

    encoded = serialized.encode("utf-8")
    if len(encoded) > MAX_COORDINATION_PAYLOAD_BYTES:
        raise DeviceCoordinationSerializationError(
            f"Serialized coordination payload size ({len(encoded)} bytes) exceeds maximum ({MAX_COORDINATION_PAYLOAD_BYTES})"
        )
    return serialized
