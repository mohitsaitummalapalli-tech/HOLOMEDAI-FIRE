# -*- coding: utf-8 -*-
"""Unit Tests for M15 Proximity Geometry — Point/Capsule Clearance & Effective Radius."""

from __future__ import annotations

import math

import pytest

from holomed.proximity.exceptions import ProximityValidationError
from holomed.proximity.geometry import (
    capsule_to_sphere_clearance,
    compute_effective_radius,
    convert_mm_to_meters,
    convert_point_mm_to_meters,
    point_to_sphere_clearance,
)


# ===========================================================================
# Point-to-Sphere Clearance
# ===========================================================================

class TestPointToSphereClearance:
    """Tests for point_to_sphere_clearance()."""

    def test_point_outside_sphere(self) -> None:
        clearance = point_to_sphere_clearance((20.0, 0.0, 0.0), (0.0, 0.0, 0.0), 10.0)
        assert math.isclose(clearance, 10.0, abs_tol=1e-9)

    def test_point_on_sphere_surface(self) -> None:
        clearance = point_to_sphere_clearance((10.0, 0.0, 0.0), (0.0, 0.0, 0.0), 10.0)
        assert math.isclose(clearance, 0.0, abs_tol=1e-9)

    def test_point_inside_sphere(self) -> None:
        clearance = point_to_sphere_clearance((5.0, 0.0, 0.0), (0.0, 0.0, 0.0), 10.0)
        assert clearance < 0.0
        assert math.isclose(clearance, -5.0, abs_tol=1e-9)

    def test_point_at_center(self) -> None:
        clearance = point_to_sphere_clearance((0.0, 0.0, 0.0), (0.0, 0.0, 0.0), 10.0)
        assert math.isclose(clearance, -10.0, abs_tol=1e-9)

    def test_diagonal_distance(self) -> None:
        # Point at (10, 10, 10), center at origin, radius 5
        dist = math.sqrt(300.0)
        clearance = point_to_sphere_clearance((10.0, 10.0, 10.0), (0.0, 0.0, 0.0), 5.0)
        assert math.isclose(clearance, dist - 5.0, abs_tol=1e-9)

    def test_non_origin_center(self) -> None:
        clearance = point_to_sphere_clearance((30.0, 0.0, 0.0), (20.0, 0.0, 0.0), 5.0)
        assert math.isclose(clearance, 5.0, abs_tol=1e-9)


# ===========================================================================
# Capsule-to-Sphere Clearance
# ===========================================================================

class TestCapsuleToSphereClearance:
    """Tests for capsule_to_sphere_clearance()."""

    def test_capsule_far_from_sphere(self) -> None:
        # Capsule tip at origin, along +Z, sphere at (100, 0, 0)
        clearance = capsule_to_sphere_clearance(
            tip_position=(0.0, 0.0, 0.0),
            shaft_direction=(0.0, 0.0, 1.0),
            shaft_length=50.0,
            shaft_radius=1.0,
            sphere_center=(100.0, 0.0, 0.0),
            sphere_radius=10.0,
        )
        # Closest point on segment to sphere center is (0,0,0)
        # Distance = 100, clearance = 100 - 10 - 1 = 89
        assert math.isclose(clearance, 89.0, abs_tol=1e-9)

    def test_capsule_tip_touching_sphere(self) -> None:
        # Sphere at (11, 0, 0) with radius 10, capsule radius 1
        clearance = capsule_to_sphere_clearance(
            tip_position=(0.0, 0.0, 0.0),
            shaft_direction=(0.0, 0.0, 1.0),
            shaft_length=50.0,
            shaft_radius=1.0,
            sphere_center=(11.0, 0.0, 0.0),
            sphere_radius=10.0,
        )
        assert math.isclose(clearance, 0.0, abs_tol=1e-9)

    def test_capsule_inside_sphere(self) -> None:
        # Sphere at (5, 0, 0) with radius 10, capsule radius 1
        clearance = capsule_to_sphere_clearance(
            tip_position=(0.0, 0.0, 0.0),
            shaft_direction=(0.0, 0.0, 1.0),
            shaft_length=50.0,
            shaft_radius=1.0,
            sphere_center=(5.0, 0.0, 0.0),
            sphere_radius=10.0,
        )
        # Distance = 5, clearance = 5 - 10 - 1 = -6
        assert clearance < 0.0

    def test_sphere_closest_to_mid_shaft(self) -> None:
        # Shaft from (0,0,0) along +Z for 100mm; sphere at (0, 20, 50)
        clearance = capsule_to_sphere_clearance(
            tip_position=(0.0, 0.0, 0.0),
            shaft_direction=(0.0, 0.0, 1.0),
            shaft_length=100.0,
            shaft_radius=2.0,
            sphere_center=(0.0, 20.0, 50.0),
            sphere_radius=5.0,
        )
        # Closest point on segment to (0,20,50) is (0,0,50)
        # Distance = 20, clearance = 20 - 5 - 2 = 13
        assert math.isclose(clearance, 13.0, abs_tol=1e-9)

    def test_sphere_beyond_shaft_end(self) -> None:
        # Shaft from (0,0,0) along +Z for 50mm; sphere at (0, 0, 100) radius 10
        clearance = capsule_to_sphere_clearance(
            tip_position=(0.0, 0.0, 0.0),
            shaft_direction=(0.0, 0.0, 1.0),
            shaft_length=50.0,
            shaft_radius=1.0,
            sphere_center=(0.0, 0.0, 100.0),
            sphere_radius=10.0,
        )
        # Closest point = (0,0,50), distance = 50, clearance = 50 - 10 - 1 = 39
        assert math.isclose(clearance, 39.0, abs_tol=1e-9)

    def test_sphere_before_shaft_start(self) -> None:
        # Shaft from (0,0,50) along +Z for 50mm; sphere at (0, 0, 0) radius 10
        clearance = capsule_to_sphere_clearance(
            tip_position=(0.0, 0.0, 50.0),
            shaft_direction=(0.0, 0.0, 1.0),
            shaft_length=50.0,
            shaft_radius=1.0,
            sphere_center=(0.0, 0.0, 0.0),
            sphere_radius=10.0,
        )
        # Closest point = (0,0,50), distance = 50, clearance = 50 - 10 - 1 = 39
        assert math.isclose(clearance, 39.0, abs_tol=1e-9)

    def test_capsule_zero_radius(self) -> None:
        """Edge case: capsule with zero shaft radius = line segment."""
        # Not possible due to MIN_SHAFT_RADIUS validation, but geometry handles it
        clearance = capsule_to_sphere_clearance(
            tip_position=(0.0, 0.0, 0.0),
            shaft_direction=(0.0, 0.0, 1.0),
            shaft_length=50.0,
            shaft_radius=0.0,
            sphere_center=(20.0, 0.0, 0.0),
            sphere_radius=10.0,
        )
        assert math.isclose(clearance, 10.0, abs_tol=1e-9)


# ===========================================================================
# Effective Radius Computation
# ===========================================================================

class TestComputeEffectiveRadius:
    """Tests for compute_effective_radius()."""

    def test_basic_computation(self) -> None:
        # R_eff = 10 + 2 + 1 + 2*0.5 = 14.0
        result = compute_effective_radius(10.0, 2.0, 1.0, 0.5)
        assert math.isclose(result, 14.0, abs_tol=1e-9)

    def test_zero_margins(self) -> None:
        result = compute_effective_radius(10.0, 0.0, 0.0, 0.0)
        assert math.isclose(result, 10.0, abs_tol=1e-9)

    def test_all_zero(self) -> None:
        result = compute_effective_radius(0.0, 0.0, 0.0, 0.0)
        assert math.isclose(result, 0.0, abs_tol=1e-9)

    def test_large_tracking_uncertainty(self) -> None:
        # R_eff = 10 + 0 + 0 + 2*10 = 30.0
        result = compute_effective_radius(10.0, 0.0, 0.0, 10.0)
        assert math.isclose(result, 30.0, abs_tol=1e-9)

    def test_negative_radius_rejected(self) -> None:
        with pytest.raises(ProximityValidationError, match="non-negative"):
            compute_effective_radius(-1.0, 0.0, 0.0, 0.0)

    def test_negative_margin_rejected(self) -> None:
        with pytest.raises(ProximityValidationError, match="non-negative"):
            compute_effective_radius(10.0, -1.0, 0.0, 0.0)

    def test_negative_reg_error_rejected(self) -> None:
        with pytest.raises(ProximityValidationError, match="non-negative"):
            compute_effective_radius(10.0, 0.0, -1.0, 0.0)

    def test_negative_uncertainty_rejected(self) -> None:
        with pytest.raises(ProximityValidationError, match="non-negative"):
            compute_effective_radius(10.0, 0.0, 0.0, -1.0)

    def test_nan_rejected(self) -> None:
        with pytest.raises(ProximityValidationError, match="non-negative"):
            compute_effective_radius(float("nan"), 0.0, 0.0, 0.0)

    def test_inf_rejected(self) -> None:
        with pytest.raises(ProximityValidationError, match="non-negative"):
            compute_effective_radius(float("inf"), 0.0, 0.0, 0.0)


# ===========================================================================
# Unit Conversion
# ===========================================================================

class TestUnitConversion:
    """Tests for mm-to-meter conversion functions."""

    def test_scalar_conversion(self) -> None:
        assert math.isclose(convert_mm_to_meters(1000.0), 1.0, abs_tol=1e-9)
        assert math.isclose(convert_mm_to_meters(0.0), 0.0, abs_tol=1e-9)
        assert math.isclose(convert_mm_to_meters(1.0), 0.001, abs_tol=1e-9)

    def test_point_conversion(self) -> None:
        result = convert_point_mm_to_meters((1000.0, 2000.0, 500.0))
        assert math.isclose(result[0], 1.0, abs_tol=1e-9)
        assert math.isclose(result[1], 2.0, abs_tol=1e-9)
        assert math.isclose(result[2], 0.5, abs_tol=1e-9)
