# -*- coding: utf-8 -*-
"""M08 Platform Test Fixtures and Service Stubs."""

from __future__ import annotations

from typing import Any, Optional
import pytest

from holomed.configuration.models import AppConfig
from holomed.core.dispatcher import MessageDispatcher
from holomed.devices.manager import DeviceManager
from holomed.runtime.context import RuntimeContext
from holomed.runtime.logging import SecretFilter
from holomed.runtime.models import (
    HealthStatus,
    OwnedResourceSet,
    ServiceHealth,
)
from holomed.runtime.service import IService, ServiceState
from holomed.xr.models import CameraViewport, PresentationDescription, VisualCategory, VisualNode


@pytest.fixture
def runtime_context() -> RuntimeContext:
    config = AppConfig(
        app_name="HoloMed-Platform-Test",
        environment="TESTING",
        host="127.0.0.1",
        port=8000,
        log_level="DEBUG",
        gemini_api_key=None,
        protocol_version="1.0",
    )
    return RuntimeContext(app_config=config, epoch_id=1)


@pytest.fixture
def secret_filter() -> SecretFilter:
    return SecretFilter(secrets=["SECRET_PLATFORM_KEY_999"])


@pytest.fixture
def message_dispatcher(runtime_context: RuntimeContext) -> MessageDispatcher:
    disp = MessageDispatcher()
    disp.initialize(runtime_context)
    return disp


@pytest.fixture
def device_manager(runtime_context: RuntimeContext) -> DeviceManager:
    dm = DeviceManager()
    dm.initialize(runtime_context)
    dm.start()
    return dm


class MockDomainService(IService):
    """Stub service implementing IService for end-to-end testing."""

    def __init__(self, service_name: str, epoch_id: int = 1) -> None:
        self._name = service_name
        self._epoch_id = epoch_id
        self._state = ServiceState.STARTED
        self._resources = OwnedResourceSet(service_name, epoch_id)
        self._resources.acquire(f"{service_name}.res")
        self.cleared = False
        self.reset_called_with_epoch: Optional[int] = None

    @property
    def name(self) -> str:
        return self._name

    @property
    def dependencies(self) -> tuple[str, ...]:
        return ()

    @property
    def state(self) -> ServiceState:
        return self._state

    @property
    def resources(self) -> OwnedResourceSet:
        return self._resources

    def initialize(self, context: RuntimeContext) -> None:
        self._state = ServiceState.INITIALIZED

    def start(self) -> None:
        self._state = ServiceState.STARTED

    def stop(self) -> None:
        for h in list(self._resources.outstanding_handles):
            self._resources.release(h.resource_id)
        self._state = ServiceState.STOPPED

    def health(self) -> ServiceHealth:
        return ServiceHealth(self._name, HealthStatus.HEALTHY, "Operational", "2026-09-01T20:00:00Z")

    def execute_cycle(self, observation: Any = None) -> tuple[Any, ...]:
        return ("action_1",)

    def step_simulation(self, count: int = 1) -> Any:
        return {"step": count}

    def generate_frame(self) -> Any:
        class DummyFrame:
            nodes = {"node_0": "node"}
        return DummyFrame()

    def reset(self, epoch_id: int) -> None:
        self.reset_called_with_epoch = epoch_id
        self._epoch_id = epoch_id
        self.cleared = True

    def clear(self) -> None:
        self.cleared = True


@pytest.fixture
def mock_domain_services() -> dict[str, IService]:
    return {
        "vision_service": MockDomainService("vision_service"),
        "audio_service": MockDomainService("audio_service"),
        "gesture_service": MockDomainService("gesture_service"),
        "ultron_service": MockDomainService("ultron_service"),
        "anatomy_service": MockDomainService("anatomy_service"),
        "xr_service": MockDomainService("xr_service"),
        "tool_service": MockDomainService("tool_service"),
    }
