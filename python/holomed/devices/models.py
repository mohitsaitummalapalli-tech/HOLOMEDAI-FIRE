"""HoloMed AI - Device data models, capability structures, and canonical constants."""

from __future__ import annotations

import enum
import math
import re
import unicodedata
from dataclasses import dataclass, field
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


class EndpointSafetyState(str, enum.Enum):
    """M48 Physical safety state of an endpoint."""

    SAFE_STOPPED = "SAFE_STOPPED"
    ACTIVE = "ACTIVE"
    HARDWARE_INTERLOCKED = "HARDWARE_INTERLOCKED"


class CommandState(str, enum.Enum):
    """Strict formal state machine for physical execution."""

    ACCEPTED = "ACCEPTED"
    RUNNING = "RUNNING"
    PREEMPT_REQUESTED = "PREEMPT_REQUESTED"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    PREEMPTED = "PREEMPTED"
    INTERLOCKED = "INTERLOCKED"
    FAULTED_UNKNOWN = "FAULTED_UNKNOWN"


class EndpointState(str, enum.Enum):
    """Authoritative lifecycle condition of a physical endpoint."""

    READY = "READY"
    QUARANTINED = "QUARANTINED"


class SubmissionStatus(str, enum.Enum):
    """Strict deterministic admission result for non-blocking submission."""

    ACCEPTED = "ACCEPTED"
    QUEUE_FULL = "QUEUE_FULL"
    WORKER_UNAVAILABLE = "WORKER_UNAVAILABLE"
    SHUTTING_DOWN = "SHUTTING_DOWN"
    DUPLICATE_REJECTED = "DUPLICATE_REJECTED"


class EventSourceAuthority(str, enum.Enum):
    """Authoritative source of a physical telemetry event."""

    HARDWARE_DRIVER = "HARDWARE_DRIVER"
    ENDPOINT_ADAPTER = "ENDPOINT_ADAPTER"
    CONTROL_PLANE_TIMEOUT = "CONTROL_PLANE_TIMEOUT"



@dataclass(frozen=True)
class EndpointLease:
    """M48 Lease identity representing an authorized physical execution context."""

    endpoint_id: str
    device_id: str
    session_id: str
    lifecycle_generation: int
    endpoint_lease_generation: int
    execution_id: str
    capability_scope: frozenset[str]

    def __post_init__(self) -> None:
        if not isinstance(self.endpoint_id, str) or not self.endpoint_id:
            raise DeviceValidationError("endpoint_id must be a non-empty string")
        if not isinstance(self.device_id, str) or not self.device_id:
            raise DeviceValidationError("device_id must be a non-empty string")
        if not isinstance(self.session_id, str) or not self.session_id:
            raise DeviceValidationError("session_id must be a non-empty string")
        if type(self.lifecycle_generation) is not int or self.lifecycle_generation < 1:
            raise DeviceValidationError("lifecycle_generation must be an int >= 1")
        if type(self.endpoint_lease_generation) is not int or self.endpoint_lease_generation < 1:
            raise DeviceValidationError("endpoint_lease_generation must be an int >= 1")
        if not isinstance(self.execution_id, str) or not self.execution_id:
            raise DeviceValidationError("execution_id must be a non-empty string")
        if not isinstance(self.capability_scope, frozenset):
            raise DeviceValidationError("capability_scope must be a frozenset of strings")


@dataclass(frozen=True)
class PhysicalCommand:
    """M49 Canonical physical authorization and execution context."""

    endpoint_id: str
    session_id: str
    lifecycle_generation: int
    endpoint_lease_generation: int
    execution_id: str
    capability_scope: frozenset[str]
    command_sequence: int
    operation: str
    parameters: Mapping[str, Any]

    def __post_init__(self) -> None:
        if type(self.endpoint_id) is not str or not self.endpoint_id.strip():
            raise DeviceValidationError("endpoint_id must be a non-empty string")
        if type(self.session_id) is not str or not self.session_id.strip():
            raise DeviceValidationError("session_id must be a non-empty string")
        if type(self.lifecycle_generation) is not int or self.lifecycle_generation < 1:
            raise DeviceValidationError("lifecycle_generation must be an int >= 1")
        if type(self.endpoint_lease_generation) is not int or self.endpoint_lease_generation < 1:
            raise DeviceValidationError("endpoint_lease_generation must be an int >= 1")
        if type(self.execution_id) is not str or not self.execution_id.strip():
            raise DeviceValidationError("execution_id must be a non-empty string")
        if not isinstance(self.capability_scope, frozenset):
            raise DeviceValidationError("capability_scope must be a frozenset")
        if any(type(x) is not str for x in self.capability_scope):
            raise DeviceValidationError("capability_scope must contain only strings")
        if type(self.command_sequence) is not int or self.command_sequence < 1:
            raise DeviceValidationError("command_sequence must be an int >= 1")
        if type(self.operation) is not str or not self.operation.strip():
            raise DeviceValidationError("operation must be a non-empty string")
        if not isinstance(self.parameters, (dict, MappingProxyType)):
            raise DeviceValidationError(f"Parameters must be a mapping, got {type(self.parameters).__name__}")

        frozen = deep_freeze_parameter(self.parameters)
        object.__setattr__(self, "parameters", frozen)

    def __reduce__(self) -> Any:
        return (
            self.__class__,
            (
                self.endpoint_id,
                self.session_id,
                self.lifecycle_generation,
                self.endpoint_lease_generation,
                self.execution_id,
                self.capability_scope,
                self.command_sequence,
                self.operation,
                dict(self.parameters),
            )
        )


@dataclass(frozen=True)
class PhysicalCommandResult:
    """Strongly typed result of a physical command actuation."""

    status: SubmissionStatus
    details: Mapping[str, Any] = field(default_factory=lambda: MappingProxyType({}))

    def __post_init__(self) -> None:
        if not isinstance(self.status, SubmissionStatus):
            raise DeviceValidationError("status must be SubmissionStatus")
        if not isinstance(self.details, (dict, MappingProxyType)):
            raise DeviceValidationError(f"details must be a mapping, got {type(self.details).__name__}")

        frozen = deep_freeze_parameter(self.details)
        object.__setattr__(self, "details", frozen)

    def __reduce__(self) -> Any:
        return (
            self.__class__,
            (
                self.status,
                dict(self.details),
            )
        )


@dataclass(frozen=True)
class ExecutionTelemetryEvent:
    """Immutable physical execution telemetry event."""

    event_id: str
    endpoint_id: str
    session_id: str
    lifecycle_generation: int
    endpoint_lease_generation: int
    execution_id: str
    command_sequence: int
    event_sequence: int
    event_type: str
    observed_state: CommandState
    source_authority: EventSourceAuthority
    source_origin: str
    timestamp_utc: str
    payload: Mapping[str, Any]

    def __post_init__(self) -> None:
        if type(self.event_id) is not str or not self.event_id.strip():
            raise DeviceValidationError("event_id must be a non-empty string")
        if type(self.endpoint_id) is not str or not self.endpoint_id.strip():
            raise DeviceValidationError("endpoint_id must be a non-empty string")
        if type(self.session_id) is not str or not self.session_id.strip():
            raise DeviceValidationError("session_id must be a non-empty string")
        if type(self.lifecycle_generation) is not int or self.lifecycle_generation < 1:
            raise DeviceValidationError("lifecycle_generation must be an int >= 1")
        if type(self.endpoint_lease_generation) is not int or self.endpoint_lease_generation < 1:
            raise DeviceValidationError("endpoint_lease_generation must be an int >= 1")
        if type(self.execution_id) is not str or not self.execution_id.strip():
            raise DeviceValidationError("execution_id must be a non-empty string")
        if type(self.command_sequence) is not int or self.command_sequence < 1:
            raise DeviceValidationError("command_sequence must be an int >= 1")
        if type(self.event_sequence) is not int or self.event_sequence < 1:
            raise DeviceValidationError("event_sequence must be an int >= 1")
        if type(self.event_type) is not str or not self.event_type.strip():
            raise DeviceValidationError("event_type must be a non-empty string")
        if not isinstance(self.observed_state, CommandState):
            raise DeviceValidationError("observed_state must be CommandState")
        if not isinstance(self.source_authority, EventSourceAuthority):
            raise DeviceValidationError("source_authority must be EventSourceAuthority")
        if type(self.source_origin) is not str or not self.source_origin.strip():
            raise DeviceValidationError("source_origin must be a non-empty string")
        if type(self.timestamp_utc) is not str or not self.timestamp_utc.strip():
            raise DeviceValidationError("timestamp_utc must be a non-empty string")
        if not isinstance(self.payload, (dict, MappingProxyType)):
            raise DeviceValidationError(f"payload must be a mapping, got {type(self.payload).__name__}")

        if self.source_authority == EventSourceAuthority.HARDWARE_DRIVER:
            if "SimulatedPhysicalEndpoint" in self.source_origin:
                raise DeviceValidationError(
                    f"Illegal authority combination: {self.source_authority.name} cannot originate from {self.source_origin}"
                )

        frozen = deep_freeze_parameter(self.payload)
        object.__setattr__(self, "payload", frozen)

    def __reduce__(self) -> Any:
        return (
            self.__class__,
            (
                self.event_id,
                self.endpoint_id,
                self.session_id,
                self.lifecycle_generation,
                self.endpoint_lease_generation,
                self.execution_id,
                self.command_sequence,
                self.event_sequence,
                self.event_type,
                self.observed_state,
                self.source_authority,
                self.source_origin,
                self.timestamp_utc,
                dict(self.payload),
            )
        )


class AuthoritativeExecutionRecord:
    """Authoritative state record owned exclusively by the resolution gate."""

    def __init__(
        self,
        execution_id: str,
        current_state: CommandState,
        latest_accepted_sequence: int,
        terminal_resolution_status: bool,
        terminal_event_id: Optional[str],
        source_authority: Optional[EventSourceAuthority],
        lifecycle_generation: int,
        timeout_status: bool,
        quarantine_consequence: bool,
    ) -> None:
        if type(execution_id) is not str or not execution_id.strip():
            raise DeviceValidationError("execution_id must be a non-empty string")
        if not isinstance(current_state, CommandState):
            raise DeviceValidationError("current_state must be CommandState")
        if type(latest_accepted_sequence) is not int or latest_accepted_sequence < 0:
            raise DeviceValidationError("latest_accepted_sequence must be an int >= 0")
        if type(terminal_resolution_status) is not bool:
            raise DeviceValidationError("terminal_resolution_status must be a bool")
        if terminal_event_id is not None and type(terminal_event_id) is not str:
            raise DeviceValidationError("terminal_event_id must be a string or None")
        if source_authority is not None and not isinstance(source_authority, EventSourceAuthority):
            raise DeviceValidationError("source_authority must be EventSourceAuthority or None")
        if type(lifecycle_generation) is not int or lifecycle_generation < 1:
            raise DeviceValidationError("lifecycle_generation must be an int >= 1")
        if type(timeout_status) is not bool:
            raise DeviceValidationError("timeout_status must be a bool")
        if type(quarantine_consequence) is not bool:
            raise DeviceValidationError("quarantine_consequence must be a bool")

        self._execution_id = execution_id
        self._current_state = current_state
        self._latest_accepted_sequence = latest_accepted_sequence
        self._terminal_resolution_status = terminal_resolution_status
        self._terminal_event_id = terminal_event_id
        self._source_authority = source_authority
        self._lifecycle_generation = lifecycle_generation
        self._timeout_status = timeout_status
        self._quarantine_consequence = quarantine_consequence

    @property
    def execution_id(self) -> str:
        return self._execution_id

    @property
    def current_state(self) -> CommandState:
        return self._current_state

    @property
    def latest_accepted_sequence(self) -> int:
        return self._latest_accepted_sequence

    @property
    def terminal_resolution_status(self) -> bool:
        return self._terminal_resolution_status

    @property
    def terminal_event_id(self) -> Optional[str]:
        return self._terminal_event_id

    @property
    def source_authority(self) -> Optional[EventSourceAuthority]:
        return self._source_authority

    @property
    def lifecycle_generation(self) -> int:
        return self._lifecycle_generation

    @property
    def timeout_status(self) -> bool:
        return self._timeout_status

    @property
    def quarantine_consequence(self) -> bool:
        return self._quarantine_consequence



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
    requires_physical_endpoint: bool = False
    target_endpoint_id: Optional[str] = None

    def __post_init__(self) -> None:
        if type(self.capability_id) is not str or not CAPABILITY_ID_REGEX.match(self.capability_id) or not (1 <= len(self.capability_id) <= 128):
            raise DeviceValidationError(f"Invalid capability_id: '{self.capability_id}'")
        if not isinstance(self.category, CapabilityCategory):
            raise DeviceValidationError(f"Invalid capability category: {self.category}")
        if not isinstance(self.parameters, (dict, MappingProxyType)):
            raise DeviceValidationError(f"Capability parameters must be a mapping, got {type(self.parameters).__name__}")
        if type(self.requires_physical_endpoint) is not bool:
            raise DeviceValidationError("requires_physical_endpoint must be a bool")
        if self.target_endpoint_id is not None and type(self.target_endpoint_id) is not str:
            raise DeviceValidationError("target_endpoint_id must be a string or None")

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
