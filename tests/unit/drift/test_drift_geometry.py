# -*- coding: utf-8 -*-
"""Unit Tests for M16 Drift Geometry — Euclidean Residual, Tolerance & Dwell Stability."""

from __future__ import annotations

import math

import pytest

from holomed.drift.exceptions import DriftValidationError
from holomed.drift.geometry import (
    compute_dwell_stability,
    compute_effective_tolerance,
    compute_euclidean_residual,
    convert_point_mm_to_meters,
)


# ===========================================================================
# Euclidean Residual
# ===========================================================================

class TestEuclideanResidual:
    """Tests for compute_euclidean_residual()."""

    def test_identical_points_zero_residual(self) -> None:
        r = compute_euclidean_residual((10.0, 20.0, 30.0), (10.0, 20.0, 30.0))
        assert math.isclose(r, 0.0, abs_tol=1e-9)

    def test_1d_displacement(self) -> None:
        r = compute_euclidean_residual((10.0, 0.0, 0.0), (12.5, 0.0, 0.0))
        assert math.isclose(r, 2.5, abs_tol=1e-9)

    def test_3d_diagonal_displacement(self) -> None:
        # 3-4-5 triangle in 3D: dx=3, dy=4, dz=0 -> dist=5
        r = compute_euclidean_residual((0.0, 0.0, 0.0), (3.0, 4.0, 0.0))
        assert math.isclose(r, 5.0, abs_tol=1e-9)


# ===========================================================================
# Effective Tolerance
# ===========================================================================

class TestEffectiveTolerance:
    """Tests for compute_effective_tolerance()."""

    def test_landmark_tighter_than_global(self) -> None:
        # landmark=1.5, global=2.0 -> effective=1.5
        eff = compute_effective_tolerance(1.5, 2.0)
        assert math.isclose(eff, 1.5, abs_tol=1e-9)

    def test_landmark_equal_to_global(self) -> None:
        eff = compute_effective_tolerance(2.0, 2.0)
        assert math.isclose(eff, 2.0, abs_tol=1e-9)

    def test_landmark_larger_clamped_to_global(self) -> None:
        eff = compute_effective_tolerance(3.0, 2.0)
        assert math.isclose(eff, 2.0, abs_tol=1e-9)

    def test_negative_tolerance_rejected(self) -> None:
        with pytest.raises(DriftValidationError):
            compute_effective_tolerance(-1.0, 2.0)


# ===========================================================================
# Dwell Stability (Jitter)
# ===========================================================================

class TestDwellStability:
    """Tests for compute_dwell_stability()."""

    def test_empty_samples(self) -> None:
        assert compute_dwell_stability([]) == 0.0

    def test_identical_samples_zero_jitter(self) -> None:
        samples = [(10.0, 0.0, 0.0), (10.0, 0.0, 0.0), (10.0, 0.0, 0.0)]
        assert math.isclose(compute_dwell_stability(samples), 0.0, abs_tol=1e-9)

    def test_jitter_dispersion_from_centroid(self) -> None:
        # Points at (9.8, 0, 0), (10.0, 0, 0), (10.2, 0, 0)
        # Centroid = (10.0, 0, 0), max distance = 0.2
        samples = [(9.8, 0.0, 0.0), (10.0, 0.0, 0.0), (10.2, 0.0, 0.0)]
        jitter = compute_dwell_stability(samples)
        assert math.isclose(jitter, 0.2, abs_tol=1e-9)


# ===========================================================================
# Presentation Unit Conversion
# ===========================================================================

class TestUnitConversion:
    """Tests for convert_point_mm_to_meters()."""

    def test_point_conversion(self) -> None:
        pt_m = convert_point_mm_to_meters((1000.0, 2000.0, 500.0))
        assert math.isclose(pt_m[0], 1.0, abs_tol=1e-9)
        assert math.isclose(pt_m[1], 2.0, abs_tol=1e-9)
        assert math.isclose(pt_m[2], 0.5, abs_tol=1e-9)
