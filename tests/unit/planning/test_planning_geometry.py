# -*- coding: utf-8 -*-
"""Unit Tests for Pure Standard-Library 3D Vector Geometry."""

from __future__ import annotations

import math
import pytest

from holomed.planning.exceptions import PlanningValidationError
from holomed.planning.geometry import (
    compute_trajectory_unit_vector,
    euclidean_distance_3d,
    point_to_line_segment_distance,
)


def test_euclidean_distance_3d() -> None:
    """Verify 3D Euclidean distance calculation."""
    p1 = (0.0, 0.0, 0.0)
    p2 = (3.0, 4.0, 0.0)
    assert euclidean_distance_3d(p1, p2) == 5.0

    p3 = (1.0, 2.0, 2.0)
    p4 = (4.0, 6.0, 14.0)
    # dx=3, dy=4, dz=12 -> 3^2+4^2+12^2 = 9+16+144 = 169 -> sqrt=13
    assert euclidean_distance_3d(p3, p4) == 13.0


def test_compute_trajectory_unit_vector() -> None:
    """Verify trajectory unit vector calculation and normalization."""
    entry = (0.0, 0.0, 0.0)
    target = (0.0, 0.0, 10.0)
    u = compute_trajectory_unit_vector(entry, target)
    assert u == (0.0, 0.0, 1.0)

    # 3D vector: (1, 1, 1) normalized -> length = 1.0
    u3 = compute_trajectory_unit_vector((0.0, 0.0, 0.0), (10.0, 10.0, 10.0))
    norm = math.sqrt(u3[0] * u3[0] + u3[1] * u3[1] + u3[2] * u3[2])
    assert pytest.approx(norm, rel=1e-6) == 1.0

    # Coincident points raise PlanningValidationError
    with pytest.raises(PlanningValidationError):
        compute_trajectory_unit_vector((5.0, 5.0, 5.0), (5.0, 5.0, 5.0))


def test_point_to_line_segment_distance() -> None:
    """Verify perpendicular distance calculation from point to line segment."""
    start = (0.0, 0.0, 0.0)
    end = (10.0, 0.0, 0.0)

    # Point directly above segment at (5, 3, 0) -> distance should be 3.0
    pt = (5.0, 3.0, 0.0)
    dist = point_to_line_segment_distance(pt, start, end)
    assert dist == 3.0

    # Point beyond start point at (-4, 3, 0) -> distance to start (0, 0, 0) = 5.0
    pt_before = (-4.0, 3.0, 0.0)
    assert point_to_line_segment_distance(pt_before, start, end) == 5.0

    # Point beyond end point at (14, 3, 0) -> distance to end (10, 0, 0) = 5.0
    pt_after = (14.0, 3.0, 0.0)
    assert point_to_line_segment_distance(pt_after, start, end) == 5.0
