# -*- coding: utf-8 -*-
"""Unit tests for Right-to-Left Handed Coordinate Conversions (D232)."""

from __future__ import annotations

import math
import pytest

from holomed.anatomy.models import Point3D, RigidTransform3D
from holomed.xr.transforms import (
    convert_right_to_left_handed_point,
    convert_right_to_left_handed_quaternion,
    convert_right_to_left_handed_transform,
)


def test_d232_point_right_to_left_handed_conversion() -> None:
    """Verify D232 z-inversion for point coordinates."""
    p_rh = Point3D(1.5, 2.5, 3.5)
    p_lh = convert_right_to_left_handed_point(p_rh)
    assert p_lh.x == 1.5
    assert p_lh.y == 2.5
    assert p_lh.z == -3.5


def test_d232_quaternion_parity_and_canonical_hemisphere() -> None:
    """Verify D232 quaternion inversion preserves parity and canonical hemisphere qw >= 0."""
    # 90 deg around Z: qw=0.707107, qz=0.707107
    q_rh = (0.707107, 0.0, 0.0, 0.707107)
    q_lh = convert_right_to_left_handed_quaternion(q_rh)
    assert q_lh[0] >= 0.0
    assert q_lh == (0.707107, 0.0, 0.0, 0.707107)

    # Inverted qw
    q_neg = (-0.707107, 0.1, 0.2, 0.0)
    q_lh_pos = convert_right_to_left_handed_quaternion(q_neg)
    assert q_lh_pos[0] > 0.0


def test_d232_rigid_transform_conversion() -> None:
    """Verify complete transform conversion."""
    tf_rh = RigidTransform3D(
        translation=Point3D(1.0, 2.0, 3.0),
        rotation_quaternion=(1.0, 0.0, 0.0, 0.0),
        source_frame="frame_A",
        target_frame="frame_B",
    )
    tf_lh = convert_right_to_left_handed_transform(tf_rh)
    assert tf_lh.translation.z == -3.0
    assert tf_lh.source_frame == "frame_A_lh"
