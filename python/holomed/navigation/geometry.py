# -*- coding: utf-8 -*-
"""Deterministic Pure Standard-Library 3D Navigation Geometry for M14."""

from __future__ import annotations

import math
from typing import Tuple

from holomed.navigation.exceptions import NavigationDegeneracyError

Point3D = Tuple[float, float, float]
Vector3D = Tuple[float, float, float]
Quaternion = Tuple[float, float, float, float]  # (qw, qx, qy, qz)


def quaternion_to_shaft_direction(q: Quaternion) -> Vector3D:
    """Extract physical tool shaft direction vector from unit quaternion.
    
    Transforms the canonical tool shaft axis pointing along local +Z:
        v = R(q) * (0, 0, 1)
        vx = 2 * (qx * qz + qw * qy)
        vy = 2 * (qy * qz - qw * qx)
        vz = 1 - 2 * (qx^2 + qy^2)
    """
    qw, qx, qy, qz = q
    vx = 2.0 * (qx * qz + qw * qy)
    vy = 2.0 * (qy * qz - qw * qx)
    vz = 1.0 - 2.0 * (qx * qx + qy * qy)

    norm = math.sqrt(vx * vx + vy * vy + vz * vz)
    if norm < 1e-6:
        raise NavigationDegeneracyError("Extracted shaft direction vector is degenerate")

    return (vx / norm, vy / norm, vz / norm)


def compute_trajectory_geometry(entry: Point3D, target: Point3D) -> Tuple[Vector3D, float]:
    """Compute trajectory unit direction vector and length."""
    dx = target[0] - entry[0]
    dy = target[1] - entry[1]
    dz = target[2] - entry[2]
    length = math.sqrt(dx * dx + dy * dy + dz * dz)
    if length < 10.0:  # MIN_TRAJECTORY_LENGTH_MM
        raise NavigationDegeneracyError(f"Trajectory length ({length:.2f} mm) is less than minimum 10.0 mm")

    return (dx / length, dy / length, dz / length), length


def compute_lateral_and_axial_deviation(
    point: Point3D,
    entry: Point3D,
    u_dir: Vector3D,
) -> Tuple[float, float]:
    """Compute orthogonal lateral deviation (mm) and axial progress (mm) relative to planned trajectory."""
    wx = point[0] - entry[0]
    wy = point[1] - entry[1]
    wz = point[2] - entry[2]

    # Axial progress s = w . u
    s = wx * u_dir[0] + wy * u_dir[1] + wz * u_dir[2]

    # Nearest point on trajectory axis: P_proj = entry + s * u
    proj_x = entry[0] + s * u_dir[0]
    proj_y = entry[1] + s * u_dir[1]
    proj_z = entry[2] + s * u_dir[2]

    # Orthogonal lateral vector: d = point - P_proj
    dx = point[0] - proj_x
    dy = point[1] - proj_y
    dz = point[2] - proj_z
    e_lateral = math.sqrt(dx * dx + dy * dy + dz * dz)

    return e_lateral, s


def compute_angular_deviation(u_dir: Vector3D, v_shaft: Vector3D) -> float:
    """Compute angular deviation in degrees between planned direction and tool shaft direction."""
    dot = u_dir[0] * v_shaft[0] + u_dir[1] * v_shaft[1] + u_dir[2] * v_shaft[2]
    # Clamp to [-1.0, 1.0] to prevent floating point domain errors in acos
    clamped_dot = max(-1.0, min(1.0, dot))
    rad = math.acos(clamped_dot)
    return rad * (180.0 / math.pi)


def convert_mm_to_meters(value_mm: float) -> float:
    """Convert millimeter scalar to meter scalar (M06 boundary)."""
    return value_mm / 1000.0


def convert_point_mm_to_meters(point_mm: Point3D) -> Point3D:
    """Convert 3D point in millimeters to meters (M06 boundary)."""
    return (point_mm[0] / 1000.0, point_mm[1] / 1000.0, point_mm[2] / 1000.0)
