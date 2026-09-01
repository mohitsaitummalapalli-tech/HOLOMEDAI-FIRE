# -*- coding: utf-8 -*-
"""Unit tests for Camera Frustum Projections and Safe Clipping (D229, D238)."""

from __future__ import annotations

import pytest

from holomed.anatomy.models import Point3D, RigidTransform3D
from holomed.xr.models import (
    CameraProjectionType,
    CameraViewport,
    ViewportOrientation,
)
from holomed.xr.projection import project_world_point_to_viewport


def test_d238_safe_projection_in_front_and_behind_near_plane() -> None:
    """Verify D238 safe projection without division by zero or NaN escape."""
    # Camera at (0, 0, 0) looking along -Z
    pose = RigidTransform3D(Point3D(0, 0, 0), (1, 0, 0, 0), "cam", "world")
    vp = CameraViewport(
        viewport_id="vp",
        orientation=ViewportOrientation.PERSPECTIVE_3D,
        projection_type=CameraProjectionType.PERSPECTIVE,
        field_of_view_deg=90.0,
        aspect_ratio=1.0,
        near_clip_m=0.1,
        far_clip_m=10.0,
        view_pose=pose,
        is_active=True,
    )

    # 1. Point in front: (0, 0, -2) -> w = 2 > near
    res_front = project_world_point_to_viewport(Point3D(0, 0, -2), vp)
    assert res_front.is_visible is True
    assert res_front.is_clipped is False
    assert res_front.point_ndc is not None
    assert res_front.point_ndc[0] == 0.0
    assert res_front.point_ndc[1] == 0.0

    # 2. Point behind camera: (0, 0, 2) -> w = -2 <= near
    res_behind = project_world_point_to_viewport(Point3D(0, 0, 2), vp)
    assert res_behind.is_visible is False
    assert res_behind.is_clipped is True
    assert res_behind.point_ndc is None

    # 3. Point inside near clipping plane: (0, 0, -0.05) -> w = 0.05 < 0.1
    res_clipped = project_world_point_to_viewport(Point3D(0, 0, -0.05), vp)
    assert res_clipped.is_visible is False
    assert res_clipped.is_clipped is True
    assert res_clipped.point_ndc is None


def test_orthographic_projection() -> None:
    """Verify orthographic projection mode."""
    pose = RigidTransform3D(Point3D(0, 0, 0), (1, 0, 0, 0), "cam", "world")
    vp = CameraViewport(
        viewport_id="vp_ortho",
        orientation=ViewportOrientation.AXIAL,
        projection_type=CameraProjectionType.ORTHOGRAPHIC,
        field_of_view_deg=60.0,
        aspect_ratio=1.0,
        near_clip_m=0.1,
        far_clip_m=10.0,
        view_pose=pose,
        is_active=True,
    )
    res = project_world_point_to_viewport(Point3D(0.5, 0.5, -5.0), vp)
    assert res.is_visible is True
    assert res.point_ndc is not None
    assert res.point_ndc[0] == 0.5
    assert res.point_ndc[1] == 0.5
