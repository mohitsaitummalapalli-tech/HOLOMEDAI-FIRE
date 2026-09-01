# -*- coding: utf-8 -*-
"""M04 Ultron Test Fixtures."""

from __future__ import annotations

from datetime import datetime, timezone
from types import MappingProxyType
import uuid
import pytest

from holomed.configuration.models import AppConfig
from holomed.core.dispatcher import MessageDispatcher
from holomed.devices.interfaces import IDevice
from holomed.devices.manager import DeviceManager
from holomed.devices.models import DeviceCapability, DeviceHealth, DeviceState, DeviceType
from holomed.runtime.context import RuntimeContext
from holomed.runtime.logging import SecretFilter
from holomed.runtime.models import HealthStatus
from holomed.ultron.models import (
    ActionType,
    ConflictSeverity,
    ConflictType,
    EntityType,
    Modality,
    ModalityObservation,
)


class DummyUltronDevice(IDevice):
    def __init__(self, device_id: str = "ultron_sensor_0", physical_id: str = "phys_ultron_0") -> None:
        self._device_id = device_id
        self._physical_id = physical_id
        self._state = DeviceState.UNREGISTERED

    @property
    def device_id(self) -> str:
        return self._device_id

    @property
    def device_type(self) -> DeviceType:
        return DeviceType.SIMULATED_GENERIC

    @property
    def physical_id(self) -> str:
        return self._physical_id

    @property
    def state(self) -> DeviceState:
        return self._state

    @property
    def capabilities(self) -> frozenset[DeviceCapability]:
        return frozenset({DeviceCapability.DATA_STREAM})

    def initialize(self) -> None:
        pass

    def start(self) -> None:
        self._state = DeviceState.ACTIVE

    def stop(self) -> None:
        self._state = DeviceState.STOPPED

    def teardown(self) -> None:
        self._state = DeviceState.STOPPED

    def health(self) -> DeviceHealth:
        return DeviceHealth(
            device_id=self._device_id,
            status=HealthStatus.HEALTHY,
            message="OK",
            timestamp_utc=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ"),
        )


@pytest.fixture
def runtime_context() -> RuntimeContext:
    config = AppConfig(
        app_name="HoloMed-Ultron-Test",
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
    return SecretFilter(secrets=["SECRET_TOKEN_XYZ_12345"])


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
    reg = getattr(dm, "registry", None) or getattr(dm, "_registry", None)
    token = getattr(dm, "registry_token", None) or getattr(dm, "_registry_token", None)
    dev = DummyUltronDevice("ultron_sensor_0", "phys_ultron_0")
    reg.register(dev, token)
    dev._state = DeviceState.ACTIVE
    return dm


@pytest.fixture
def sample_vision_observation() -> ModalityObservation:
    now_utc = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
    return ModalityObservation(
        observation_id=str(uuid.uuid4()),
        modality=Modality.VISION,
        source_id="vision.camera",
        physical_id="phys_cam_0",
        epoch_id=1,
        sequence_number=1,
        timestamp_utc=now_utc,
        confidence=0.95,
        payload=MappingProxyType({"position": (0.1, 0.2, 1.0), "entity_id": "entity_0001"}),
    )


@pytest.fixture
def sample_gesture_observation() -> ModalityObservation:
    now_utc = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
    return ModalityObservation(
        observation_id=str(uuid.uuid4()),
        modality=Modality.GESTURE,
        source_id="gesture.tracker",
        physical_id="phys_cam_0",
        epoch_id=1,
        sequence_number=1,
        timestamp_utc=now_utc,
        confidence=0.90,
        payload=MappingProxyType({
            "position": (0.12, 0.21, 1.01),
            "active_gestures": ["PINCH"],
            "entity_id": "entity_0001",
        }),
    )


@pytest.fixture
def sample_audio_observation() -> ModalityObservation:
    now_utc = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
    return ModalityObservation(
        observation_id=str(uuid.uuid4()),
        modality=Modality.AUDIO,
        source_id="audio.microphone",
        physical_id="phys_mic_0",
        epoch_id=1,
        sequence_number=1,
        timestamp_utc=now_utc,
        confidence=0.80,
        payload=MappingProxyType({"acoustic_direction": (5.7, 0.0), "direction_confidence": 0.85}),
    )
