# -*- coding: utf-8 -*-
"""Strongly Typed Data Models and Validation Enums for M12 Surgical Planning."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import math
import re
from typing import Any, Mapping, Optional, Tuple

from holomed.planning.exceptions import PlanningValidationError
from holomed.workflow.models import InterlockSeverity

# Canonical Limits
MAX_ACTIVE_PLANS: int = 16
MAX_TRAJECTORIES_PER_PLAN: int = 8
MAX_EXCLUSION_ZONES_PER_PLAN: int = 16
MAX_CHECKPOINTS_PER_PLAN: int = 32
MIN_TRAJECTORY_LENGTH_MM: float = 5.0
MAX_TRAJECTORY_LENGTH_MM: float = 500.0
MIN_CLEARANCE_TOLERANCE_MM: float = 0.5
MAX_CLEARANCE_TOLERANCE_MM: float = 50.0

PLAN_ID_REGEX = re.compile(r"^[a-z0-9_-]{1,64}$")
CASE_ID_REGEX = re.compile(r"^[a-zA-Z0-9_-]{1,64}$")
PATIENT_HASH_REGEX = re.compile(r"^[0-9a-f]{64}$")
SEMVER_REGEX = re.compile(r"^(0|[1-9]\d*)\.(0|[1-9]\d*)(\.(0|[1-9]\d*))?$")


class SurgicalLaterality(str, Enum):
    """Authoritative surgical anatomical side specification."""

    LEFT = "LEFT"
    RIGHT = "RIGHT"
    BILATERAL = "BILATERAL"
    MIDLINE = "MIDLINE"
    NOT_APPLICABLE = "NOT_APPLICABLE"


@dataclass(frozen=True)
class PatientCaseContext:
    """Immutable patient case descriptor with pseudonymized identification."""

    case_id: str
    patient_hash: str
    procedure_code: str
    laterality: SurgicalLaterality
    primary_surgeon_id: str
    scheduled_date_utc: str

    def __post_init__(self) -> None:
        if not isinstance(self.case_id, str) or not CASE_ID_REGEX.match(self.case_id):
            raise PlanningValidationError(f"Invalid case_id syntax: {self.case_id!r}")
        if not isinstance(self.patient_hash, str) or not PATIENT_HASH_REGEX.match(self.patient_hash):
            raise PlanningValidationError(f"patient_hash must be a 64-character SHA-256 hex string, got {self.patient_hash!r}")
        if not isinstance(self.procedure_code, str) or not self.procedure_code.strip():
            raise PlanningValidationError("procedure_code must be a non-empty string")
        if not isinstance(self.laterality, SurgicalLaterality):
            raise PlanningValidationError(f"Invalid laterality: {self.laterality!r}")
        if not isinstance(self.primary_surgeon_id, str) or not self.primary_surgeon_id.strip():
            raise PlanningValidationError("primary_surgeon_id must be non-empty")
        if not isinstance(self.scheduled_date_utc, str) or not self.scheduled_date_utc.strip():
            raise PlanningValidationError("scheduled_date_utc must be non-empty")


@dataclass(frozen=True)
class TrajectoryPlan:
    """Planned 3D surgical vector trajectory and spatial precision tolerances."""

    trajectory_id: str
    target_structure: str
    entry_point_mm: Tuple[float, float, float]
    target_point_mm: Tuple[float, float, float]
    max_lateral_deviation_mm: float
    max_angular_deviation_deg: float
    min_confidence: float
    max_uncertainty: float

    def __post_init__(self) -> None:
        if not isinstance(self.trajectory_id, str) or not PLAN_ID_REGEX.match(self.trajectory_id):
            raise PlanningValidationError(f"Invalid trajectory_id syntax: {self.trajectory_id!r}")
        if not isinstance(self.target_structure, str) or not self.target_structure.strip():
            raise PlanningValidationError("target_structure must be non-empty")

        for name, pt in (("entry_point_mm", self.entry_point_mm), ("target_point_mm", self.target_point_mm)):
            if not isinstance(pt, (tuple, list)) or len(pt) != 3:
                raise PlanningValidationError(f"{name} must be a 3-element tuple of floats")
            if not all(isinstance(v, (int, float)) and math.isfinite(v) for v in pt):
                raise PlanningValidationError(f"{name} coordinates must be finite floats")

        # Geometric degeneracy checks (D301, D303)
        dx = self.target_point_mm[0] - self.entry_point_mm[0]
        dy = self.target_point_mm[1] - self.entry_point_mm[1]
        dz = self.target_point_mm[2] - self.entry_point_mm[2]
        length = math.sqrt(dx * dx + dy * dy + dz * dz)

        if length < MIN_TRAJECTORY_LENGTH_MM:
            raise PlanningValidationError(
                f"Trajectory length ({length:.2f} mm) is less than minimum {MIN_TRAJECTORY_LENGTH_MM} mm"
            )
        if length > MAX_TRAJECTORY_LENGTH_MM:
            raise PlanningValidationError(
                f"Trajectory length ({length:.2f} mm) exceeds maximum {MAX_TRAJECTORY_LENGTH_MM} mm"
            )

        if self.max_lateral_deviation_mm <= 0.0 or not math.isfinite(self.max_lateral_deviation_mm):
            raise PlanningValidationError("max_lateral_deviation_mm must be positive finite float")
        if self.max_angular_deviation_deg <= 0.0 or not math.isfinite(self.max_angular_deviation_deg):
            raise PlanningValidationError("max_angular_deviation_deg must be positive finite float")
        if not (0.0 <= self.min_confidence <= 1.0) or not math.isfinite(self.min_confidence):
            raise PlanningValidationError("min_confidence must be in [0.0, 1.0]")
        if not (0.0 <= self.max_uncertainty <= 1.0) or not math.isfinite(self.max_uncertainty):
            raise PlanningValidationError("max_uncertainty must be in [0.0, 1.0]")


@dataclass(frozen=True)
class SafetyExclusionZone:
    """Prohibited 3D spatial safety volume around critical neurovascular structures."""

    zone_id: str
    anatomical_structure: str
    center_point_mm: Tuple[float, float, float]
    bounding_radius_mm: float
    min_clearance_mm: float
    severity_if_breached: InterlockSeverity = InterlockSeverity.BLOCKING

    def __post_init__(self) -> None:
        if not isinstance(self.zone_id, str) or not PLAN_ID_REGEX.match(self.zone_id):
            raise PlanningValidationError(f"Invalid zone_id syntax: {self.zone_id!r}")
        if not isinstance(self.anatomical_structure, str) or not self.anatomical_structure.strip():
            raise PlanningValidationError("anatomical_structure must be non-empty")
        if not isinstance(self.center_point_mm, (tuple, list)) or len(self.center_point_mm) != 3:
            raise PlanningValidationError("center_point_mm must be a 3-element tuple of floats")
        if not all(isinstance(v, (int, float)) and math.isfinite(v) for v in self.center_point_mm):
            raise PlanningValidationError("center_point_mm coordinates must be finite floats")
        if self.bounding_radius_mm <= 0.0 or not math.isfinite(self.bounding_radius_mm):
            raise PlanningValidationError("bounding_radius_mm must be positive finite float")
        if self.min_clearance_mm <= 0.0 or not math.isfinite(self.min_clearance_mm):
            raise PlanningValidationError("min_clearance_mm must be positive finite float")
        if not isinstance(self.severity_if_breached, InterlockSeverity):
            raise PlanningValidationError(f"Invalid severity_if_breached: {self.severity_if_breached!r}")


@dataclass(frozen=True)
class SurgicalPlanDefinition:
    """Authoritative preoperative surgical plan containing trajectories and exclusion zones."""

    plan_id: str
    version: str
    case_context: PatientCaseContext
    trajectories: Tuple[TrajectoryPlan, ...]
    exclusion_zones: Tuple[SafetyExclusionZone, ...]
    is_locked: bool = False
    created_at_utc: str = ""

    def __post_init__(self) -> None:
        if not isinstance(self.plan_id, str) or not PLAN_ID_REGEX.match(self.plan_id):
            raise PlanningValidationError(f"Invalid plan_id syntax: {self.plan_id!r}")
        if not isinstance(self.version, str) or not SEMVER_REGEX.match(self.version):
            raise PlanningValidationError(f"Invalid semantic version: {self.version!r}")
        if not isinstance(self.case_context, PatientCaseContext):
            raise PlanningValidationError("case_context must be a PatientCaseContext instance")
        if not isinstance(self.trajectories, (tuple, list)) or len(self.trajectories) == 0:
            raise PlanningValidationError("Plan must contain at least one TrajectoryPlan")
        if len(self.trajectories) > MAX_TRAJECTORIES_PER_PLAN:
            raise PlanningValidationError(
                f"Exceeded MAX_TRAJECTORIES_PER_PLAN ({MAX_TRAJECTORIES_PER_PLAN})"
            )
        if len(self.exclusion_zones) > MAX_EXCLUSION_ZONES_PER_PLAN:
            raise PlanningValidationError(
                f"Exceeded MAX_EXCLUSION_ZONES_PER_PLAN ({MAX_EXCLUSION_ZONES_PER_PLAN})"
            )


@dataclass(frozen=True)
class PlanVerificationRecord:
    """Formal record of pre-procedure verification (WHO Surgical Safety Timeout)."""

    verification_id: str
    plan_id: str
    session_id: str
    epoch_id: int
    verified: bool
    patient_identity_matched: bool
    procedure_matched: bool
    laterality_matched: bool
    verifier_operator_id: str
    timestamp_utc: str
    details: Mapping[str, Any]
