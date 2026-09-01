# -*- coding: utf-8 -*-
"""M05 Anatomy & Simulation Test Fixtures."""

from __future__ import annotations

from datetime import datetime, timezone
import pytest

from holomed.anatomy.models import (
    AnatomicalClassification,
    AnatomicalEntity,
    BoundingBox3D,
    Point3D,
    RigidTransform3D,
)
from holomed.configuration.models import AppConfig
from holomed.core.dispatcher import MessageDispatcher
from holomed.devices.interfaces import IDevice
from holomed.devices.manager import DeviceManager
from holomed.devices.models import DeviceCapability, DeviceHealth, DeviceState, DeviceType
from holomed.runtime.context import RuntimeContext
from holomed.runtime.logging import SecretFilter
from holomed.runtime.models import HealthStatus


class DummyAnatomyDevice(IDevice):
    def __init__(self, device_id: str = "surgical_tool_0", physical_id: str = "phys_tool_0") -> None:
        self._device_id = device_id
        self._physical_id = physical_id
        self._state = DeviceState.UNREGISTERED

    @property
    def device_id(self) -> str:
        return self._device_id

    @property
    def device_type(self) -> DeviceType:
        return DeviceType.SURGICAL_TOOL

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
        app_name="HoloMed-Anatomy-Test",
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
    return SecretFilter(secrets=["SECRET_ANATOMY_PATIENT_TOKEN_999"])


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
    dev = DummyAnatomyDevice("surgical_tool_0", "phys_tool_0")
    reg.register(dev, token)
    dev._state = DeviceState.ACTIVE
    return dm


@pytest.fixture
def sample_organ_entity() -> AnatomicalEntity:
    pose = RigidTransform3D(
        translation=Point3D(0.1, 0.2, 0.5),
        rotation_quaternion=(1.0, 0.0, 0.0, 0.0),
        source_frame="organ_local",
        target_frame="patient_reference",
    )
    bbox = BoundingBox3D(
        min_point=Point3D(0.0, 0.1, 0.4),
        max_point=Point3D(0.2, 0.3, 0.6),
    )
    lm1 = Point3D(0.1, 0.2, 0.5)
    return AnatomicalEntity(
        entity_id="anat.abdomen.liver",
        name="Liver",
        classification=AnatomicalClassification.ORGAN,
        parent_id=None,
        reference_pose=pose,
        bounding_volume=bbox,
        landmarks=(lm1,),
        metadata={"segment_count": 8},
    )
