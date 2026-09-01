# -*- coding: utf-8 -*-
"""Spatial Coordinate Transformation Operations for M13 Registration."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Tuple

from holomed.planning.models import TrajectoryPlan
from holomed.registration.geometry import (
    matrix_mult_3x3,
    matrix_mult_vector_3x3,
    matrix_transpose_3x3,
)
from holomed.registration.models import RigidRegistrationTransform3D

Point3D = Tuple[float, float, float]
Vector3D = Tuple[float, float, float]


def transform_point(transform: RigidRegistrationTransform3D, point: Point3D) -> Point3D:
    """Transform 3D point from source frame to target frame: p' = R * p + t."""
    R = transform.rotation_matrix
    t = transform.translation_vector_mm
    return (
        R[0][0] * point[0] + R[0][1] * point[1] + R[0][2] * point[2] + t[0],
        R[1][0] * point[0] + R[1][1] * point[1] + R[1][2] * point[2] + t[1],
        R[2][0] * point[0] + R[2][1] * point[1] + R[2][2] * point[2] + t[2],
    )


def transform_vector(transform: RigidRegistrationTransform3D, vector: Vector3D) -> Vector3D:
    """Transform 3D directional vector from source frame to target frame: v' = R * v.
    
    CRITICAL INVARIANT: Translation vector t is NEVER applied to directional vectors.
    """
    R = transform.rotation_matrix
    return matrix_mult_vector_3x3(R, vector)


def invert_transform(transform: RigidRegistrationTransform3D) -> RigidRegistrationTransform3D:
    """Compute mathematical inverse transform T^-1: R_inv = R^T, t_inv = -R^T * t."""
    R = transform.rotation_matrix
    t = transform.translation_vector_mm

    R_inv = matrix_transpose_3x3(R)
    # -R^T * t
    t_inv = (
        -(R_inv[0][0] * t[0] + R_inv[0][1] * t[1] + R_inv[0][2] * t[2]),
        -(R_inv[1][0] * t[0] + R_inv[1][1] * t[1] + R_inv[1][2] * t[2]),
        -(R_inv[2][0] * t[0] + R_inv[2][1] * t[1] + R_inv[2][2] * t[2]),
    )

    now_utc = datetime.now(timezone.utc).isoformat()
    return RigidRegistrationTransform3D(
        rotation_matrix=R_inv,
        translation_vector_mm=t_inv,
        source_frame=transform.target_frame,
        target_frame=transform.source_frame,
        epoch_id=transform.epoch_id,
        created_at_utc=now_utc,
    )


def compose_transforms(
    t1: RigidRegistrationTransform3D,
    t2: RigidRegistrationTransform3D,
) -> RigidRegistrationTransform3D:
    """Compose two rigid transforms: T_comp(p) = T2(T1(p)).
    
    R_comp = R2 * R1
    t_comp = R2 * t1 + t2
    """
    R1 = t1.rotation_matrix
    t1_vec = t1.translation_vector_mm
    R2 = t2.rotation_matrix
    t2_vec = t2.translation_vector_mm

    R_comp = matrix_mult_3x3(R2, R1)
    t_comp_temp = matrix_mult_vector_3x3(R2, t1_vec)
    t_comp = (
        t_comp_temp[0] + t2_vec[0],
        t_comp_temp[1] + t2_vec[1],
        t_comp_temp[2] + t2_vec[2],
    )

    now_utc = datetime.now(timezone.utc).isoformat()
    return RigidRegistrationTransform3D(
        rotation_matrix=R_comp,
        translation_vector_mm=t_comp,
        source_frame=t1.source_frame,
        target_frame=t2.target_frame,
        epoch_id=t2.epoch_id,
        created_at_utc=now_utc,
    )


def transform_trajectory(
    transform: RigidRegistrationTransform3D,
    trajectory: TrajectoryPlan,
) -> TrajectoryPlan:
    """Transform M12 TrajectoryPlan entry and target points into physical patient frame."""
    new_entry = transform_point(transform, trajectory.entry_point_mm)
    new_target = transform_point(transform, trajectory.target_point_mm)

    return TrajectoryPlan(
        trajectory_id=trajectory.trajectory_id,
        target_structure=trajectory.target_structure,
        entry_point_mm=new_entry,
        target_point_mm=new_target,
        max_lateral_deviation_mm=trajectory.max_lateral_deviation_mm,
        max_angular_deviation_deg=trajectory.max_angular_deviation_deg,
        min_confidence=trajectory.min_confidence,
        max_uncertainty=trajectory.max_uncertainty,
    )
