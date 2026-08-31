"""HoloMed AI - Core interfaces for devices, event sinks, discovery, and proxies."""

from __future__ import annotations

import abc
from typing import TYPE_CHECKING, Iterable, List, Optional, Tuple

from holomed.devices.exceptions import DeviceCapacityError
from holomed.devices.models import (
    CapabilityCategory,
    DeviceCapability,
    DeviceDescriptor,
    DeviceHealth,
    DeviceState,
    DeviceType,
    MAX_RECORDED_EVENTS,
)
from holomed.protocol.models import MessageEnvelope
from holomed.runtime.models import ResourceHandle

if TYPE_CHECKING:
    from holomed.devices.authority import DeviceResourceAuthority


class RegistryAuthorityToken:
    """Opaque capability token ensuring only DeviceManager can mutate DeviceRegistry."""


class DeviceResourceAccessor:
    """Scoped, isolated proxy handed to devices for namespaced resource acquisition and release.

    Guarantees:
    - Devices never receive DeviceManager, OwnedResourceSet, or internal ownership indices.
    - Resources are scoped strictly to the device's namespace: device.<device_id>.<resource_name>.
    - Access to manager.* or foreign device namespaces is physically impossible through this proxy.
    """

    def __init__(self, authority: DeviceResourceAuthority, device_id: str) -> None:
        self._authority = authority
        self._device_id = device_id

    @property
    def device_id(self) -> str:
        """The identifier of the device owning this accessor."""
        return self._device_id

    def acquire(self, resource_name: str) -> ResourceHandle:
        """Acquire a named resource scoped to this device."""
        return self._authority.acquire(self._device_id, resource_name)

    def release(self, resource_id: str) -> None:
        """Release an acquired resource owned by this device."""
        self._authority.release(self._device_id, resource_id)

    def mark_release_failed(self, resource_id: str, error: str) -> None:
        """Record an unreleased resource failure during teardown."""
        self._authority.mark_release_failed(self._device_id, resource_id, error)

    @property
    def outstanding_handles(self) -> frozenset[ResourceHandle]:
        """All active or failed unreleased handles owned by this device."""
        return self._authority.get_device_outstanding_handles(self._device_id)

    @property
    def is_empty(self) -> bool:
        """True if and only if zero active or unreleased handles remain for this device."""
        return self._authority.is_device_clean(self._device_id)


class IDevice(abc.ABC):
    """Abstract interface for all hardware devices and simulated test doubles."""

    @property
    @abc.abstractmethod
    def device_id(self) -> str:
        """Unique dot-separated device identifier."""

    @property
    @abc.abstractmethod
    def physical_id(self) -> str:
        """Physical or simulated connection hardware identifier."""

    @property
    @abc.abstractmethod
    def device_type(self) -> DeviceType:
        """Hardware type category of this device."""

    @property
    @abc.abstractmethod
    def state(self) -> DeviceState:
        """Current lifecycle state of the device."""

    @property
    @abc.abstractmethod
    def capabilities(self) -> Tuple[DeviceCapability, ...]:
        """Exact declared capabilities of this device."""

    @abc.abstractmethod
    def initialize(self, accessor: DeviceResourceAccessor) -> None:
        """Acquire device-scoped structural resources and prepare for operation."""

    @abc.abstractmethod
    def start(self) -> None:
        """Activate device. MUST NOT acquire new resources."""

    @abc.abstractmethod
    def stop(self, accessor: DeviceResourceAccessor) -> None:
        """Deactivate device and release all acquired resources."""

    @abc.abstractmethod
    def health(self) -> DeviceHealth:
        """Produce a synchronous in-process diagnostic snapshot."""


class IDeviceEventSink(abc.ABC):
    """Hardware-independent event sink for lifecycle and diagnostic events."""

    @abc.abstractmethod
    def emit(self, envelope: MessageEnvelope) -> None:
        """Emit a validated lifecycle message envelope."""


class NullDeviceEventSink(IDeviceEventSink):
    """No-op event sink discarding all emitted events."""

    def emit(self, envelope: MessageEnvelope) -> None:
        pass


class RecordingDeviceEventSink(IDeviceEventSink):
    """Recording event sink for testing and verification with strict capacity bounds."""

    def __init__(self) -> None:
        self._events: List[MessageEnvelope] = []

    def emit(self, envelope: MessageEnvelope) -> None:
        if len(self._events) >= MAX_RECORDED_EVENTS:
            raise DeviceCapacityError(f"RecordingDeviceEventSink capacity exceeded ({MAX_RECORDED_EVENTS})")
        self._events.append(envelope)

    @property
    def events(self) -> Tuple[MessageEnvelope, ...]:
        """Immutable view of recorded message envelopes."""
        return tuple(self._events)

    def clear(self) -> None:
        """Reset the recorded event buffer."""
        self._events = []


class DeviceDiscoveryProvider(abc.ABC):
    """Abstract interface for hardware device discovery providers."""

    @abc.abstractmethod
    def discover(self) -> Iterable[DeviceDescriptor]:
        """Discover available devices and return candidate descriptors."""
