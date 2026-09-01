# -*- coding: utf-8 -*-
"""Unit tests for Discrete Fixed-Timestep Simulation Engine (D221)."""

from __future__ import annotations

import pytest

from holomed.anatomy.exceptions import (
    AnatomyCapacityError,
    AnatomyValidationError,
)
from holomed.anatomy.models import (
    FIXED_SIMULATION_TIMESTEP_S,
    Vector3D,
)
from holomed.anatomy.simulation import AnatomicalSimulationEngine


def test_d221_discrete_timestep_determinism() -> None:
    """Verify D221 discrete timestepping advances without wall-clock dependence."""
    sim1 = AnatomicalSimulationEngine(epoch_id=1)
    sim2 = AnatomicalSimulationEngine(epoch_id=1)

    sim1.register_entity_tissue("anat.liver", Vector3D(0.01, 0.0, 0.0))
    sim2.register_entity_tissue("anat.liver", Vector3D(0.01, 0.0, 0.0))

    # Step 10 steps (10 * 0.01 = 0.10s)
    state1 = sim1.step(count=10)
    state2 = sim2.step(count=10)

    assert state1.simulation_time_s == 0.10
    assert state2.simulation_time_s == 0.10
    assert state1.step_count == 10
    assert state2.step_count == 10

    snap1 = state1.tissue_states["anat.liver"]
    snap2 = state2.tissue_states["anat.liver"]
    assert snap1 == snap2
    assert snap1.is_simulated is True


def test_simulation_step_count_capacity_limit() -> None:
    """Verify step count > 100 raises AnatomyCapacityError."""
    sim = AnatomicalSimulationEngine(epoch_id=1)

    with pytest.raises(AnatomyCapacityError):
        sim.step(count=101)

    with pytest.raises(AnatomyValidationError):
        sim.step(count=0)


def test_simulation_apply_displacement_constraint() -> None:
    """Verify applying displacement constraint updates tissue state."""
    sim = AnatomicalSimulationEngine(epoch_id=1)
    snap = sim.apply_displacement_constraint(
        entity_id="anat.kidney",
        target_displacement=Vector3D(0.05, 0.0, 0.0),
        stiffness_kpa=20.0,
    )
    assert snap.is_deformed is True
    assert snap.displacement.dx > 0.0
    assert snap.stress_kpa > 0.0
