# -*- coding: utf-8 -*-
"""Deterministic Derivation of M10 Anatomical Checkpoints from Surgical Plans."""

from __future__ import annotations

from typing import Tuple

from holomed.planning.models import SurgicalPlanDefinition
from holomed.workflow.models import AnatomicalCheckpoint


def derive_checkpoints_from_plan(plan: SurgicalPlanDefinition) -> Tuple[AnatomicalCheckpoint, ...]:
    """Deterministically derive frozen M10 AnatomicalCheckpoint objects from plan targets (D296)."""
    checkpoints: list[AnatomicalCheckpoint] = []

    # 1. Generate checkpoints for planned trajectories
    for traj in plan.trajectories:
        chk_id = f"chk_traj_{traj.trajectory_id}"
        chk = AnatomicalCheckpoint(
            checkpoint_id=chk_id,
            entity_id=traj.target_structure,
            expected_relation="trajectory_aligned",
            max_tolerance_mm=traj.max_lateral_deviation_mm,
            min_confidence=traj.min_confidence,
            max_uncertainty=traj.max_uncertainty,
        )
        checkpoints.append(chk)

    # 2. Generate checkpoints for safety exclusion zones
    for zone in plan.exclusion_zones:
        chk_id = f"chk_zone_{zone.zone_id}"
        chk = AnatomicalCheckpoint(
            checkpoint_id=chk_id,
            entity_id=zone.anatomical_structure,
            expected_relation="outside_exclusion_zone",
            max_tolerance_mm=zone.min_clearance_mm,
            min_confidence=0.90,
            max_uncertainty=0.10,
        )
        checkpoints.append(chk)

    return tuple(checkpoints)
