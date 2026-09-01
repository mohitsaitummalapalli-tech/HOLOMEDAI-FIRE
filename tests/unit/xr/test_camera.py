# -*- coding: utf-8 -*-
"""Unit tests for ViewportManager and Viewport Limits."""

from __future__ import annotations

import pytest

from holomed.anatomy.models import Point3D, RigidTransform3D
from holomed.xr.camera import ViewportManager
from holomed.xr.exceptions import XRCapacityError, XRValidationError
from holomed.xr.models import (
    CameraProjectionType,
    CameraViewport,
    ViewportOrientation,
)


def make_viewport(vp_id: str) -> CameraViewport:
    pose = RigidTransform3D(Point3D(0, 0, 0), (1, 0, 0, 0), "cam", "world")
    return CameraViewport(
        viewport_id=vp_id,
        orientation=ViewportOrientation.PERSPECTIVE_3D,
        projection_type=CameraProjectionType.PERSPECTIVE,
        field_of_view_deg=60.0,
        aspect_ratio=1.0,
        near_clip_m=0.1,
        far_clip_m=10.0,
        view_pose=pose,
        is_active=True,
    )


def test_viewport_manager_capacity_4() -> None:
    """Verify MAX_VIEWPORTS = 4 capacity bound."""
    vm = ViewportManager()
    for i in range(4):
        vm.add_viewport(make_viewport(f"vp_{i}"))

    assert vm.viewport_count == 4

    # 5th viewport raises XRCapacityError
    with pytest.raises(XRCapacityError):
        vm.add_viewport(make_viewport("vp_overflow"))


def test_viewport_manager_duplicate_rejection() -> None:
    """Verify duplicate viewport_id is rejected."""
    vm = ViewportManager()
    vm.add_viewport(make_viewport("vp_main"))
    with pytest.raises(XRValidationError):
        vm.add_viewport(make_viewport("vp_main"))
