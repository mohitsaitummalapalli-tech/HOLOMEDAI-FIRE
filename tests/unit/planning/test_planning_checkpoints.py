# -*- coding: utf-8 -*-
"""Unit Tests for Deterministic M10 Checkpoint Derivation."""

from __future__ import annotations

from holomed.planning.checkpoints import derive_checkpoints_from_plan
from holomed.planning.models import SurgicalPlanDefinition
from holomed.workflow.models import AnatomicalCheckpoint


def test_plan_derived_checkpoints_generation(sample_plan: SurgicalPlanDefinition) -> None:
    """Verify checkpoints are derived for both trajectories and exclusion zones (D296)."""
    checkpoints = derive_checkpoints_from_plan(sample_plan)
    assert len(checkpoints) == 2

    traj_chk = checkpoints[0]
    assert isinstance(traj_chk, AnatomicalCheckpoint)
    assert traj_chk.checkpoint_id == "chk_traj_traj_cup_reaming"
    assert traj_chk.entity_id == "acetabular_fossa"
    assert traj_chk.expected_relation == "trajectory_aligned"
    assert traj_chk.max_tolerance_mm == 2.0
    assert traj_chk.min_confidence == 0.85
    assert traj_chk.max_uncertainty == 0.15

    zone_chk = checkpoints[1]
    assert isinstance(zone_chk, AnatomicalCheckpoint)
    assert zone_chk.checkpoint_id == "chk_zone_zone_sciatic_nerve"
    assert zone_chk.entity_id == "sciatic_nerve_trunk"
    assert zone_chk.expected_relation == "outside_exclusion_zone"
    assert zone_chk.max_tolerance_mm == 5.0
