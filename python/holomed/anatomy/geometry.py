# -*- coding: utf-8 -*-
"""Deterministic 3D Geometry and Spatial Math Primitives for M05 Anatomy."""

from __future__ import annotations

import math
from typing import Tuple

from holomed.anatomy.exceptions import AnatomyValidationError
from holomed.anatomy.models import (
    BoundingBox3D,
    COORDINATE_EPSILON,
    MAX_ANATOMY_COORDINATE_METERS,
    Point3D,
    RigidTransform3D,
    Vector3D,
)


def point_distance(p1: Point3D, p2: Point3D) -> float:
    """Compute Euclidean distance between two Point3D coordinates."""
    dx = p1.x - p2.x
    dy = p1.y - p2.y
    dz = p1.z - p2.z
    return math.sqrt(dx * dx + dy * dy + dz * dz)


def vector_add(v1: Vector3D, v2: Vector3D) -> Vector3D:
    """Add two 3D vectors."""
    return Vector3D(v1.dx + v2.dx, v1.dy + v2.dy, v1.dz + v2.dz)


def vector_sub(v1: Vector3D, v2: Vector3D) -> Vector3D:
    """Subtract vector v2 from v1."""
    return Vector3D(v1.dx - v2.dx, v1.dy - v2.dy, v1.dz - v2.dz)


def vector_scale(v: Vector3D, scale: float) -> Vector3D:
    """Scale a 3D vector by scalar factor."""
    if not math.isfinite(scale):
        raise AnatomyValidationError(f"Scale factor must be finite, got {scale!r}")
    return Vector3D(v.dx * scale, v.dy * scale, v.dz * scale)


def vector_dot(v1: Vector3D, v2: Vector3D) -> float:
    """Compute scalar dot product between two vectors."""
    return v1.dx * v2.dx + v1.dy * v2.dy + v1.dz * v2.dz


def vector_cross(v1: Vector3D, v2: Vector3D) -> Vector3D:
    """Compute vector cross product v1 x v2."""
    cx = v1.dy * v2.dz - v1.dz * v2.dy
    cy = v1.dz * v2.dx - v1.dx * v2.dz
    cz = v1.dx * v2.dy - v1.dy * v2.dx
    return Vector3D(cx, cy, cz)


def point_add_vector(p: Point3D, v: Vector3D) -> Point3D:
    """Displace a point by a vector."""
    return Point3D(p.x + v.dx, p.y + v.dy, p.z + v.dz)


def point_sub_point(p1: Point3D, p2: Point3D) -> Vector3D:
    """Compute displacement vector pointing from p2 to p1."""
    return Vector3D(p1.x - p2.x, p1.y - p2.y, p1.z - p2.z)


# -----------------------------------------------------------------------------
# Quaternion Operations
# -----------------------------------------------------------------------------

def quaternion_multiply(
    q1: tuple[float, float, float, float],
    q2: tuple[float, float, float, float],
) -> tuple[float, float, float, float]:
    """Hamilton quaternion product q1 * q2 for quaternions (qw, qx, qy, qz)."""
    w1, x1, y1, z1 = q1
    w2, x2, y2, z2 = q2

    w = w1 * w2 - x1 * x2 - y1 * y2 - z1 * z2
    x = w1 * x2 + x1 * w2 + y1 * z2 - z1 * y2
    y = w1 * y2 - x1 * z2 + y1 * w2 + z1 * x2
    z = w1 * z2 + x1 * y2 - y1 * x2 + z1 * w2

    # Canonical hemisphere convention (qw >= 0)
    if w < 0.0:
        w, x, y, z = -w, -x, -y, -z

    return (round(w, 6), round(x, 6), round(y, 6), round(z, 6))


def quaternion_inverse(q: tuple[float, float, float, float]) -> tuple[float, float, float, float]:
    """Conjugate/inverse for unit quaternion (qw, qx, qy, qz)."""
    qw, qx, qy, qz = q
    # Conjugate
    inv_w, inv_x, inv_y, inv_z = qw, -qx, -qy, -qz
    if inv_w < 0.0:
        inv_w, inv_x, inv_y, inv_z = -inv_w, -inv_x, -inv_y, -inv_z
    return (round(inv_w, 6), round(inv_x, 6), round(inv_y, 6), round(inv_z, 6))


def rotate_vector_by_quaternion(
    v: Vector3D,
    q: tuple[float, float, float, float],
) -> Vector3D:
    """Rotate 3D vector by unit quaternion (qw, qx, qy, qz): v' = q * v * q^-1."""
    qw, qx, qy, qz = q
    # Optimized quaternion vector rotation formula
    # v' = v + 2 * r x (r x v + qw * v) where r = (qx, qy, qz)
    rx, ry, rz = qx, qy, qz
    # cross1 = r x v + qw * v
    cx1 = ry * v.dz - rz * v.dy + qw * v.dx
    cy1 = rz * v.dx - rx * v.dz + qw * v.dy
    cz1 = rx * v.dy - ry * v.dx + qw * v.dz

    # cross2 = r x cross1
    cx2 = ry * cz1 - rz * cy1
    cy2 = rz * cx1 - rx * cz1
    cz2 = rx * cy1 - ry * cx1

    res_x = v.dx + 2.0 * cx2
    res_y = v.dy + 2.0 * cy2
    res_z = v.dz + 2.0 * cz2

    return Vector3D(res_x, res_y, res_z)


# -----------------------------------------------------------------------------
# RigidTransform3D Composition and Inversion
# -----------------------------------------------------------------------------

def apply_rigid_transform(tf: RigidTransform3D, pt: Point3D) -> Point3D:
    """Apply rigid transformation p_target = R * p_source + t."""
    vec = Vector3D(pt.x, pt.y, pt.z)
    rotated_vec = rotate_vector_by_quaternion(vec, tf.rotation_quaternion)
    res_x = rotated_vec.dx + tf.translation.x
    res_y = rotated_vec.dy + tf.translation.y
    res_z = rotated_vec.dz + tf.translation.z
    return Point3D(res_x, res_y, res_z)


def invert_rigid_transform(tf: RigidTransform3D) -> RigidTransform3D:
    """Compute analytical inverse transformation T^-1."""
    q_inv = quaternion_inverse(tf.rotation_quaternion)
    # t' = -R^-1 * t
    t_vec = Vector3D(tf.translation.x, tf.translation.y, tf.translation.z)
    rot_t = rotate_vector_by_quaternion(t_vec, q_inv)
    inv_t = Point3D(-rot_t.dx, -rot_t.dy, -rot_t.dz)

    return RigidTransform3D(
        translation=inv_t,
        rotation_quaternion=q_inv,
        source_frame=tf.target_frame,
        target_frame=tf.source_frame,
    )


def compose_rigid_transforms(tf_bc: RigidTransform3D, tf_ab: RigidTransform3D) -> RigidTransform3D:
    """Compose two rigid transforms T_ac = T_bc * T_ab.
    
    Validates that tf_ab.target_frame == tf_bc.source_frame.
    """
    if tf_ab.target_frame != tf_bc.source_frame:
        raise AnatomyValidationError(
            f"Frame mismatch in transform composition: {tf_ab.target_frame!r} != {tf_bc.source_frame!r}"
        )

    # R_ac = R_bc * R_ab
    q_ac = quaternion_multiply(tf_bc.rotation_quaternion, tf_ab.rotation_quaternion)

    # t_ac = R_bc * t_ab + t_bc
    t_ab_vec = Vector3D(tf_ab.translation.x, tf_ab.translation.y, tf_ab.translation.z)
    rot_t_ab = rotate_vector_by_quaternion(t_ab_vec, tf_bc.rotation_quaternion)
    t_ac = Point3D(
        rot_t_ab.dx + tf_bc.translation.x,
        rot_t_ab.dy + tf_bc.translation.y,
        rot_t_ab.dz + tf_bc.translation.z,
    )

    return RigidTransform3D(
        translation=t_ac,
        rotation_quaternion=q_ac,
        source_frame=tf_ab.source_frame,
        target_frame=tf_bc.target_frame,
    )
