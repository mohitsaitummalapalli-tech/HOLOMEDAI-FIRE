# -*- coding: utf-8 -*-
"""Discrete Fixed-Timestep Anatomical Simulation Engine for M05."""

from __future__ import annotations

import math
from typing import Mapping, Optional

from holomed.anatomy.exceptions import (
    AnatomyCapacityError,
    AnatomyEpochMismatchError,
    AnatomyValidationError,
)
from holomed.anatomy.models import (
    FIXED_SIMULATION_TIMESTEP_S,
    MAX_DISPLACEMENT_METERS,
    MAX_SIMULATION_STEPS_PER_CYCLE,
    ConvergenceStatus,
    Point3D,
    SimulationState,
    TissueStateSnapshot,
    Vector3D,
)
from holomed.anatomy.solver import AnatomicalConstraintSolver


class AnatomicalSimulationEngine:
    """Synchronous, discrete time-stepped physiological and tissue simulation engine."""

    def __init__(self, epoch_id: int = 0) -> None:
        self._epoch_id = epoch_id
        self._simulation_time_s: float = 0.0
        self._step_count: int = 0
        self._tissue_states: dict[str, TissueStateSnapshot] = {}

    @property
    def simulation_time_s(self) -> float:
        return self._simulation_time_s

    @property
    def step_count(self) -> int:
        return self._step_count

    @property
    def state(self) -> SimulationState:
        """Capture immutable simulation state snapshot."""
        return SimulationState(
            epoch_id=self._epoch_id,
            simulation_time_s=self._simulation_time_s,
            step_count=self._step_count,
            tissue_states=self._tissue_states,
            active_constraints_count=len(self._tissue_states),
            convergence_status=ConvergenceStatus.CONVERGED,
            is_simulated=True,
        )

    def register_entity_tissue(
        self,
        entity_id: str,
        initial_displacement: Optional[Vector3D] = None,
        initial_temperature_c: float = 37.0,
    ) -> None:
        """Initialize tissue state tracking for an anatomical entity."""
        disp = initial_displacement or Vector3D(0.0, 0.0, 0.0)
        self._tissue_states[entity_id] = TissueStateSnapshot(
            entity_id=entity_id,
            simulation_time_s=self._simulation_time_s,
            displacement=disp,
            stress_kpa=0.0,
            strain=0.0,
            temperature_c=initial_temperature_c,
            is_deformed=disp.magnitude > 1e-6,
            is_simulated=True,
        )

    def apply_displacement_constraint(
        self,
        entity_id: str,
        target_displacement: Vector3D,
        stiffness_kpa: float = 10.0,
    ) -> TissueStateSnapshot:
        """Apply displacement constraint to tissue state via bounded constraint solver."""
        if entity_id not in self._tissue_states:
            self.register_entity_tissue(entity_id)

        solved_disp, status, _ = AnatomicalConstraintSolver.solve_displacement(
            target_displacement=target_displacement,
            stiffness_kpa=stiffness_kpa,
        )

        mag = solved_disp.magnitude
        strain = round(mag / (mag + 0.1), 6)
        stress_kpa = round(stiffness_kpa * strain, 4)

        prev = self._tissue_states[entity_id]
        snapshot = TissueStateSnapshot(
            entity_id=entity_id,
            simulation_time_s=self._simulation_time_s,
            displacement=solved_disp,
            stress_kpa=stress_kpa,
            strain=strain,
            temperature_c=prev.temperature_c,
            is_deformed=mag > 1e-6,
            is_simulated=True,
        )
        self._tissue_states[entity_id] = snapshot
        return snapshot

    def step(self, count: int = 1) -> SimulationState:
        """Advance discrete simulation time by count * FIXED_SIMULATION_TIMESTEP_S without wall-clock dependence."""
        if count <= 0:
            raise AnatomyValidationError(f"Step count must be positive integer, got {count}")
        if count > MAX_SIMULATION_STEPS_PER_CYCLE:
            raise AnatomyCapacityError(
                f"Step count ({count}) exceeds maximum allowed per cycle ({MAX_SIMULATION_STEPS_PER_CYCLE})"
            )

        # Advance discrete time deterministically
        dt = float(count) * FIXED_SIMULATION_TIMESTEP_S
        self._simulation_time_s = round(self._simulation_time_s + dt, 4)
        self._step_count += count

        # Natural relaxation of strains over time
        for e_id, snap in list(self._tissue_states.items()):
            if snap.is_deformed:
                decay = math.exp(-0.1 * dt)
                new_disp = Vector3D(
                    snap.displacement.dx * decay,
                    snap.displacement.dy * decay,
                    snap.displacement.dz * decay,
                )
                mag = new_disp.magnitude
                new_strain = round(snap.strain * decay, 6)
                new_stress = round(snap.stress_kpa * decay, 4)

                self._tissue_states[e_id] = TissueStateSnapshot(
                    entity_id=e_id,
                    simulation_time_s=self._simulation_time_s,
                    displacement=new_disp,
                    stress_kpa=new_stress,
                    strain=new_strain,
                    temperature_c=snap.temperature_c,
                    is_deformed=mag > 1e-6,
                    is_simulated=True,
                )

        return self.state

    def reset(self, epoch_id: int) -> None:
        """Reset simulation state and epoch."""
        self._epoch_id = epoch_id
        self._simulation_time_s = 0.0
        self._step_count = 0
        self._tissue_states.clear()
