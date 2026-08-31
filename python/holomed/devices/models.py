"""HoloMed AI - Device data models, capability structures, and canonical constants."""

from __future__ import annotations

import enum
import math
import re
import unicodedata
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, Callable, Dict, Mapping, Optional, Set, Tuple

from holomed.devices.exceptions import DeviceValidationError
from holomed.runtime.models import HealthStatus

# Constant Limits
MAX_DEVICE_ID_LENGTH: int = 64
MAX_RESOURCE_NAME_LENGTH: int = 64
MAX_RESOURCE_ID_LENGTH: int = 256
MAX_HANDLES_PER_DEVICE: int = 32
MAX_REGISTERED_DEVICES: int = 256
MAX_TOTAL_DEVICE_HANDLES: int = 8192
MAX_THEORETICAL_OWNED_RESOURCE_SET_HANDLES: int = 8195
MAX_DISCOVERY_BATCH_SIZE: int = 256
MAX_CAPABILITIES_PER_DEVICE: int = 32
MAX_METADATA_ENTRIES: int = 32
MAX_METADATA_TOTAL_BYTES: int = 4096
MAX_CAPABILITY_STRING_LENGTH: int = 1024
MAX_CAPABILITY_CONTAINER_WIDTH: int = 64
MAX_CAPABILITY_TOTAL_NODES: int = 256
MAX_CAPABILITY_TOTAL_UNITS: int = 16384
MAX_CAPABILITY_PARAMETER_DEPTH: int = 8
MAX_RECORDED_EVENTS: int = 1000
MIN_SAFE_INTEGER: int = -9223372036854775808
MAX_SAFE_INTEGER: int = 9223372036854775807

DEVICE_ID_REGEX = re.compile(r"^[a-z0-9]([a-z0-9._-]*[a-z0-9])?$")
PHYSICAL_ID_REGEX = re.compile(r"^[a-zA-Z0-9]([a-zA-Z0-9._:/-]*[a-zA-Z0-9])?$")
CAPABILITY_ID_REGEX = re.compile(r"^[a-z0-9]+(\.[a-z0-9]+)*$")
PARAMETER_KEY_REGEX = re.compile(r"^[a-z0-9_]+$")
METADATA_KEY_REGEX = re.compile(r"^[a-zA-Z0-9_.-]+$")
RESOURCE_NAME_REGEX = re.compile(r"^[a-z0-9]([a-z0-9_-]*[a-z0-9])?$")


class DeviceType(str, enum.Enum):
    """Categorization of device hardware types."""

    RGB_CAMERA = "RGB_CAMERA"
    DEPTH_CAMERA = "DEPTH_CAMERA"
    AUDIO_MICROPHONE = "AUDIO_MICROPHONE"
    IMU_SENSOR = "IMU_SENSOR"
    HAPTIC_CONTROLLER = "HAPTIC_CONTROLLER"
    OPTICAL_TRACKER = "OPTICAL_TRACKER"
    SURGICAL_TOOL = "SURGICAL_TOOL"
    SIMULATED_GENERIC = "SIMULATED_GENERIC"


class CapabilityCategory(str, enum.Enum):
    """Semantic domain category of device capability."""

    STREAMING = "STREAMING"
    CONTROL = "CONTROL"
    CONFIGURATION = "CONFIGURATION"
    CALIBRATION = "CALIBRATION"
    DIAGNOSTICS = "DIAGNOSTICS"


class DeviceState(str, enum.Enum):
    """Authoritative 8-state machine for device lifecycle."""

    UNREGISTERED = "UNREGISTERED"
    REGISTERED = "REGISTERED"
    INITIALIZING = "INITIALIZING"
    READY = "READY"
    ACTIVE = "ACTIVE"
    STOPPING = "STOPPING"
    STOPPED = "STOPPED"
    FAILED = "FAILED"


def deep_freeze_parameter(
    val: Any,
    depth: int = 0,
    seen_ids: Optional[Set[int]] = None,
    stats: Optional[Dict[str, int]] = None,
) -> Any:
    """Recursively validates and freezes capability parameter structures into immutable forms.

    Enforces:
    - bool before int check
    - signed 64-bit integer range [-2^63, 2^63 - 1]
    - NaN and infinity float rejection
    - negative zero normalization (-0.0 -> 0.0)
    - Unicode NFC normalization on string values
    - key-type verification before dictionary key sorting
    - containers restricted to frozenset, tuple, MappingProxyType
    - frozensets restricted to hashable primitives only
    - maximum container width 64
    - maximum nesting depth 8
    - maximum total tree nodes 256
    - maximum Logical Structural Cost Units 16384 (16 KiB)
    """
    if seen_ids is None:
        seen_ids = set()
    if stats is None:
        stats = {"nodes": 0, "units": 0}

    stats["nodes"] += 1
    if stats["nodes"] > MAX_CAPABILITY_TOTAL_NODES:
        raise DeviceValidationError(f"Capability parameters node count exceeds maximum ({MAX_CAPABILITY_TOTAL_NODES})")
    if depth > MAX_CAPABILITY_PARAMETER_DEPTH:
        raise DeviceValidationError(f"Capability parameter nesting depth exceeds maximum ({MAX_CAPABILITY_PARAMETER_DEPTH})")

    # 1. Primitives
    if val is None:
        stats["units"] += 4
        if stats["units"] > MAX_CAPABILITY_TOTAL_UNITS:
            raise DeviceValidationError(f"Capability parameter cost units exceed maximum ({MAX_CAPABILITY_TOTAL_UNITS})")
        return None

    # CRITICAL: bool is a subclass of int in Python; MUST check bool before int!
    if isinstance(val, bool):
        stats["units"] += 4
        if stats["units"] > MAX_CAPABILITY_TOTAL_UNITS:
            raise DeviceValidationError(f"Capability parameter cost units exceed maximum ({MAX_CAPABILITY_TOTAL_UNITS})")
        return val

    if isinstance(val, int):
        if not (MIN_SAFE_INTEGER <= val <= MAX_SAFE_INTEGER):
            raise DeviceValidationError(f"Integer value out of safe 64-bit range: {val}")
        stats["units"] += 8
        if stats["units"] > MAX_CAPABILITY_TOTAL_UNITS:
            raise DeviceValidationError(f"Capability parameter cost units exceed maximum ({MAX_CAPABILITY_TOTAL_UNITS})")
        return val

    if isinstance(val, float):
        if math.isnan(val) or math.isinf(val):
            raise DeviceValidationError("NaN and Infinite float values are strictly forbidden in capability parameters")
        # Normalize negative zero to positive zero
        norm_val = 0.0 if val == 0.0 else val
        stats["units"] += 8
        if stats["units"] > MAX_CAPABILITY_TOTAL_UNITS:
            raise DeviceValidationError(f"Capability parameter cost units exceed maximum ({MAX_CAPABILITY_TOTAL_UNITS})")
        return norm_val

    if isinstance(val, str):
        norm_str = unicodedata.normalize("NFC", val)
        b_len = len(norm_str.encode("utf-8"))
        if b_len > MAX_CAPABILITY_STRING_LENGTH:
            raise DeviceValidationError(f"Parameter string byte length ({b_len}) exceeds maximum ({MAX_CAPABILITY_STRING_LENGTH})")
        stats["units"] += b_len
        if stats["units"] > MAX_CAPABILITY_TOTAL_UNITS:
            raise DeviceValidationError(f"Capability parameter cost units exceed maximum ({MAX_CAPABILITY_TOTAL_UNITS})")
        return norm_str

    # 2. Containers (Cycle & Type Validation)
    val_id = id(val)
    if val_id in seen_ids:
        raise DeviceValidationError("Cyclic reference detected in capability parameters")
    seen_ids.add(val_id)

    try:
        if isinstance(val, (dict, MappingProxyType)):
            if len(val) > MAX_CAPABILITY_CONTAINER_WIDTH:
                raise DeviceValidationError(f"Mapping container width ({len(val)}) exceeds maximum ({MAX_CAPABILITY_CONTAINER_WIDTH})")
            # Step A: Validate ALL keys are str FIRST before sorting (prevents TypeError on heterogeneous keys)
            for k in val.keys():
                if type(k) is not str:
                    raise DeviceValidationError(f"Parameter key must be exact str, got {type(k).__name__}")
                if not PARAMETER_KEY_REGEX.match(k) or not (1 <= len(k) <= 64):
                    raise DeviceValidationError(f"Invalid parameter key syntax or length: '{k}'")

            frozen_dict: Dict[str, Any] = {}
            for k in sorted(val.keys()):
                k_bytes = len(k.encode("utf-8"))
                stats["units"] += k_bytes + 8  # key bytes + entry overhead
                if stats["units"] > MAX_CAPABILITY_TOTAL_UNITS:
                    raise DeviceValidationError(f"Capability parameter cost units exceed maximum ({MAX_CAPABILITY_TOTAL_UNITS})")
                frozen_dict[k] = deep_freeze_parameter(val[k], depth + 1, seen_ids, stats)
            return MappingProxyType(frozen_dict)

        elif isinstance(val, (list, tuple)):
            if len(val) > MAX_CAPABILITY_CONTAINER_WIDTH:
                raise DeviceValidationError(f"Sequence container width ({len(val)}) exceeds maximum ({MAX_CAPABILITY_CONTAINER_WIDTH})")
            frozen_seq = []
            for item in val:
                stats["units"] += 8  # element overhead
                if stats["units"] > MAX_CAPABILITY_TOTAL_UNITS:
                    raise DeviceValidationError(f"Capability parameter cost units exceed maximum ({MAX_CAPABILITY_TOTAL_UNITS})")
                frozen_seq.append(deep_freeze_parameter(item, depth + 1, seen_ids, stats))
            return tuple(frozen_seq)

        elif isinstance(val, (set, frozenset)):
            if len(val) > MAX_CAPABILITY_CONTAINER_WIDTH:
                raise DeviceValidationError(f"Set container width ({len(val)}) exceeds maximum ({MAX_CAPABILITY_CONTAINER_WIDTH})")
            # Enforce sets contain ONLY hashable primitives
            frozen_set_items = []
            for item in val:
                if item is not None and not isinstance(item, (str, int, float, bool)):
                    raise DeviceValidationError(f"Nested container or complex type '{type(item).__name__}' inside set is strictly forbidden")
                stats["units"] += 8  # element overhead
                if stats["units"] > MAX_CAPABILITY_TOTAL_UNITS:
                    raise DeviceValidationError(f"Capability parameter cost units exceed maximum ({MAX_CAPABILITY_TOTAL_UNITS})")
                frozen_set_items.append(deep_freeze_parameter(item, depth + 1, seen_ids, stats))
            return frozenset(frozen_set_items)

        else:
            raise DeviceValidationError(f"Unsupported capability parameter type: {type(val).__name__}")
    finally:
        seen_ids.remove(val_id)


@dataclass(frozen=True)
class DeviceCapability:
    """Immutable declaration of a specific capability exposed by a device."""

    capability_id: str
    category: CapabilityCategory
    parameters: Mapping[str, Any]

    def __post_init__(self) -> None:
        if type(self.capability_id) is not str or not CAPABILITY_ID_REGEX.match(self.capability_id) or not (1 <= len(self.capability_id) <= 128):
            raise DeviceValidationError(f"Invalid capability_id: '{self.capability_id}'")
        if not isinstance(self.category, CapabilityCategory):
            raise DeviceValidationError(f"Invalid capability category: {self.category}")
        if not isinstance(self.parameters, (dict, MappingProxyType)):
            raise DeviceValidationError(f"Capability parameters must be a mapping, got {type(self.parameters).__name__}")

        frozen = deep_freeze_parameter(self.parameters)
        object.__setattr__(self, "parameters", frozen)


@dataclass(frozen=True)
class DeviceDescriptor:
    """Authoritative, deeply immutable specification of device identity and capabilities."""

    device_id: str
    physical_id: str
    device_type: DeviceType
    capabilities: Tuple[DeviceCapability, ...]
    metadata: Mapping[str, str]

    def __post_init__(self) -> None:
        # 1. device_id validation
        if type(self.device_id) is not str or not DEVICE_ID_REGEX.match(self.device_id) or not (1 <= len(self.device_id) <= MAX_DEVICE_ID_LENGTH):
            raise DeviceValidationError(f"Invalid device_id '{self.device_id}': must match {DEVICE_ID_REGEX.pattern} and length 1..{MAX_DEVICE_ID_LENGTH}")

        # 2. physical_id validation
        if type(self.physical_id) is not str or not PHYSICAL_ID_REGEX.match(self.physical_id) or not (1 <= len(self.physical_id) <= 256):
            raise DeviceValidationError(f"Invalid physical_id '{self.physical_id}': must match {PHYSICAL_ID_REGEX.pattern} and length 1..256")

        # 3. device_type validation
        if not isinstance(self.device_type, DeviceType):
            raise DeviceValidationError(f"Invalid device_type: {self.device_type}")

        # 4. metadata validation
        if not isinstance(self.metadata, (dict, MappingProxyType)):
            raise DeviceValidationError(f"Metadata must be a mapping, got {type(self.metadata).__name__}")
        if len(self.metadata) > MAX_METADATA_ENTRIES:
            raise DeviceValidationError(f"Metadata entries count ({len(self.metadata)}) exceeds maximum ({MAX_METADATA_ENTRIES})")

        total_bytes = 0
        clean_metadata: Dict[str, str] = {}
        for k, v in self.metadata.items():
            if type(k) is not str or not METADATA_KEY_REGEX.match(k) or not (1 <= len(k) <= 64):
                raise DeviceValidationError(f"Invalid metadata key '{k}'")
            if type(v) is not str:
                raise DeviceValidationError(f"Metadata value for '{k}' must be str, got {type(v).__name__}")
            norm_v = unicodedata.normalize("NFC", v)
            k_bytes = len(k.encode("utf-8"))
            v_bytes = len(norm_v.encode("utf-8"))
            total_bytes += k_bytes + v_bytes
            clean_metadata[k] = norm_v

        if total_bytes > MAX_METADATA_TOTAL_BYTES:
            raise DeviceValidationError(f"Metadata total bytes ({total_bytes}) exceeds maximum ({MAX_METADATA_TOTAL_BYTES})")
        object.__setattr__(self, "metadata", MappingProxyType(clean_metadata))

        # 5. capabilities validation & canonical sorting
        if not isinstance(self.capabilities, (tuple, list)):
            raise DeviceValidationError(f"Capabilities must be sequence, got {type(self.capabilities).__name__}")
        if len(self.capabilities) > MAX_CAPABILITIES_PER_DEVICE:
            raise DeviceValidationError(f"Capabilities count ({len(self.capabilities)}) exceeds maximum ({MAX_CAPABILITIES_PER_DEVICE})")

        seen_cap_ids: Set[str] = set()
        clean_caps = []
        for cap in self.capabilities:
            if not isinstance(cap, DeviceCapability):
                raise DeviceValidationError(f"Capability element must be DeviceCapability, got {type(cap).__name__}")
            if cap.capability_id in seen_cap_ids:
                raise DeviceValidationError(f"Duplicate capability_id '{cap.capability_id}' in descriptor")
            seen_cap_ids.add(cap.capability_id)
            clean_caps.append(cap)

        clean_caps.sort(key=lambda c: c.capability_id)
        object.__setattr__(self, "capabilities", tuple(clean_caps))


@dataclass(frozen=True)
class DeviceHealth:
    """Synchronous in-process diagnostic snapshot of a single device."""

    device_id: str
    status: HealthStatus
    message: str
    timestamp_utc: str
    diagnostics: Mapping[str, Any] = MappingProxyType({})


@dataclass(frozen=True)
class DeviceShutdownFailureRecord:
    """Public diagnostic record of a device or structural handle teardown failure."""

    device_id: str
    error_type: str
    error_message: str
    execution_index: int
    unreleased_resources: Tuple[str, ...]


@dataclass(frozen=True)
class DeviceRefreshReport:
    """Outcome report produced by DeviceManager.refresh_devices()."""

    unchanged: Tuple[str, ...]
    added: Tuple[str, ...]
    removed: Tuple[str, ...]
    failed: Mapping[str, str]


DeviceFactory = Callable[[DeviceDescriptor], Any]
