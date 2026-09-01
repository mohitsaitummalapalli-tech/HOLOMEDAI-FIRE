# -*- coding: utf-8 -*-
"""Unit Tests for 3D Navigation Geometry, Vector Projections, and Conversions."""

from __future__ import annotations

import math
import pytest

from holomed.navigation.exceptions import NavigationDegeneracyError
from holomed.navigation.geometry import (
    compute_angular_deviation,
    compute_lateral_and_axial_deviation,
    compute_trajectory_geometry,
    convert_mm_to_meters,
    convert_point_mm_to_meters,
    quaternion_to_shaft_direction,
)


def test_quaternion_to_shaft_direction_identity() -> None:
    """Verify identity quaternion (1, 0, 0, 0) maps local shaft +Z to (0, 0, 1)."""
    q_id = (1.0, 0.0, 0.0, 0.0)
    v = quaternion_to_shaft_direction(q_id)
    assert pytest.approx(v[0], abs=1e-6) == 0.0
    assert pytest.approx(v[1], abs=1e-6) == 0.0
    assert pytest.approx(v[2], abs=1e-6) == 1.0


def test_quaternion_to_shaft_direction_90deg_around_x() -> None:
    """Verify 90-deg rotation around X: (cos(pi/4), sin(pi/4), 0, 0) maps +Z to (0, -1, 0)."""
    # Rot around X by 90 deg: +Z rotates to -Y
    angle = math.pi / 4.0
    q = (math.cos(angle), math.sin(angle), 0.0, 0.0)
    v = quaternion_to_shaft_direction(q)
    assert pytest.approx(v[0], abs=1e-6) == 0.0
    assert pytest.approx(v[1], abs=1e-6) == -1.0
    assert pytest.approx(v[2], abs=1e-6) == 0.0


def test_quaternion_to_shaft_direction_90deg_around_y() -> None:
    """Verify 90-deg rotation around Y: (cos(pi/4), 0, sin(pi/4), 0) maps +Z to (1, 0, 0)."""
    # Rot around Y by 90 deg: +Z rotates to +X
    angle = math.pi / 4.0
    q = (math.cos(angle), 0.0, math.sin(angle), 0.0)
    v = quaternion_to_shaft_direction(q)
    assert pytest.approx(v[0], abs=1e-6) == 1.0
    assert pytest.approx(v[1], abs=1e-6) == 0.0
    assert pytest.approx(v[2], abs=1e-6) == 0.0


def test_quaternion_to_shaft_direction_180deg_around_x() -> None:
    """Verify 180-deg rotation around X: (0, 1, 0, 0) maps +Z to (0, 0, -1)."""
    q = (0.0, 1.0, 0.0, 0.0)
    v = quaternion_to_shaft_direction(q)
    assert pytest.approx(v[0], abs=1e-6) == 0.0
    assert pytest.approx(v[1], abs=1e-6) == 0.0
    assert pytest.approx(v[2], abs=1e-6) == -1.0


def test_compute_trajectory_geometry_length_and_unit_vector() -> None:
    """Verify unit direction and length calculation for trajectory."""
    entry = (10.0, 20.0, 30.0)
    target = (10.0, 20.0, 130.0)
    u_dir, length = compute_trajectory_geometry(entry, target)
    assert pytest.approx(length, abs=1e-6) == 100.0
    assert u_dir == (0.0, 0.0, 1.0)


def test_compute_trajectory_geometry_rejects_sub_10mm() -> None:
    """Verify trajectory length < 10 mm raises NavigationDegeneracyError."""
    entry = (0.0, 0.0, 0.0)
    target = (0.0, 0.0, 9.99)
    with pytest.raises(NavigationDegeneracyError):
        compute_trajectory_geometry(entry, target)


def test_lateral_and_axial_deviation_calculations() -> None:
    """Verify orthogonal lateral distance and axial progress calculation."""
    entry = (0.0, 0.0, 0.0)
    u_dir = (0.0, 0.0, 1.0)

    # 1. Point on axis at z=50: lateral=0, axial=50
    p_on_axis = (0.0, 0.0, 50.0)
    lat, s = compute_lateral_and_axial_deviation(p_on_axis, entry, u_dir)
    assert pytest.approx(lat, abs=1e-6) == 0.0
    assert pytest.approx(s, abs=1e-6) == 50.0

    # 2. Point offset by 3mm in X and 4mm in Y at z=50: lateral=5.0, axial=50
    p_offset = (3.0, 4.0, 50.0)
    lat, s = compute_lateral_and_axial_deviation(p_offset, entry, u_dir)
    assert pytest.approx(lat, abs=1e-6) == 5.0
    assert pytest.approx(s, abs=1e-6) == 50.0

    # 3. Point before entry at z=-25: lateral=0, axial=-25 (PRE_ENTRY)
    p_pre = (0.0, 0.0, -25.0)
    lat, s = compute_lateral_and_axial_deviation(p_pre, entry, u_dir)
    assert pytest.approx(lat, abs=1e-6) == 0.0
    assert pytest.approx(s, abs=1e-6) == -25.0


def test_angular_deviation_calculations() -> None:
    """Verify angular deviation in degrees with domain clamping."""
    u_dir = (0.0, 0.0, 1.0)

    # Parallel -> 0 deg
    assert pytest.approx(compute_angular_deviation(u_dir, (0.0, 0.0, 1.0)), abs=1e-5) == 0.0

    # Perpendicular -> 90 deg
    assert pytest.approx(compute_angular_deviation(u_dir, (1.0, 0.0, 0.0)), abs=1e-5) == 90.0

    # Opposite -> 180 deg
    assert pytest.approx(compute_angular_deviation(u_dir, (0.0, 0.0, -1.0)), abs=1e-5) == 180.0

    # Slight dot product drift > 1.0 or < -1.0 protected by clamp
    assert pytest.approx(compute_angular_deviation((1.0000001, 0.0, 0.0), (1.0, 0.0, 0.0)), abs=1e-5) == 0.0


def test_mm_to_meter_conversion_boundary() -> None:
    """Verify conversion from millimeters to meters across scalar and point helpers."""
    assert convert_mm_to_meters(1500.0) == 1.5
    assert convert_point_mm_to_meters((1000.0, 2500.0, -500.0)) == (1.0, 2.5, -0.5)
