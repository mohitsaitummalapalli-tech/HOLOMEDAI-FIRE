"""HoloMed AI - Fully functional simulated device test double."""

from __future__ import annotations

from datetime import datetime, timezone
from types import MappingProxyType
from typing import Optional, Tuple

from holomed.devices.interfaces import DeviceResourceAccessor, IDevice
from holomed.devices.models import (
    DeviceCapability,
    DeviceHealth,
    DeviceState,
    DeviceType,
)
from holomed.runtime.models import HealthStatus


class SimulatedDevice(IDevice):
    """Concrete, hardware-independent simulated device for testing and integration."""

    def __init__(
        self,
        device_id: str,
        physical_id: str,
        device_type: DeviceType = DeviceType.SIMULATED_GENERIC,
        capabilities: Tuple[DeviceCapability, ...] = (),
    ) -> None:
        self._device_id = device_id
        self._physical_id = physical_id
        self._device_type = device_type
        self._capabilities = tuple(capabilities)
        self._state: DeviceState = DeviceState.UNREGISTERED
        self._acquired_resource_ids: list[str] = []
        self._fail_on_initialize: Optional[Exception] = None
        self._fail_on_start: Optional[Exception] = None
        self._fail_on_stop: Optional[Exception] = None
        self._fail_on_health: Optional[Exception] = None
        self._custom_health: Optional[DeviceHealth] = None

    @property
    def device_id(self) -> str:
        return self._device_id

    @property
    def physical_id(self) -> str:
        return self._physical_id

    @property
    def device_type(self) -> DeviceType:
        return self._device_type

    @property
    def state(self) -> DeviceState:
        return self._state

    @property
    def capabilities(self) -> Tuple[DeviceCapability, ...]:
        return self._capabilities

    def initialize(self, accessor: DeviceResourceAccessor) -> None:
        if self._fail_on_initialize is not None:
            raise self._fail_on_initialize
        handle = accessor.acquire("primary")
        self._acquired_resource_ids.append(handle.resource_id)

    def start(self) -> None:
        if self._fail_on_start is not None:
            raise self._fail_on_start

    def stop(self, accessor: DeviceResourceAccessor) -> None:
        if self._fail_on_stop is not None:
            # Optionally record failure
            for r_id in list(self._acquired_resource_ids):
                accessor.mark_release_failed(r_id, str(self._fail_on_stop))
            raise self._fail_on_stop

        for r_id in list(self._acquired_resource_ids):
            accessor.release(r_id)
        self._acquired_resource_ids.clear()

    def health(self) -> DeviceHealth:
        if self._fail_on_health is not None:
            raise self._fail_on_health
        if self._custom_health is not None:
            return self._custom_health
        return DeviceHealth(
            device_id=self._device_id,
            status=HealthStatus.HEALTHY,
            message="Simulated device operating normally",
            timestamp_utc=datetime.now(timezone.utc).isoformat(),
            diagnostics=MappingProxyType({}),
        )

    # Test configuration hooks
    def set_fail_on_initialize(self, exc: Optional[Exception]) -> None:
        self._fail_on_initialize = exc

    def set_fail_on_start(self, exc: Optional[Exception]) -> None:
        self._fail_on_start = exc

    def set_fail_on_stop(self, exc: Optional[Exception]) -> None:
        self._fail_on_stop = exc

    def set_fail_on_health(self, exc: Optional[Exception]) -> None:
        self._fail_on_health = exc

    def set_custom_health(self, health: Optional[DeviceHealth]) -> None:
        self._custom_health = health
