# -*- coding: utf-8 -*-
"""Unit tests for Bounded Constraint Solver (D222)."""

from __future__ import annotations

import pytest

from holomed.anatomy.exceptions import AnatomyValidationError
from holomed.anatomy.models import (
    MAX_DISPLACEMENT_METERS,
    MAX_SOLVER_ITERATIONS,
    ConvergenceStatus,
    Vector3D,
)
from holomed.anatomy.solver import AnatomicalConstraintSolver


def test_d222_solver_convergence() -> None:
    """Verify solver converges within MAX_SOLVER_ITERATIONS (16) on reasonable input."""
    target = Vector3D(0.04, 0.03, 0.0)
    solved, status, iters = AnatomicalConstraintSolver.solve_displacement(
        target_displacement=target,
        stiffness_kpa=10.0,
    )
    assert status == ConvergenceStatus.CONVERGED
    assert iters <= MAX_SOLVER_ITERATIONS
    assert abs(solved.dx - 0.04) < 1e-3
    assert abs(solved.dy - 0.03) < 1e-3


def test_d222_solver_degenerate_and_divergent_inputs() -> None:
    """Verify safe termination on degenerate or non-finite inputs."""
    # Zero / negative stiffness -> DEGENERATE
    solved, status, iters = AnatomicalConstraintSolver.solve_displacement(
        target_displacement=Vector3D(0.01, 0.0, 0.0),
        stiffness_kpa=-5.0,
    )
    assert status == ConvergenceStatus.DEGENERATE
    assert solved == Vector3D(0.0, 0.0, 0.0)

    # Non-finite displacement rejected by Vector3D constructor
    with pytest.raises(AnatomyValidationError):
        Vector3D(float("nan"), 0.0, 0.0)

    # Bypass constructor to verify solver defense in depth
    non_finite_target = object.__new__(Vector3D)
    object.__setattr__(non_finite_target, "dx", float("nan"))
    object.__setattr__(non_finite_target, "dy", 0.0)
    object.__setattr__(non_finite_target, "dz", 0.0)
    solved, status, iters = AnatomicalConstraintSolver.solve_displacement(
        target_displacement=non_finite_target,
        stiffness_kpa=10.0,
    )
    assert status == ConvergenceStatus.DIVERGED
    assert solved == Vector3D(0.0, 0.0, 0.0)


def test_d222_solver_displacement_saturation() -> None:
    """Verify solver saturates displacement within MAX_DISPLACEMENT_METERS (0.10m)."""
    large_target = Vector3D(1.0, 1.0, 1.0)
    solved, status, iters = AnatomicalConstraintSolver.solve_displacement(
        target_displacement=large_target,
        stiffness_kpa=0.1,  # Low stiffness allows maximum deflection
        iterations_limit=16,
    )
    assert solved.dx <= MAX_DISPLACEMENT_METERS
    assert solved.dy <= MAX_DISPLACEMENT_METERS
    assert solved.dz <= MAX_DISPLACEMENT_METERS
