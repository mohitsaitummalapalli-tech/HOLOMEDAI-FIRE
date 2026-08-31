"""HoloMed AI - Hardware device abstraction foundation exports."""

from __future__ import annotations

from holomed.devices.authority import DeviceResourceAuthority
from holomed.devices.discovery import StaticDiscoveryProvider
from holomed.devices.exceptions import (
    DeviceCapacityError,
    DeviceDiscoveryError,
    DeviceError,
    DeviceFactoryError,
    DeviceFactoryMissingError,
    DeviceLifecycleError,
    DeviceNotFoundError,
    DeviceResourceIntegrityError,
    DeviceResourceOwnershipError,
    DeviceSecurityError,
    DeviceShutdownError,
    DeviceValidationError,
    DuplicateDeviceError,
)
from holomed.devices.freshness import DeviceFreshnessTracker
from holomed.devices.interfaces import (
    DeviceDiscoveryProvider,
    DeviceResourceAccessor,
    IDevice,
    IDeviceEventSink,
    NullDeviceEventSink,
    RecordingDeviceEventSink,
    RegistryAuthorityToken,
)
from holomed.devices.manager import DeviceManager
from holomed.devices.models import (
    CapabilityCategory,
    DeviceCapability,
    DeviceDescriptor,
    DeviceFactory,
    DeviceHealth,
    DeviceRefreshReport,
    DeviceShutdownFailureRecord,
    DeviceState,
    DeviceType,
    MAX_CAPABILITIES_PER_DEVICE,
    MAX_CAPABILITY_CONTAINER_WIDTH,
    MAX_CAPABILITY_PARAMETER_DEPTH,
    MAX_CAPABILITY_STRING_LENGTH,
    MAX_CAPABILITY_TOTAL_NODES,
    MAX_CAPABILITY_TOTAL_UNITS,
    MAX_DEVICE_ID_LENGTH,
    MAX_DISCOVERY_BATCH_SIZE,
    MAX_HANDLES_PER_DEVICE,
    MAX_METADATA_ENTRIES,
    MAX_METADATA_TOTAL_BYTES,
    MAX_RECORDED_EVENTS,
    MAX_REGISTERED_DEVICES,
    MAX_RESOURCE_ID_LENGTH,
    MAX_RESOURCE_NAME_LENGTH,
    MAX_THEORETICAL_OWNED_RESOURCE_SET_HANDLES,
    MAX_TOTAL_DEVICE_HANDLES,
    deep_freeze_parameter,
)
from holomed.devices.registry import DeviceRegistry
from holomed.devices.simulated import SimulatedDevice

__all__ = [
    # Core Service & Manager
    "DeviceManager",
    "DeviceRegistry",
    "DeviceResourceAuthority",
    "DeviceResourceAccessor",
    "DeviceFreshnessTracker",
    "RegistryAuthorityToken",
    # Interfaces
    "IDevice",
    "IDeviceEventSink",
    "NullDeviceEventSink",
    "RecordingDeviceEventSink",
    "DeviceDiscoveryProvider",
    "StaticDiscoveryProvider",
    "SimulatedDevice",
    # Data Models & Enums
    "DeviceType",
    "CapabilityCategory",
    "DeviceState",
    "DeviceCapability",
    "DeviceDescriptor",
    "DeviceHealth",
    "DeviceShutdownFailureRecord",
    "DeviceRefreshReport",
    "DeviceFactory",
    "deep_freeze_parameter",
    # Constants & Limits
    "MAX_DEVICE_ID_LENGTH",
    "MAX_RESOURCE_NAME_LENGTH",
    "MAX_RESOURCE_ID_LENGTH",
    "MAX_HANDLES_PER_DEVICE",
    "MAX_REGISTERED_DEVICES",
    "MAX_TOTAL_DEVICE_HANDLES",
    "MAX_THEORETICAL_OWNED_RESOURCE_SET_HANDLES",
    "MAX_DISCOVERY_BATCH_SIZE",
    "MAX_CAPABILITIES_PER_DEVICE",
    "MAX_METADATA_ENTRIES",
    "MAX_METADATA_TOTAL_BYTES",
    "MAX_CAPABILITY_STRING_LENGTH",
    "MAX_CAPABILITY_CONTAINER_WIDTH",
    "MAX_CAPABILITY_TOTAL_NODES",
    "MAX_CAPABILITY_TOTAL_UNITS",
    "MAX_CAPABILITY_PARAMETER_DEPTH",
    "MAX_RECORDED_EVENTS",
    # Exceptions
    "DeviceError",
    "DeviceValidationError",
    "DeviceLifecycleError",
    "DuplicateDeviceError",
    "DeviceCapacityError",
    "DeviceNotFoundError",
    "DeviceFactoryError",
    "DeviceFactoryMissingError",
    "DeviceDiscoveryError",
    "DeviceResourceOwnershipError",
    "DeviceResourceIntegrityError",
    "DeviceSecurityError",
    "DeviceShutdownError",
]
