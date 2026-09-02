# -*- coding: utf-8 -*-
"""Deterministic Pure Standard-Library 3D Geometry for M16 Registration Drift."""

from __future__ import annotations

import math
from typing import Sequence, Tuple

from holomed.drift.exceptions import DriftValidationError

Point3D = Tuple[float, float, float]


def compute_euclidean_residual(
    p_expected: Point3D,
    p_observed: Point3D,
) -> float:
    """Compute 3D Euclidean distance (residual error) between expected and observed points.

    residual = ||p_expected - p_observed||
    Result in millimeters (non-negative scalar).
    """
    dx = p_expected[0] - p_observed[0]
    dy = p_expected[1] - p_observed[1]
    dz = p_expected[2] - p_observed[2]
    return math.sqrt(dx * dx + dy * dy + dz * dz)


def compute_effective_tolerance(
    landmark_max_tolerance_mm: float,
    global_max_allowed_drift_mm: float,
) -> float:
    """Compute effective tolerance enforcing global safety ceiling.

    effective_tolerance = min(landmark_max_tolerance_mm, global_max_allowed_drift_mm)
    """
    if not isinstance(landmark_max_tolerance_mm, (int, float)) or not math.isfinite(landmark_max_tolerance_mm):
        raise DriftValidationError("landmark_max_tolerance_mm must be finite float")
    if landmark_max_tolerance_mm <= 0.0:
        raise DriftValidationError("landmark_max_tolerance_mm must be positive")

    return min(landmark_max_tolerance_mm, global_max_allowed_drift_mm)


def compute_dwell_stability(
    sample_points: Sequence[Point3D],
) -> float:
    """Compute maximum spatial jitter radius (dispersion from centroid) across dwell buffer.

    Returns the maximum distance of any sample point from the centroid of all samples.
    """
    if not sample_points:
        return 0.0

    n = len(sample_points)
    cx = sum(p[0] for p in sample_points) / n
    cy = sum(p[1] for p in sample_points) / n
    cz = sum(p[2] for p in sample_points) / n
    centroid = (cx, cy, cz)

    max_dist = 0.0
    for p in sample_points:
        d = compute_euclidean_residual(centroid, p)
        if d > max_dist:
            max_dist = d

    return max_dist


def convert_point_mm_to_meters(point_mm: Point3D) -> Point3D:
    """Convert 3D point in millimeters to meters (presentation boundary)."""
    return (point_mm[0] / 1000.0, point_mm[1] / 1000.0, point_mm[2] / 1000.0)
