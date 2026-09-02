# -*- coding: utf-8 -*-
"""Immutable Data Models, Enums, and Audit Records for M17 Spatial Recovery."""

from __future__ import annotations

from dataclasses import dataclass, field
import enum
import math
from typing import Any, Mapping, Optional

from holomed.recovery.constants import (
    OPERATOR_ID_REGEX,
    PLAN_ID_REGEX,
    RECOVERY_SCHEMA_VERSION,
    SESSION_ID_REGEX,
)
from holomed.recovery.exceptions import RecoveryValidationError
from holomed.registration.models import (
    FiducialCloud,
    RegistrationQualityReport,
    RegistrationState,
    RigidRegistrationTransform3D,
)


class RecoveryState(str, enum.Enum):
    """Spatial recovery state machine phases."""

    IDLE = "IDLE"
    STAGED = "STAGED"
    SOLVED = "SOLVED"
    VERIFIED = "VERIFIED"
    ACTIVATING = "ACTIVATING"
    ACTIVATED = "ACTIVATED"
    FAILED = "FAILED"
    BLOCKED = "BLOCKED"


@dataclass(frozen=True)
class RecoveryAuthorization:
    """Immutable operator authorization record for recovery activation."""

    operator_id: str
    rationale: str
    timestamp_utc: str
    sequence_number: int
    authorization_reference: str

    def __post_init__(self) -> None:
        if not isinstance(self.operator_id, str) or not OPERATOR_ID_REGEX.match(self.operator_id):
            raise RecoveryValidationError(f"Invalid operator_id format: {self.operator_id!r}")
        if not isinstance(self.rationale, str) or not self.rationale.strip() or len(self.rationale) > 256:
            raise RecoveryValidationError("rationale must be non-empty string <= 256 characters")
        if not isinstance(self.timestamp_utc, str) or not self.timestamp_utc.strip():
            raise RecoveryValidationError("timestamp_utc must be a valid non-empty ISO-8601 string")
        if not isinstance(self.sequence_number, int) or self.sequence_number < 1:
            raise RecoveryValidationError("sequence_number must be a positive integer >= 1")
        if not isinstance(self.authorization_reference, str) or not self.authorization_reference.strip():
            raise RecoveryValidationError("authorization_reference must be a non-empty string")


@dataclass(frozen=True)
class StagedRegistrationCandidate:
    """Isolated pre-validated candidate registration stored in M17 memory."""

    session_id: str
    plan_id: str
    fiducial_cloud: FiducialCloud
    candidate_transform: RigidRegistrationTransform3D
    quality_report: RegistrationQualityReport
    staged_at_utc: str

    def __post_init__(self) -> None:
        if not isinstance(self.session_id, str) or not SESSION_ID_REGEX.match(self.session_id):
            raise RecoveryValidationError(f"Invalid session_id: {self.session_id!r}")
        if not isinstance(self.plan_id, str) or not PLAN_ID_REGEX.match(self.plan_id):
            raise RecoveryValidationError(f"Invalid plan_id: {self.plan_id!r}")


@dataclass(frozen=True)
class RecoveryVerificationSnapshot:
    """Physical checkpoint verification measurement snapshot for recovery."""

    verification_id: str
    session_id: str
    operator_id: str
    measured_drift_error_mm: float
    passed: bool
    verified_at_utc: str

    def __post_init__(self) -> None:
        if not isinstance(self.verification_id, str) or not self.verification_id.strip():
            raise RecoveryValidationError("verification_id must be non-empty")
        if not isinstance(self.session_id, str) or not SESSION_ID_REGEX.match(self.session_id):
            raise RecoveryValidationError(f"Invalid session_id: {self.session_id!r}")
        if not isinstance(self.operator_id, str) or not OPERATOR_ID_REGEX.match(self.operator_id):
            raise RecoveryValidationError(f"Invalid operator_id: {self.operator_id!r}")
        if not isinstance(self.measured_drift_error_mm, (int, float)) or not math.isfinite(self.measured_drift_error_mm):
            raise RecoveryValidationError("measured_drift_error_mm must be a finite float")
        if self.measured_drift_error_mm < 0.0:
            raise RecoveryValidationError("measured_drift_error_mm must be non-negative")


@dataclass(frozen=True)
class RecoveryStatusRecord:
    """Immutable status snapshot of the spatial recovery orchestrator for a session."""

    recovery_id: str
    session_id: str
    state: RecoveryState
    registration_revision: int
    prior_registration_state: Optional[RegistrationState]
    active_transform: Optional[RigidRegistrationTransform3D]
    last_quality_report: Optional[RegistrationQualityReport]
    last_verification: Optional[RecoveryVerificationSnapshot]
    updated_at_utc: str
    schema_version: str = RECOVERY_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if not isinstance(self.recovery_id, str) or not self.recovery_id.strip():
            raise RecoveryValidationError("recovery_id must be non-empty")
        if not isinstance(self.session_id, str) or not SESSION_ID_REGEX.match(self.session_id):
            raise RecoveryValidationError(f"Invalid session_id: {self.session_id!r}")
        if not isinstance(self.registration_revision, int) or self.registration_revision < 0:
            raise RecoveryValidationError("registration_revision must be an integer >= 0")
