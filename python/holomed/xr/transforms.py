# -*- coding: utf-8 -*-
"""Spatial Coordinate Transformations and Right/Left-Handed Conversions (D232)."""

from __future__ import annotations

import math
from typing import Tuple

from holomed.anatomy.geometry import (
    apply_rigid_transform,
    compose_rigid_transforms,
    invert_rigid_transform,
    point_distance,
    quaternion_inverse,
    quaternion_multiply,
    rotate_vector_by_quaternion,
)
from holomed.anatomy.models import Point3D, RigidTransform3D, Vector3D
from holomed.xr.exceptions import XRValidationError


def convert_right_to_left_handed_point(pt_rh: Point3D) -> Point3D:
    """Convert right-handed Point3D to Unity left-handed metric coordinate.
    
    x_LH = x_RH
    y_LH = y_RH
    z_LH = -z_RH
    """
    return Point3D(pt_rh.x, pt_rh.y, -pt_rh.z)


def convert_right_to_left_handed_quaternion(
    q_rh: tuple[float, float, float, float],
) -> tuple[float, float, float, float]:
    """Convert right-handed unit quaternion to Unity left-handed orientation.
    
    qw_LH = qw_RH
    qx_LH = -qx_RH
    qy_LH = -qy_RH
    qz_LH = qz_RH
    Enforces canonical hemisphere qw >= 0.0.
    """
    qw, qx, qy, qz = q_rh
    qw_lh = float(qw)
    qx_lh = -float(qx)
    qy_lh = -float(qy)
    qz_lh = float(qz)

    if qw_lh < 0.0:
        qw_lh, qx_lh, qy_lh, qz_lh = -qw_lh, -qx_lh, -qy_lh, -qz_lh

    w = 0.0 if qw_lh == 0.0 else round(qw_lh, 6)
    x = 0.0 if qx_lh == 0.0 else round(qx_lh, 6)
    y = 0.0 if qy_lh == 0.0 else round(qy_lh, 6)
    z = 0.0 if qz_lh == 0.0 else round(qz_lh, 6)

    return (w, x, y, z)


def convert_right_to_left_handed_transform(tf_rh: RigidTransform3D) -> RigidTransform3D:
    """Convert right-handed RigidTransform3D to Unity left-handed RigidTransform3D."""
    t_lh = convert_right_to_left_handed_point(tf_rh.translation)
    q_lh = convert_right_to_left_handed_quaternion(tf_rh.rotation_quaternion)
    return RigidTransform3D(
        translation=t_lh,
        rotation_quaternion=q_lh,
        source_frame=f"{tf_rh.source_frame}_lh",
        target_frame=f"{tf_rh.target_frame}_lh",
    )
