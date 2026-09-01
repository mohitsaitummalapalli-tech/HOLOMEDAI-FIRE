# -*- coding: utf-8 -*-
"""M06 XR Visualization Test Fixtures."""

from __future__ import annotations

import pytest

from holomed.anatomy.models import BoundingBox3D, Point3D, RigidTransform3D
from holomed.configuration.models import AppConfig
from holomed.core.dispatcher import MessageDispatcher
from holomed.devices.manager import DeviceManager
from holomed.runtime.context import RuntimeContext
from holomed.runtime.logging import SecretFilter
from holomed.xr.models import (
    CameraProjectionType,
    CameraViewport,
    ViewportOrientation,
    VisibilityState,
    VisualCategory,
    VisualNode,
)


@pytest.fixture
def runtime_context() -> RuntimeContext:
    config = AppConfig(
        app_name="HoloMed-XR-Test",
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
    return SecretFilter(secrets=["SECRET_XR_TOKEN_999"])


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


@pytest.fixture
def sample_visual_node() -> VisualNode:
    pose = RigidTransform3D(
        translation=Point3D(0.0, 0.0, 0.0),
        rotation_quaternion=(1.0, 0.0, 0.0, 0.0),
        source_frame="node_local",
        target_frame="world",
    )
    bbox = BoundingBox3D(Point3D(-0.1, -0.1, -0.1), Point3D(0.1, 0.1, 0.1))
    return VisualNode(
        node_id="visual.liver",
        name="Liver Hologram",
        category=VisualCategory.ANATOMY,
        parent_id=None,
        local_transform=pose,
        bounding_volume=bbox,
        visibility=VisibilityState.VISIBLE,
        opacity=1.0,
        layer_id=1,
        presentation_reference="anat.abdomen.liver",
        is_simulated=True,
        uncertainty_radius_m=0.01,
        metadata={"organ": "liver"},
    )


@pytest.fixture
def sample_viewport() -> CameraViewport:
    view_pose = RigidTransform3D(
        translation=Point3D(0.0, 0.0, 1.0),  # Camera positioned at (0, 0, 1) looking along -Z
        rotation_quaternion=(1.0, 0.0, 0.0, 0.0),
        source_frame="camera",
        target_frame="world",
    )
    return CameraViewport(
        viewport_id="viewport.perspective_0",
        orientation=ViewportOrientation.PERSPECTIVE_3D,
        projection_type=CameraProjectionType.PERSPECTIVE,
        field_of_view_deg=60.0,
        aspect_ratio=1.0,
        near_clip_m=0.1,
        far_clip_m=10.0,
        view_pose=view_pose,
        is_active=True,
    )
