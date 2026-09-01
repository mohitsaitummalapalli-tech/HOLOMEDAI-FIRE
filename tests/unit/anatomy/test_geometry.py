# -*- coding: utf-8 -*-
"""Unit tests for 3D Geometry and Transform Math (D223)."""

from __future__ import annotations

import math
import pytest

from holomed.anatomy.exceptions import AnatomyValidationError
from holomed.anatomy.geometry import (
    apply_rigid_transform,
    compose_rigid_transforms,
    invert_rigid_transform,
    point_add_vector,
    point_distance,
    point_sub_point,
    quaternion_inverse,
    quaternion_multiply,
    rotate_vector_by_quaternion,
    vector_add,
    vector_cross,
    vector_dot,
    vector_scale,
    vector_sub,
)
from holomed.anatomy.models import Point3D, RigidTransform3D, Vector3D


def test_vector_and_point_arithmetic() -> None:
    """Verify basic vector and point arithmetic operations."""
    v1 = Vector3D(1.0, 2.0, 3.0)
    v2 = Vector3D(4.0, 5.0, 6.0)

    # Addition & Subtraction
    assert vector_add(v1, v2) == Vector3D(5.0, 7.0, 9.0)
    assert vector_sub(v2, v1) == Vector3D(3.0, 3.0, 3.0)

    # Scaling & Dot Product
    assert vector_scale(v1, 2.0) == Vector3D(2.0, 4.0, 6.0)
    assert vector_dot(v1, v2) == 1.0 * 4.0 + 2.0 * 5.0 + 3.0 * 6.0

    # Cross product (i x j = k)
    vi = Vector3D(1.0, 0.0, 0.0)
    vj = Vector3D(0.0, 1.0, 0.0)
    assert vector_cross(vi, vj) == Vector3D(0.0, 0.0, 1.0)

    # Point-Vector Displacements
    p = Point3D(1.0, 1.0, 1.0)
    p_displaced = point_add_vector(p, vi)
    assert p_displaced == Point3D(2.0, 1.0, 1.0)
    assert point_sub_point(p_displaced, p) == vi


def test_quaternion_rotation_and_inversion() -> None:
    """Verify quaternion 90-degree rotation around Z axis."""
    # 90 degrees around Z axis: qw = cos(45) = 0.707107, qz = sin(45) = 0.707107
    qw = math.cos(math.pi / 4.0)
    qz = math.sin(math.pi / 4.0)
    q_z90 = (round(qw, 6), 0.0, 0.0, round(qz, 6))

    # Rotate vector along X axis (1, 0, 0) -> should become (0, 1, 0)
    vx = Vector3D(1.0, 0.0, 0.0)
    v_rot = rotate_vector_by_quaternion(vx, q_z90)
    assert abs(v_rot.dx - 0.0) < 1e-4
    assert abs(v_rot.dy - 1.0) < 1e-4
    assert abs(v_rot.dz - 0.0) < 1e-4

    # Rotate back with inverse quaternion
    q_inv = quaternion_inverse(q_z90)
    v_back = rotate_vector_by_quaternion(v_rot, q_inv)
    assert abs(v_back.dx - 1.0) < 1e-4
    assert abs(v_back.dy - 0.0) < 1e-4


def test_d223_rigid_transform_apply_invert_and_compose() -> None:
    """Verify D223 transform application, inversion, and composition."""
    # 90 deg around Z + translation (1, 2, 3)
    qw = round(math.cos(math.pi / 4.0), 6)
    qz = round(math.sin(math.pi / 4.0), 6)
    tf_ab = RigidTransform3D(
        translation=Point3D(1.0, 2.0, 3.0),
        rotation_quaternion=(qw, 0.0, 0.0, qz),
        source_frame="frame_A",
        target_frame="frame_B",
    )

    # Point in A: (1, 0, 0)
    pt_a = Point3D(1.0, 0.0, 0.0)
    pt_b = apply_rigid_transform(tf_ab, pt_a)
    # Expected: (0, 1, 0) + (1, 2, 3) = (1, 3, 3)
    assert abs(pt_b.x - 1.0) < 1e-4
    assert abs(pt_b.y - 3.0) < 1e-4
    assert abs(pt_b.z - 3.0) < 1e-4

    # Invert transform T_ba: T_ba(pt_b) should yield pt_a
    tf_ba = invert_rigid_transform(tf_ab)
    assert tf_ba.source_frame == "frame_B"
    assert tf_ba.target_frame == "frame_A"
    pt_a_reconstructed = apply_rigid_transform(tf_ba, pt_b)
    assert abs(pt_a_reconstructed.x - pt_a.x) < 1e-4
    assert abs(pt_a_reconstructed.y - pt_a.y) < 1e-4
    assert abs(pt_a_reconstructed.z - pt_a.z) < 1e-4

    # Composition T_bc * T_ab
    tf_bc = RigidTransform3D(
        translation=Point3D(0.0, 0.0, 5.0),
        rotation_quaternion=(1.0, 0.0, 0.0, 0.0),
        source_frame="frame_B",
        target_frame="frame_C",
    )
    tf_ac = compose_rigid_transforms(tf_bc, tf_ab)
    assert tf_ac.source_frame == "frame_A"
    assert tf_ac.target_frame == "frame_C"
    pt_c = apply_rigid_transform(tf_ac, pt_a)
    # Expected: (1, 3, 3) + (0, 0, 5) = (1, 3, 8)
    assert abs(pt_c.x - 1.0) < 1e-4
    assert abs(pt_c.y - 3.0) < 1e-4
    assert abs(pt_c.z - 8.0) < 1e-4

    # Frame mismatch error
    tf_mismatch = RigidTransform3D(
        translation=Point3D(0.0, 0.0, 0.0),
        rotation_quaternion=(1.0, 0.0, 0.0, 0.0),
        source_frame="wrong_frame",
        target_frame="frame_C",
    )
    with pytest.raises(AnatomyValidationError):
        compose_rigid_transforms(tf_mismatch, tf_ab)
