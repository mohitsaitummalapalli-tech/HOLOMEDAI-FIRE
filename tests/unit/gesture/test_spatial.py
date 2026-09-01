# -*- coding: utf-8 -*-
"""Unit tests for spatial interaction solvers, ray casting, and palm orientation."""

from __future__ import annotations

import math
import pytest

from holomed.gesture.models import (
    HandLandmark3D,
    Handedness,
    unproject_spatial_landmark,
)
from holomed.gesture.spatial import compute_spatial_interaction
from holomed.vision.models import SpatialLandmark


def test_pointing_ray_unit_vector(pointing_landmarks: tuple[SpatialLandmark, ...]) -> None:
    """Verify pointing ray direction is a normalized unit vector."""
    lms_3d = {lm.landmark_id: unproject_spatial_landmark(lm) for lm in pointing_landmarks}
    spatial = compute_spatial_interaction(lms_3d, Handedness.RIGHT)

    assert spatial.pointing_ray_origin is not None
    assert spatial.pointing_ray_direction is not None

    dx, dy, dz = spatial.pointing_ray_direction
    norm = math.sqrt(dx * dx + dy * dy + dz * dz)
    assert abs(norm - 1.0) <= 1e-5


def test_pointing_ray_zero_length_rejection() -> None:
    """Verify D205: zero-length direction vector produces None and does not divide by zero."""
    # Place index MCP (5) and index TIP (8) at exact same 3D coordinates
    mcp_5 = HandLandmark3D(5, "INDEX_MCP", 0.1, 0.2, 1.0, 0.5, 0.5, 0.9)
    tip_8 = HandLandmark3D(8, "INDEX_TIP", 0.1, 0.2, 1.0, 0.5, 0.5, 0.9)
    lms = {5: mcp_5, 8: tip_8}

    spatial = compute_spatial_interaction(lms, Handedness.RIGHT)
    assert spatial.pointing_ray_direction is None


def test_pinch_point_midpoint(pinch_landmarks: tuple[SpatialLandmark, ...]) -> None:
    """Verify pinch point is exact midpoint between thumb tip and index tip."""
    lms_3d = {lm.landmark_id: unproject_spatial_landmark(lm) for lm in pinch_landmarks}
    spatial = compute_spatial_interaction(lms_3d, Handedness.RIGHT)

    assert spatial.pinch_point is not None
    t4 = lms_3d[4]
    t8 = lms_3d[8]

    expected_x = round((t4.x + t8.x) / 2.0, 6)
    expected_y = round((t4.y + t8.y) / 2.0, 6)
    expected_z = round((t4.z + t8.z) / 2.0, 6)

    assert spatial.pinch_point == (expected_x, expected_y, expected_z)


def test_palm_center_mean_of_six_landmarks(open_hand_landmarks: tuple[SpatialLandmark, ...]) -> None:
    """Verify palm center is mean of landmarks 0, 1, 5, 9, 13, 17."""
    lms_3d = {lm.landmark_id: unproject_spatial_landmark(lm) for lm in open_hand_landmarks}
    spatial = compute_spatial_interaction(lms_3d, Handedness.RIGHT)

    assert spatial.palm_center is not None
    pts = [lms_3d[i] for i in (0, 1, 5, 9, 13, 17)]
    exp_x = round(sum(p.x for p in pts) / 6.0, 6)
    exp_y = round(sum(p.y for p in pts) / 6.0, 6)
    exp_z = round(sum(p.z for p in pts) / 6.0, 6)

    assert spatial.palm_center == (exp_x, exp_y, exp_z)


def test_palm_normal_unit_vector_and_handedness(open_hand_landmarks: tuple[SpatialLandmark, ...]) -> None:
    """Verify palm normal unit vector is normalized and accounts for handedness."""
    lms_3d = {lm.landmark_id: unproject_spatial_landmark(lm) for lm in open_hand_landmarks}

    norm_right = compute_spatial_interaction(lms_3d, Handedness.RIGHT).palm_normal
    norm_left = compute_spatial_interaction(lms_3d, Handedness.LEFT).palm_normal

    assert norm_right is not None
    assert norm_left is not None

    len_r = math.sqrt(sum(v * v for v in norm_right))
    assert abs(len_r - 1.0) <= 1e-5

    # Left hand cross product is reversed: v2 x v1 = -(v1 x v2)
    assert abs(norm_right[0] + norm_left[0]) <= 1e-5
    assert abs(norm_right[1] + norm_left[1]) <= 1e-5
    assert abs(norm_right[2] + norm_left[2]) <= 1e-5
