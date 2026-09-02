# -*- coding: utf-8 -*-
"""M17 — Intraoperative Spatial Re-Registration & Recovery Orchestration Foundation.

This package provides deterministic, thread-safe, fail-closed spatial recovery orchestration,
allowing candidate registration staging, pure mathematical pre-validation, physical checkpoint
re-verification, and sequential rebinding into M16, M15, and M14 without mutating frozen code.
"""

from __future__ import annotations

from holomed.recovery.constants import (
    DEFAULT_PATIENT_TRACKER_FRAME,
    DEFAULT_PLAN_FRAME,
    MAX_ACTIVE_RECOVERY_SESSIONS,
    MAX_ALLOWED_RECOVERY_DRIFT_MM,
    MAX_ALLOWED_RECOVERY_FRE_MM,
    MAX_AUTHORIZATION_AGE_MS,
    MAX_RECOVERY_HISTORY,
    RECOVERY_SCHEMA_VERSION,
    SERVICE_NAME,
    STRUCTURAL_RESOURCE_IDS,
)
from holomed.recovery.evaluator import RecoveryEvaluator
from holomed.recovery.exceptions import (
    RecoveryAccuracyError,
    RecoveryActivationError,
    RecoveryAuthorizationError,
    RecoveryCapacityError,
    RecoveryConsistencyError,
    RecoveryError,
    RecoveryLifecycleError,
    RecoveryShutdownError,
    RecoveryValidationError,
    RecoveryVerificationError,
)
from holomed.recovery.models import (
    RecoveryAuthorization,
    RecoveryState,
    RecoveryStatusRecord,
    RecoveryVerificationSnapshot,
    StagedRegistrationCandidate,
)
from holomed.recovery.service import RecoveryService

__all__ = (
    # Constants
    "DEFAULT_PATIENT_TRACKER_FRAME",
    "DEFAULT_PLAN_FRAME",
    "MAX_ACTIVE_RECOVERY_SESSIONS",
    "MAX_ALLOWED_RECOVERY_DRIFT_MM",
    "MAX_ALLOWED_RECOVERY_FRE_MM",
    "MAX_AUTHORIZATION_AGE_MS",
    "MAX_RECOVERY_HISTORY",
    "RECOVERY_SCHEMA_VERSION",
    "SERVICE_NAME",
    "STRUCTURAL_RESOURCE_IDS",
    # Enums & Models
    "RecoveryAuthorization",
    "RecoveryState",
    "RecoveryStatusRecord",
    "RecoveryVerificationSnapshot",
    "StagedRegistrationCandidate",
    # Exceptions
    "RecoveryAccuracyError",
    "RecoveryActivationError",
    "RecoveryAuthorizationError",
    "RecoveryCapacityError",
    "RecoveryConsistencyError",
    "RecoveryError",
    "RecoveryLifecycleError",
    "RecoveryShutdownError",
    "RecoveryValidationError",
    "RecoveryVerificationError",
    # Evaluator & Service
    "RecoveryEvaluator",
    "RecoveryService",
)
