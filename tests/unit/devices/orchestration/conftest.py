"""Test fixtures and mock helpers for M00.9 device orchestration tests."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, Optional, Tuple
import pytest

from holomed.configuration.models import AppConfig, EnvironmentProfile, LogLevel, SecretString
from holomed.core.dispatcher import MessageDispatcher
from holomed.devices.control.manager import DeviceControlManager
from holomed.devices.coordination.coordinator import DeviceCoordinationService
from holomed.devices.data.processor import DeviceDataProcessor
from holomed.devices.interfaces import DeviceResourceAccessor, IDevice, RegistryAuthorityToken
from holomed.devices.manager import DeviceManager
from holomed.devices.models import (
    DeviceCapability,
    DeviceHealth,
    DeviceState,
    DeviceType,
)
from holomed.devices.orchestration.orchestrator import DevicePlaneOrchestrator
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
        gemini_api_key=SecretString("test_orchestration_secret_key_999"),
    )
    return RuntimeContext(app_config=config, epoch_id=epoch_id)


class DummyOrchestrationDevice(IDevice):
    """Test device implementing IDevice for orchestration testing."""

    def __init__(
        self,
        device_id: str,
        physical_id: str,
        device_type: DeviceType = DeviceType.SIMULATED_GENERIC,
        state: DeviceState = DeviceState.UNREGISTERED,
        health_status: HealthStatus = HealthStatus.HEALTHY,
        health_message: str = "Dummy orchestration device healthy",
    ) -> None:
        self._device_id = device_id
        self._physical_id = physical_id
        self._device_type = device_type
        self._state = state
        self._health_status = health_status
        self._health_message = health_message

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
        return DeviceHealth(
            device_id=self._device_id,
            status=self._health_status,
            message=self._health_message,
            timestamp_utc=datetime.now(timezone.utc).isoformat(),
        )


def register_device(
    registry: DeviceRegistry,
    token: RegistryAuthorityToken,
    dev: DummyOrchestrationDevice,
    active: bool = True,
) -> DummyOrchestrationDevice:
    """Helper to register a device in UNREGISTERED state then transition to ACTIVE."""
    dev._state = DeviceState.UNREGISTERED
    registry.register(dev, token)
    if active:
        dev._state = DeviceState.ACTIVE
    return dev


@pytest.fixture
def context() -> RuntimeContext:
    return make_test_context(epoch_id=1)


@pytest.fixture
def device_subsystem_stack(context: RuntimeContext):
    """Create and initialize all four child device planes for testing."""
    # 1. DeviceManager
    mgr = DeviceManager()
    mgr.initialize(context)
    mgr.start()

    reg = getattr(mgr, "registry", None) or getattr(mgr, "_registry", None)
    token = getattr(mgr, "registry_token", None) or getattr(mgr, "_registry_token", None)

    # 2. ControlManager
    ctrl = DeviceControlManager(reg)
    ctrl.initialize(context)
    ctrl.start()

    # 3. DataProcessor
    data = DeviceDataProcessor(reg)
    data.initialize(context)
    data.start()

    # 4. CoordinationService
    coord = DeviceCoordinationService(reg)
    coord.initialize(context)
    coord.start()

    yield {
        "manager": mgr,
        "control": ctrl,
        "data": data,
        "coordination": coord,
        "registry": reg,
        "token": token,
    }

    # Cleanup teardown
    for s in (coord, data, ctrl, mgr):
        try:
            s.stop()
        except Exception:
            pass
