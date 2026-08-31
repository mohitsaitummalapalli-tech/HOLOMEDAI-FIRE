"""HoloMed AI - Device data plane and telemetry processing exports."""

from __future__ import annotations

from holomed.devices.data.events import (
    IDeviceDataEventSink,
    NullDeviceDataEventSink,
    RecordingDeviceDataEventSink,
)
from holomed.devices.data.exceptions import (
    DataPayloadValidationError,
    DataProcessorCapacityError,
    DataProcessorError,
    DeviceDataCapacityError,
    DeviceDataEpochMismatchError,
    DeviceDataError,
    DeviceDataLifecycleError,
    DeviceDataNotFoundError,
    DeviceDataValidationError,
    DeviceIdentityMismatchError,
    DeviceStaleInstanceError,
    DuplicateDataSequenceError,
    OutOfOrderDataError,
)
from holomed.devices.data.models import (
    DataProcessor,
    DeviceData,
    DeviceDataKind,
    MAX_DATA_CONTAINER_WIDTH,
    MAX_DATA_PAYLOAD_BYTES,
    MAX_DATA_PAYLOAD_DEPTH,
    MAX_DATA_STRING_LENGTH,
    MAX_DATA_TOTAL_NODES,
    MAX_DATA_TOTAL_UNITS,
    MAX_QUEUE_ITEMS,
    MAX_REGISTERED_PROCESSORS,
    MAX_TRACKED_DEVICE_STATS,
    ProcessingReceipt,
    ProcessingResult,
    ProcessingStatus,
)
from holomed.devices.data.processor import DeviceDataProcessor
from holomed.devices.data.queue import BoundedDeviceDataQueue
from holomed.devices.data.registry import DeviceDataProcessorRegistry

__all__ = [
    # Processor & Core Types
    "DeviceDataProcessor",
    "BoundedDeviceDataQueue",
    "DeviceDataProcessorRegistry",
    "DeviceData",
    "DeviceDataKind",
    "ProcessingReceipt",
    "ProcessingResult",
    "ProcessingStatus",
    "DataProcessor",
    # Event Sinks
    "IDeviceDataEventSink",
    "NullDeviceDataEventSink",
    "RecordingDeviceDataEventSink",
    # Constants
    "MAX_QUEUE_ITEMS",
    "MAX_REGISTERED_PROCESSORS",
    "MAX_TRACKED_DEVICE_STATS",
    "MAX_DATA_PAYLOAD_BYTES",
    "MAX_DATA_PAYLOAD_DEPTH",
    "MAX_DATA_CONTAINER_WIDTH",
    "MAX_DATA_TOTAL_NODES",
    "MAX_DATA_TOTAL_UNITS",
    "MAX_DATA_STRING_LENGTH",
    # Exceptions
    "DeviceDataError",
    "DeviceDataValidationError",
    "DeviceDataLifecycleError",
    "DeviceDataCapacityError",
    "DeviceDataNotFoundError",
    "DeviceIdentityMismatchError",
    "DeviceStaleInstanceError",
    "DeviceDataEpochMismatchError",
    "DuplicateDataSequenceError",
    "OutOfOrderDataError",
    "DataProcessorError",
    "DataProcessorCapacityError",
    "DataPayloadValidationError",
]
