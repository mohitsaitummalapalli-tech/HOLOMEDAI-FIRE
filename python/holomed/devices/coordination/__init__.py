"""HoloMed AI - Device state and telemetry coordination foundation."""

from __future__ import annotations

from holomed.devices.coordination.coordinator import DeviceCoordinationService
from holomed.devices.coordination.events import (
    IDeviceCoordinationEventSink,
    NullDeviceCoordinationEventSink,
    RecordingDeviceCoordinationEventSink,
)
from holomed.devices.coordination.exceptions import (
    DeviceCoordinationCapacityError,
    DeviceCoordinationDeviceNotFoundError,
    DeviceCoordinationEpochMismatchError,
    DeviceCoordinationError,
    DeviceCoordinationIdentityError,
    DeviceCoordinationIntegrityError,
    DeviceCoordinationLifecycleError,
    DeviceCoordinationSequenceError,
    DeviceCoordinationSerializationError,
    DeviceCoordinationShutdownError,
    DeviceCoordinationValidationError,
)
from holomed.devices.coordination.health import DeviceHealthAggregator
from holomed.devices.coordination.models import (
    MAX_COORDINATION_ERROR_MESSAGE_LENGTH,
    MAX_COORDINATION_PAYLOAD_BYTES,
    MAX_DEVICE_SNAPSHOT_SIZE,
    MAX_RECORDED_COORDINATION_EVENTS,
    MAX_TELEMETRY_DEVICES,
    DeviceObservation,
    DeviceSnapshot,
    DeviceTelemetryRecord,
    TelemetryStatus,
    convert_for_json_serialization,
    serialize_coordination_payload,
)
from holomed.devices.coordination.telemetry import TelemetryAccumulator

__all__ = [
    # Coordinator & Aggregators
    "DeviceCoordinationService",
    "DeviceHealthAggregator",
    "TelemetryAccumulator",
    # Models & Dataclasses
    "DeviceObservation",
    "DeviceSnapshot",
    "DeviceTelemetryRecord",
    "TelemetryStatus",
    # Event Sinks
    "IDeviceCoordinationEventSink",
    "NullDeviceCoordinationEventSink",
    "RecordingDeviceCoordinationEventSink",
    # Serialization
    "serialize_coordination_payload",
    "convert_for_json_serialization",
    # Constants
    "MAX_DEVICE_SNAPSHOT_SIZE",
    "MAX_TELEMETRY_DEVICES",
    "MAX_RECORDED_COORDINATION_EVENTS",
    "MAX_COORDINATION_PAYLOAD_BYTES",
    "MAX_COORDINATION_ERROR_MESSAGE_LENGTH",
    # Exceptions
    "DeviceCoordinationError",
    "DeviceCoordinationValidationError",
    "DeviceCoordinationLifecycleError",
    "DeviceCoordinationCapacityError",
    "DeviceCoordinationDeviceNotFoundError",
    "DeviceCoordinationEpochMismatchError",
    "DeviceCoordinationIdentityError",
    "DeviceCoordinationSequenceError",
    "DeviceCoordinationSerializationError",
    "DeviceCoordinationShutdownError",
    "DeviceCoordinationIntegrityError",
]
