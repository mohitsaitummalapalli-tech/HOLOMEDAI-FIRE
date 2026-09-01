"""Test fixtures and mock helpers for M00.8 device coordination tests."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, Optional, Tuple

import pytest

from holomed.configuration.models import AppConfig, EnvironmentProfile, LogLevel, SecretString
from holomed.devices.interfaces import DeviceResourceAccessor, IDevice, RegistryAuthorityToken
from holomed.devices.models import (
    DeviceCapability,
    DeviceHealth,
    DeviceState,
    DeviceType,
)
from holomed.devices.registry import DeviceRegistry
from holomed.runtime.context import RuntimeContext
from holomed.runtime.models import HealthStatus


def make_test_context(epoch_id: int = 1) -> RuntimeContext:
    config = AppConfig(
        app_name="HoloMed AI",
        environment=EnvironmentProfile.TESTING,
        host="127.0.0.1",
        port=8000,
        log_level=LogLevel.DEBUG,
        gemini_api_key=SecretString("test_secret_key_12345"),
    )
    return RuntimeContext(app_config=config, epoch_id=epoch_id)


class DummyCoordinationDevice(IDevice):
    """Test device implementing IDevice for coordination verification."""

    def __init__(
        self,
        device_id: str,
        physical_id: str,
        device_type: DeviceType = DeviceType.SIMULATED_GENERIC,
        state: DeviceState = DeviceState.UNREGISTERED,
        health_status: HealthStatus = HealthStatus.HEALTHY,
        health_message: str = "Dummy device healthy",
        health_raise_exc: Optional[Exception] = None,
        health_malformed_return: Any = None,
    ) -> None:
        self._device_id = device_id
        self._physical_id = physical_id
        self._device_type = device_type
        self._state = state
        self._health_status = health_status
        self._health_message = health_message
        self._health_raise_exc = health_raise_exc
        self._health_malformed_return = health_malformed_return

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
        return ()

    def initialize(self, accessor: DeviceResourceAccessor) -> None:
        self._state = DeviceState.READY

    def start(self) -> None:
        self._state = DeviceState.ACTIVE

    def stop(self, accessor: DeviceResourceAccessor) -> None:
        self._state = DeviceState.STOPPED

    def health(self) -> DeviceHealth:
        if self._health_raise_exc is not None:
            raise self._health_raise_exc
        if self._health_malformed_return is not None:
            return self._health_malformed_return
        return DeviceHealth(
            device_id=self._device_id,
            status=self._health_status,
            message=self._health_message,
            timestamp_utc=datetime.now(timezone.utc).isoformat(),
        )


def register_device(
    registry: DeviceRegistry,
    token: RegistryAuthorityToken,
    dev: DummyCoordinationDevice,
    active: bool = True,
) -> DummyCoordinationDevice:
    """Helper to register a device in UNREGISTERED state then transition to ACTIVE."""
    dev._state = DeviceState.UNREGISTERED
    registry.register(dev, token)
    if active:
        dev._state = DeviceState.ACTIVE
    return dev


@pytest.fixture
def registry_token() -> RegistryAuthorityToken:
    return RegistryAuthorityToken()


@pytest.fixture
def device_registry(registry_token: RegistryAuthorityToken) -> DeviceRegistry:
    return DeviceRegistry(registry_token)
