# -*- coding: utf-8 -*-
"""Spatial interaction ray, pinch point, palm center, and orientation computation."""

from __future__ import annotations

import math
from typing import Mapping, Optional

from holomed.gesture.models import (
    COORDINATE_EPSILON,
    Handedness,
    HandLandmark3D,
    SpatialInteraction,
    quantize_coord,
)


def compute_spatial_interaction(
    landmarks: Mapping[int, HandLandmark3D],
    handedness: Handedness,
) -> SpatialInteraction:
    """Compute spatial interaction primitives including pointing ray and pinch point."""
    # 1. Pointing Ray
    ray_origin: Optional[tuple[float, float, float]] = None
    ray_direction: Optional[tuple[float, float, float]] = None

    mcp_5 = landmarks.get(5)
    tip_8 = landmarks.get(8)
    if mcp_5 is not None and tip_8 is not None:
        dx = tip_8.x - mcp_5.x
        dy = tip_8.y - mcp_5.y
        dz = tip_8.z - mcp_5.z
        length = math.sqrt(dx * dx + dy * dy + dz * dz)
        if length >= COORDINATE_EPSILON:
            ray_origin = (
                quantize_coord(mcp_5.x, 6),
                quantize_coord(mcp_5.y, 6),
                quantize_coord(mcp_5.z, 6),
            )
            ray_direction = (
                quantize_coord(dx / length, 6),
                quantize_coord(dy / length, 6),
                quantize_coord(dz / length, 6),
            )

    # 2. Pinch Point
    pinch_pt: Optional[tuple[float, float, float]] = None
    tip_4 = landmarks.get(4)
    if tip_4 is not None and tip_8 is not None:
        pinch_pt = (
            quantize_coord((tip_4.x + tip_8.x) / 2.0, 6),
            quantize_coord((tip_4.y + tip_8.y) / 2.0, 6),
            quantize_coord((tip_4.z + tip_8.z) / 2.0, 6),
        )

    # 3. Palm Center (Landmarks 0, 1, 5, 9, 13, 17)
    palm_center: Optional[tuple[float, float, float]] = None
    palm_pts = [landmarks[i] for i in (0, 1, 5, 9, 13, 17) if i in landmarks]
    if palm_pts:
        sum_x = sum(p.x for p in palm_pts)
        sum_y = sum(p.y for p in palm_pts)
        sum_z = sum(p.z for p in palm_pts)
        n = len(palm_pts)
        palm_center = (
            quantize_coord(sum_x / n, 6),
            quantize_coord(sum_y / n, 6),
            quantize_coord(sum_z / n, 6),
        )

    # 4. Palm Normal
    palm_normal: Optional[tuple[float, float, float]] = None
    w = landmarks.get(0)
    mcp_17 = landmarks.get(17)
    if w is not None and mcp_5 is not None and mcp_17 is not None:
        v1x, v1y, v1z = mcp_5.x - w.x, mcp_5.y - w.y, mcp_5.z - w.z
        v2x, v2y, v2z = mcp_17.x - w.x, mcp_17.y - w.y, mcp_17.z - w.z

        # Cross product: v1 x v2
        if handedness == Handedness.LEFT:
            nx = v2y * v1z - v2z * v1y
            ny = v2z * v1x - v2x * v1z
            nz = v2x * v1y - v2y * v1x
        else:
            nx = v1y * v2z - v1z * v2y
            ny = v1z * v2x - v1x * v2z
            nz = v1x * v2y - v1y * v2x

        n_len = math.sqrt(nx * nx + ny * ny + nz * nz)
        if n_len >= COORDINATE_EPSILON:
            palm_normal = (
                quantize_coord(nx / n_len, 6),
                quantize_coord(ny / n_len, 6),
                quantize_coord(nz / n_len, 6),
            )

    return SpatialInteraction(
        pointing_ray_origin=ray_origin,
        pointing_ray_direction=ray_direction,
        pinch_point=pinch_pt,
        palm_center=palm_center,
        palm_normal=palm_normal,
    )
