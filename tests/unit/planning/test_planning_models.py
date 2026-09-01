# -*- coding: utf-8 -*-
"""Unit Tests for Planning Models, Geometric Bounds, and Capacity Validation."""

from __future__ import annotations

import pytest

from holomed.planning.exceptions import PlanningValidationError
from holomed.planning.models import (
    MAX_EXCLUSION_ZONES_PER_PLAN,
    MAX_TRAJECTORIES_PER_PLAN,
    PatientCaseContext,
    SafetyExclusionZone,
    SurgicalLaterality,
    SurgicalPlanDefinition,
    TrajectoryPlan,
)
from holomed.workflow.models import InterlockSeverity


def test_surgical_laterality_enum_integrity() -> None:
    """Verify all 5 authoritative laterality values exist."""
    expected = {"LEFT", "RIGHT", "BILATERAL", "MIDLINE", "NOT_APPLICABLE"}
    actual = {l.value for l in SurgicalLaterality}
    assert actual == expected


def test_patient_case_context_validation(sample_case_context: PatientCaseContext) -> None:
    """Verify PatientCaseContext validates case_id, hash, and laterality."""
    assert sample_case_context.case_id == "case_tha_001"
    assert sample_case_context.laterality == SurgicalLaterality.RIGHT

    # Invalid patient hash (must be 64-char hex)
    with pytest.raises(PlanningValidationError):
        PatientCaseContext(
            case_id="case_01",
            patient_hash="not_a_valid_64_hex_hash",
            procedure_code="THA",
            laterality=SurgicalLaterality.LEFT,
            primary_surgeon_id="dr_smith",
            scheduled_date_utc="2026-09-01T10:00:00Z",
        )

    # Invalid case_id (contains slash)
    with pytest.raises(PlanningValidationError):
        PatientCaseContext(
            case_id="case/with/slashes",
            patient_hash="a" * 64,
            procedure_code="THA",
            laterality=SurgicalLaterality.LEFT,
            primary_surgeon_id="dr_smith",
            scheduled_date_utc="2026-09-01T10:00:00Z",
        )


def test_trajectory_plan_validation() -> None:
    """Verify TrajectoryPlan validates non-coincident points, length, and tolerances."""
    traj = TrajectoryPlan(
        trajectory_id="traj_01",
        target_structure="femur",
        entry_point_mm=(0.0, 0.0, 0.0),
        target_point_mm=(0.0, 0.0, 50.0),
        max_lateral_deviation_mm=2.0,
        max_angular_deviation_deg=3.0,
        min_confidence=0.8,
        max_uncertainty=0.2,
    )
    assert traj.trajectory_id == "traj_01"

    # Coincident entry and target points (length = 0)
    with pytest.raises(PlanningValidationError) as exc_info:
        TrajectoryPlan(
            trajectory_id="traj_bad",
            target_structure="femur",
            entry_point_mm=(10.0, 10.0, 10.0),
            target_point_mm=(10.0, 10.0, 10.0),
            max_lateral_deviation_mm=2.0,
            max_angular_deviation_deg=3.0,
            min_confidence=0.8,
            max_uncertainty=0.2,
        )
    assert "less than minimum" in str(exc_info.value)

    # Sub-minimum length (< 5.0 mm)
    with pytest.raises(PlanningValidationError):
        TrajectoryPlan(
            trajectory_id="traj_short",
            target_structure="femur",
            entry_point_mm=(0.0, 0.0, 0.0),
            target_point_mm=(0.0, 0.0, 4.0),
            max_lateral_deviation_mm=2.0,
            max_angular_deviation_deg=3.0,
            min_confidence=0.8,
            max_uncertainty=0.2,
        )

    # Negative lateral deviation tolerance
    with pytest.raises(PlanningValidationError):
        TrajectoryPlan(
            trajectory_id="traj_neg",
            target_structure="femur",
            entry_point_mm=(0.0, 0.0, 0.0),
            target_point_mm=(0.0, 0.0, 50.0),
            max_lateral_deviation_mm=-1.0,
            max_angular_deviation_deg=3.0,
            min_confidence=0.8,
            max_uncertainty=0.2,
        )


def test_safety_exclusion_zone_validation() -> None:
    """Verify SafetyExclusionZone validates radius and clearance bounds."""
    zone = SafetyExclusionZone(
        zone_id="zone_nerve",
        anatomical_structure="nerve",
        center_point_mm=(10.0, 20.0, 30.0),
        bounding_radius_mm=5.0,
        min_clearance_mm=3.0,
    )
    assert zone.zone_id == "zone_nerve"

    # Negative radius
    with pytest.raises(PlanningValidationError):
        SafetyExclusionZone(
            zone_id="zone_neg",
            anatomical_structure="nerve",
            center_point_mm=(0.0, 0.0, 0.0),
            bounding_radius_mm=-2.0,
            min_clearance_mm=3.0,
        )


def test_surgical_plan_capacity_limits(
    sample_case_context: PatientCaseContext,
    sample_trajectory: TrajectoryPlan,
    sample_exclusion_zone: SafetyExclusionZone,
) -> None:
    """Verify MAX_TRAJECTORIES_PER_PLAN = 8 and MAX_EXCLUSION_ZONES_PER_PLAN = 16 limits."""
    # Overflow trajectories (> 8)
    trajs = tuple(sample_trajectory for _ in range(MAX_TRAJECTORIES_PER_PLAN + 1))
    with pytest.raises(PlanningValidationError):
        SurgicalPlanDefinition(
            plan_id="plan_over_traj",
            version="1.0",
            case_context=sample_case_context,
            trajectories=trajs,
            exclusion_zones=(),
        )

    # Overflow exclusion zones (> 16)
    zones = tuple(sample_exclusion_zone for _ in range(MAX_EXCLUSION_ZONES_PER_PLAN + 1))
    with pytest.raises(PlanningValidationError):
        SurgicalPlanDefinition(
            plan_id="plan_over_zones",
            version="1.0",
            case_context=sample_case_context,
            trajectories=(sample_trajectory,),
            exclusion_zones=zones,
        )
