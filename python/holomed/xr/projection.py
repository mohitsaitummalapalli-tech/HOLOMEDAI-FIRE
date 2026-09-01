# -*- coding: utf-8 -*-
"""Camera Frustum Mathematical Projections and Clipping (D229, D233, D238)."""

from __future__ import annotations

import math
from typing import Optional

from holomed.anatomy.geometry import apply_rigid_transform, invert_rigid_transform
from holomed.anatomy.models import Point3D
from holomed.xr.models import (
    CameraProjectionType,
    CameraViewport,
    ProjectionResult,
)


def project_world_point_to_viewport(
    world_point: Point3D,
    viewport: CameraViewport,
) -> ProjectionResult:
    """Project a 3D world coordinate into camera NDC coordinates [-1.0, 1.0].
    
    Camera looks along -Z. Points in front of the camera have z_camera < 0.
    Viewing depth is w = -z_camera.
    """
    # 1. Transform world point into camera space: p_cam = T_world_to_cam * p_world
    tf_cam_to_world = viewport.view_pose
    tf_world_to_cam = invert_rigid_transform(tf_cam_to_world)
    p_cam = apply_rigid_transform(tf_world_to_cam, world_point)

    xc = p_cam.x
    yc = p_cam.y
    zc = p_cam.z

    # Viewing depth w along -Z
    w = -zc

    # 2. Check Near/Far Clipping Plane Boundaries
    if w <= viewport.near_clip_m or w >= viewport.far_clip_m:
        return ProjectionResult(point_ndc=None, is_visible=False, is_clipped=True)

    if viewport.projection_type == CameraProjectionType.PERSPECTIVE:
        tan_half = math.tan(math.radians(viewport.field_of_view_deg) / 2.0)
        denom_x = w * viewport.aspect_ratio * tan_half
        denom_y = w * tan_half

        if abs(denom_x) < 1e-9 or abs(denom_y) < 1e-9:
            return ProjectionResult(point_ndc=None, is_visible=False, is_clipped=True)

        x_ndc = xc / denom_x
        y_ndc = yc / denom_y

        near = viewport.near_clip_m
        far = viewport.far_clip_m
        z_ndc = ((far + near) / (far - near)) + ((2.0 * far * near) / (w * (near - far)))

    else:  # Orthographic
        ortho_size = 1.0  # 1m half-height standard
        ortho_width = ortho_size * viewport.aspect_ratio
        x_ndc = xc / ortho_width
        y_ndc = yc / ortho_size
        near = viewport.near_clip_m
        far = viewport.far_clip_m
        z_ndc = (2.0 * w - (far + near)) / (far - near)

    norm_x = 0.0 if float(x_ndc) == 0.0 else round(float(x_ndc), 6)
    norm_y = 0.0 if float(y_ndc) == 0.0 else round(float(y_ndc), 6)
    norm_z = 0.0 if float(z_ndc) == 0.0 else round(float(z_ndc), 6)

    # Check if inside [-1, 1] unit frustum box
    is_in_frustum = (-1.0 <= norm_x <= 1.0) and (-1.0 <= norm_y <= 1.0) and (-1.0 <= norm_z <= 1.0)

    return ProjectionResult(
        point_ndc=(norm_x, norm_y, norm_z),
        is_visible=is_in_frustum,
        is_clipped=not is_in_frustum,
    )
