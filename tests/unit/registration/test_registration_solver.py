# -*- coding: utf-8 -*-
"""Unit Tests for Horn Rigid Registration Solver and Cyclic Jacobi Eigensolver."""

from __future__ import annotations

import math
import pytest

from holomed.registration.exceptions import (
    RegistrationAccuracyError,
    RegistrationDegeneracyError,
)
from holomed.registration.models import (
    FiducialCloud,
    FiducialPointPair,
    RigidRegistrationTransform3D,
)
from holomed.registration.solver import HornRigidRegistrationSolver


def test_horns_method_identity_transform() -> None:
    """Verify solver recovers identity transform when points are identical."""
    pairs = (
        FiducialPointPair("f1", (0.0, 0.0, 0.0), (0.0, 0.0, 0.0), "p1"),
        FiducialPointPair("f2", (100.0, 0.0, 0.0), (100.0, 0.0, 0.0), "p2"),
        FiducialPointPair("f3", (0.0, 100.0, 0.0), (0.0, 100.0, 0.0), "p3"),
        FiducialPointPair("f4", (0.0, 0.0, 100.0), (0.0, 0.0, 100.0), "p4"),
    )
    cloud = FiducialCloud(pairs=pairs)
    t = HornRigidRegistrationSolver.solve(cloud)

    # Translation should be approx (0, 0, 0)
    for v in t.translation_vector_mm:
        assert pytest.approx(v, abs=1e-6) == 0.0

    # Rotation should be identity
    for i in range(3):
        for j in range(3):
            expected = 1.0 if i == j else 0.0
            assert pytest.approx(t.rotation_matrix[i][j], abs=1e-6) == expected


def test_horns_method_pure_translation() -> None:
    """Verify solver recovers pure translation: +15 X, -25 Y, +50 Z."""
    tx, ty, tz = 15.0, -25.0, 50.0
    pts_plan = [
        (0.0, 0.0, 0.0),
        (50.0, 0.0, 0.0),
        (0.0, 50.0, 0.0),
        (0.0, 0.0, 50.0),
    ]
    pairs = tuple(
        FiducialPointPair(f"f{i}", p, (p[0] + tx, p[1] + ty, p[2] + tz), f"p{i}")
        for i, p in enumerate(pts_plan)
    )
    cloud = FiducialCloud(pairs=pairs)
    t = HornRigidRegistrationSolver.solve(cloud)

    assert pytest.approx(t.translation_vector_mm[0], abs=1e-5) == tx
    assert pytest.approx(t.translation_vector_mm[1], abs=1e-5) == ty
    assert pytest.approx(t.translation_vector_mm[2], abs=1e-5) == tz


def test_horns_method_pure_rotation_90deg_z() -> None:
    """Verify solver recovers 90-deg rotation around Z: (x, y, z) -> (-y, x, z)."""
    pts_plan = [
        (0.0, 0.0, 0.0),
        (100.0, 0.0, 0.0),
        (0.0, 100.0, 0.0),
        (50.0, 50.0, 50.0),
    ]
    pairs = tuple(
        FiducialPointPair(f"f{i}", p, (-p[1], p[0], p[2]), f"p{i}")
        for i, p in enumerate(pts_plan)
    )
    cloud = FiducialCloud(pairs=pairs)
    t = HornRigidRegistrationSolver.solve(cloud)

    # Expected R: [[0, -1, 0], [1, 0, 0], [0, 0, 1]]
    assert pytest.approx(t.rotation_matrix[0][1], abs=1e-5) == -1.0
    assert pytest.approx(t.rotation_matrix[1][0], abs=1e-5) == 1.0
    assert pytest.approx(t.rotation_matrix[2][2], abs=1e-5) == 1.0


def test_horns_method_general_6dof() -> None:
    """Verify solver recovers arbitrary 6-DOF rotation and translation."""
    # Rotate 45 deg around X, translate (10, 20, 30)
    theta = math.pi / 4.0
    cos_t = math.cos(theta)
    sin_t = math.sin(theta)

    pts_plan = [
        (10.0, 10.0, 10.0),
        (80.0, 20.0, 15.0),
        (15.0, 90.0, 25.0),
        (25.0, 35.0, 110.0),
    ]
    pairs = []
    for i, (x, y, z) in enumerate(pts_plan):
        # Rotate around X: x' = x, y' = y*cos - z*sin, z' = y*sin + z*cos
        yr = y * cos_t - z * sin_t
        zr = y * sin_t + z * cos_t
        pairs.append(
            FiducialPointPair(f"f{i}", (x, y, z), (x + 10.0, yr + 20.0, zr + 30.0), f"p{i}")
        )

    cloud = FiducialCloud(pairs=tuple(pairs))
    t = HornRigidRegistrationSolver.solve(cloud)

    assert pytest.approx(t.translation_vector_mm[0], abs=1e-4) == 10.0
    assert pytest.approx(t.translation_vector_mm[1], abs=1e-4) == 20.0
    assert pytest.approx(t.translation_vector_mm[2], abs=1e-4) == 30.0


def test_jacobi_eigensolver_non_convergence_fails_closed() -> None:
    """Verify cyclic Jacobi eigensolver fails closed if sweeps exceed bound without convergence."""
    # Matrix with non-zero off-diagonals called with max_sweeps=0 -> raises RegistrationAccuracyError
    A = [[2.0, 1.0, 1.0, 1.0], [1.0, 3.0, 1.0, 1.0], [1.0, 1.0, 4.0, 1.0], [1.0, 1.0, 1.0, 5.0]]
    with pytest.raises(RegistrationAccuracyError) as exc:
        HornRigidRegistrationSolver._solve_jacobi_4x4(A, max_sweeps=0)
    assert "failed to converge" in str(exc.value)
