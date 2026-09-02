# -*- coding: utf-8 -*-
"""Deterministic Pure Standard-Library 3D Proximity Geometry for M15.

All clearance computations operate in millimeters internally.
Capsule model: a shaft is a finite line segment dilated by shaft_radius_mm.
"""

from __future__ import annotations

import math
from typing import Tuple

from holomed.proximity.exceptions import ProximityValidationError

Point3D = Tuple[float, float, float]
Vector3D = Tuple[float, float, float]


def point_to_sphere_clearance(
    point: Point3D,
    sphere_center: Point3D,
    sphere_radius: float,
) -> float:
    """Compute signed clearance from a point to the surface of a sphere.

    clearance = ||point - center|| - sphere_radius
    Positive: outside the sphere.  Negative: inside.
    """
    dx = point[0] - sphere_center[0]
    dy = point[1] - sphere_center[1]
    dz = point[2] - sphere_center[2]
    dist = math.sqrt(dx * dx + dy * dy + dz * dz)
    return dist - sphere_radius


def _closest_point_on_segment(
    seg_start: Point3D,
    seg_end: Point3D,
    query: Point3D,
) -> Point3D:
    """Find the closest point on a finite line segment [seg_start, seg_end] to query.

    Parameter t is clamped to [0, 1].
    """
    dx = seg_end[0] - seg_start[0]
    dy = seg_end[1] - seg_start[1]
    dz = seg_end[2] - seg_start[2]
    seg_len_sq = dx * dx + dy * dy + dz * dz

    if seg_len_sq < 1e-12:
        # Degenerate segment (zero length) — return start
        return seg_start

    # Project query onto segment line
    wx = query[0] - seg_start[0]
    wy = query[1] - seg_start[1]
    wz = query[2] - seg_start[2]
    t = (wx * dx + wy * dy + wz * dz) / seg_len_sq

    # Clamp t to [0, 1]
    if t < 0.0:
        t = 0.0
    elif t > 1.0:
        t = 1.0

    return (
        seg_start[0] + t * dx,
        seg_start[1] + t * dy,
        seg_start[2] + t * dz,
    )


def capsule_to_sphere_clearance(
    tip_position: Point3D,
    shaft_direction: Vector3D,
    shaft_length: float,
    shaft_radius: float,
    sphere_center: Point3D,
    sphere_radius: float,
) -> float:
    """Compute signed clearance from a capsule (shaft) to the surface of a sphere.

    The capsule is defined as a finite line segment from tip_position along
    shaft_direction for shaft_length, dilated by shaft_radius.

    clearance = ||P_closest - sphere_center|| - sphere_radius - shaft_radius

    Where P_closest is the closest point on the shaft line segment to sphere_center.
    """
    # Compute shaft segment endpoint (tip -> tail along shaft_direction)
    seg_end = (
        tip_position[0] + shaft_direction[0] * shaft_length,
        tip_position[1] + shaft_direction[1] * shaft_length,
        tip_position[2] + shaft_direction[2] * shaft_length,
    )

    # Find closest point on segment to sphere center
    closest = _closest_point_on_segment(tip_position, seg_end, sphere_center)

    # Compute distance from closest point to sphere center
    dx = closest[0] - sphere_center[0]
    dy = closest[1] - sphere_center[1]
    dz = closest[2] - sphere_center[2]
    dist = math.sqrt(dx * dx + dy * dy + dz * dz)

    # clearance = distance - sphere_radius - shaft_radius
    return dist - sphere_radius - shaft_radius


def compute_effective_radius(
    zone_bounding_radius_mm: float,
    static_margin_mm: float,
    registration_error_mm: float,
    tracking_uncertainty_mm: float,
) -> float:
    """Compute dynamic effective exclusion zone radius.

    R_effective = R_zone + static_margin + registration_error_mm
                  + 2 * tracking_uncertainty_mm

    All parameters must be non-negative and finite.
    """
    for name, val in (
        ("zone_bounding_radius_mm", zone_bounding_radius_mm),
        ("static_margin_mm", static_margin_mm),
        ("registration_error_mm", registration_error_mm),
        ("tracking_uncertainty_mm", tracking_uncertainty_mm),
    ):
        if not isinstance(val, (int, float)) or not math.isfinite(val) or val < 0.0:
            raise ProximityValidationError(f"{name} must be non-negative finite float, got {val!r}")

    return zone_bounding_radius_mm + static_margin_mm + registration_error_mm + 2.0 * tracking_uncertainty_mm


def convert_mm_to_meters(value_mm: float) -> float:
    """Convert millimeter scalar to meter scalar (M06 XR boundary)."""
    return value_mm / 1000.0


def convert_point_mm_to_meters(point_mm: Point3D) -> Point3D:
    """Convert 3D point in millimeters to meters (M06 XR boundary)."""
    return (point_mm[0] / 1000.0, point_mm[1] / 1000.0, point_mm[2] / 1000.0)
