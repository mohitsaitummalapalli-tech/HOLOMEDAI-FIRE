# -*- coding: utf-8 -*-
"""HoloMed AI Preoperative Surgical Planning & Patient Case Foundation (M12)."""

from __future__ import annotations

from holomed.planning.checkpoints import derive_checkpoints_from_plan
from holomed.planning.exceptions import (
    PlanningAuthorizationError,
    PlanningCapacityError,
    PlanningEpochMismatchError,
    PlanningError,
    PlanningLifecycleError,
    PlanningLockError,
    PlanningShutdownError,
    PlanningValidationError,
    PlanningVerificationError,
)
from holomed.planning.geometry import (
    compute_trajectory_unit_vector,
    euclidean_distance_3d,
    point_to_line_segment_distance,
)
from holomed.planning.models import (
    CASE_ID_REGEX,
    MAX_ACTIVE_PLANS,
    MAX_CHECKPOINTS_PER_PLAN,
    MAX_CLEARANCE_TOLERANCE_MM,
    MAX_EXCLUSION_ZONES_PER_PLAN,
    MAX_TRAJECTORIES_PER_PLAN,
    MAX_TRAJECTORY_LENGTH_MM,
    MIN_CLEARANCE_TOLERANCE_MM,
    MIN_TRAJECTORY_LENGTH_MM,
    PATIENT_HASH_REGEX,
    PLAN_ID_REGEX,
    PatientCaseContext,
    PlanVerificationRecord,
    SafetyExclusionZone,
    SurgicalLaterality,
    SurgicalPlanDefinition,
    TrajectoryPlan,
)
from holomed.planning.service import PlanningService
from holomed.planning.verification import PlanVerificationEngine

__all__ = [
    # Exceptions
    "PlanningError",
    "PlanningLifecycleError",
    "PlanningValidationError",
    "PlanningCapacityError",
    "PlanningLockError",
    "PlanningVerificationError",
    "PlanningEpochMismatchError",
    "PlanningAuthorizationError",
    "PlanningShutdownError",
    # Enums
    "SurgicalLaterality",
    # Models
    "PatientCaseContext",
    "TrajectoryPlan",
    "SafetyExclusionZone",
    "SurgicalPlanDefinition",
    "PlanVerificationRecord",
    # Constants
    "MAX_ACTIVE_PLANS",
    "MAX_TRAJECTORIES_PER_PLAN",
    "MAX_EXCLUSION_ZONES_PER_PLAN",
    "MAX_CHECKPOINTS_PER_PLAN",
    "MIN_TRAJECTORY_LENGTH_MM",
    "MAX_TRAJECTORY_LENGTH_MM",
    "MIN_CLEARANCE_TOLERANCE_MM",
    "MAX_CLEARANCE_TOLERANCE_MM",
    "PLAN_ID_REGEX",
    "CASE_ID_REGEX",
    "PATIENT_HASH_REGEX",
    # Geometry
    "euclidean_distance_3d",
    "compute_trajectory_unit_vector",
    "point_to_line_segment_distance",
    # Components
    "derive_checkpoints_from_plan",
    "PlanVerificationEngine",
    "PlanningService",
]
