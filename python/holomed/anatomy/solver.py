# -*- coding: utf-8 -*-
"""Bounded Iterative Constraint Solver for M05 Anatomy."""

from __future__ import annotations

import math
from typing import Mapping, Sequence

from holomed.anatomy.models import (
    COORDINATE_EPSILON,
    MAX_DISPLACEMENT_METERS,
    MAX_SOLVER_ITERATIONS,
    SOLVER_CONVERGENCE_TOLERANCE,
    ConvergenceStatus,
    Vector3D,
)


class AnatomicalConstraintSolver:
    """Bounded, deterministic relaxation solver for anatomical displacement constraints."""

    @staticmethod
    def solve_displacement(
        target_displacement: Vector3D,
        stiffness_kpa: float = 10.0,
        iterations_limit: int = MAX_SOLVER_ITERATIONS,
    ) -> tuple[Vector3D, ConvergenceStatus, int]:
        """Solve bounded displacement relaxation under material stiffness.
        
        Returns:
            Tuple of (solved_displacement, convergence_status, iterations_used).
        """
        # Guard against degenerate stiffness
        if not math.isfinite(stiffness_kpa) or stiffness_kpa <= 0.0:
            return Vector3D(0.0, 0.0, 0.0), ConvergenceStatus.DEGENERATE, 0

        # Guard against non-finite input displacement
        if not (math.isfinite(target_displacement.dx) and math.isfinite(target_displacement.dy) and math.isfinite(target_displacement.dz)):
            return Vector3D(0.0, 0.0, 0.0), ConvergenceStatus.DIVERGED, 0

        curr_dx = 0.0
        curr_dy = 0.0
        curr_dz = 0.0

        # Relaxation factor: alpha = 1.0 / (1.0 + stiffness * 0.01)
        alpha = min(0.9, max(0.1, 1.0 / (1.0 + stiffness_kpa * 0.05)))
        target_dx = max(-MAX_DISPLACEMENT_METERS, min(MAX_DISPLACEMENT_METERS, target_displacement.dx))
        target_dy = max(-MAX_DISPLACEMENT_METERS, min(MAX_DISPLACEMENT_METERS, target_displacement.dy))
        target_dz = max(-MAX_DISPLACEMENT_METERS, min(MAX_DISPLACEMENT_METERS, target_displacement.dz))

        status = ConvergenceStatus.MAX_ITERATIONS_REACHED
        actual_iterations = 0

        for it in range(1, min(iterations_limit, MAX_SOLVER_ITERATIONS) + 1):
            actual_iterations = it
            diff_x = target_dx - curr_dx
            diff_y = target_dy - curr_dy
            diff_z = target_dz - curr_dz

            err = math.sqrt(diff_x * diff_x + diff_y * diff_y + diff_z * diff_z)
            if err <= SOLVER_CONVERGENCE_TOLERANCE:
                status = ConvergenceStatus.CONVERGED
                break

            curr_dx += alpha * diff_x
            curr_dy += alpha * diff_y
            curr_dz += alpha * diff_z

            # Clamp displacement at each iteration
            curr_dx = max(-MAX_DISPLACEMENT_METERS, min(MAX_DISPLACEMENT_METERS, curr_dx))
            curr_dy = max(-MAX_DISPLACEMENT_METERS, min(MAX_DISPLACEMENT_METERS, curr_dy))
            curr_dz = max(-MAX_DISPLACEMENT_METERS, min(MAX_DISPLACEMENT_METERS, curr_dz))

        solved = Vector3D(curr_dx, curr_dy, curr_dz)
        return solved, status, actual_iterations
