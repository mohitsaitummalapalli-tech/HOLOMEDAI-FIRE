# -*- coding: utf-8 -*-
"""Deterministic Pure Standard-Library 3D Vector Geometry for M12 Planning."""

from __future__ import annotations

import math
from typing import Tuple

from holomed.planning.exceptions import PlanningValidationError

Point3D = Tuple[float, float, float]
Vector3D = Tuple[float, float, float]


def euclidean_distance_3d(p1: Point3D, p2: Point3D) -> float:
    """Compute Euclidean distance between two 3D points."""
    dx = p2[0] - p1[0]
    dy = p2[1] - p1[1]
    dz = p2[2] - p1[2]
    return math.sqrt(dx * dx + dy * dy + dz * dz)


def compute_trajectory_unit_vector(entry: Point3D, target: Point3D) -> Vector3D:
    """Compute normalized unit direction vector from entry to target point."""
    dx = target[0] - entry[0]
    dy = target[1] - entry[1]
    dz = target[2] - entry[2]
    length = math.sqrt(dx * dx + dy * dy + dz * dz)
    if length < 1e-9:
        raise PlanningValidationError("Cannot compute unit vector for coincident points")
    return (dx / length, dy / length, dz / length)


def point_to_line_segment_distance(point: Point3D, start: Point3D, end: Point3D) -> float:
    """Calculate minimum perpendicular distance from a 3D point to a line segment."""
    dx = end[0] - start[0]
    dy = end[1] - start[1]
    dz = end[2] - start[2]
    seg_len_sq = dx * dx + dy * dy + dz * dz

    if seg_len_sq < 1e-9:
        return euclidean_distance_3d(point, start)

    # Project point onto line segment clamped to [0, 1]
    px = point[0] - start[0]
    py = point[1] - start[1]
    pz = point[2] - start[2]

    t = max(0.0, min(1.0, (px * dx + py * dy + pz * dz) / seg_len_sq))
    proj_x = start[0] + t * dx
    proj_y = start[1] + t * dy
    proj_z = start[2] + t * dz

    return euclidean_distance_3d(point, (proj_x, proj_y, proj_z))
