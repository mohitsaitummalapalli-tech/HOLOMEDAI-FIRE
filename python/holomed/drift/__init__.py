# -*- coding: utf-8 -*-
"""HoloMed AI - Intraoperative Registration Drift Detection & Anatomical Landmark Confirmation Foundation (M16)."""

from __future__ import annotations

from holomed.drift.constants import (
    DEFAULT_PATIENT_TRACKER_FRAME,
    LANDMARK_ID_REGEX,
    MAX_ACTIVE_DRIFT_SESSIONS,
    MAX_ALLOWED_DRIFT_MM,
    MAX_DRIFT_HISTORY,
    MAX_DWELL_BUFFER_SAMPLES,
    MAX_FUTURE_TIMESTAMP_SKEW_MS,
    MAX_LANDMARKS_PER_SESSION,
    MAX_LANDMARK_TOLERANCE_MM,
    MAX_OBSERVATION_AGE_MS,
    MAX_POINT_COORDINATE_MM,
    MAX_TIP_JITTER_MM,
    MIN_DWELL_DURATION_MS,
    MIN_LANDMARK_CONFIDENCE,
    MIN_LANDMARK_TOLERANCE_MM,
    MIN_STABLE_SAMPLES,
    SERVICE_NAME,
    SESSION_ID_REGEX,
    WARNING_DRIFT_THRESHOLD_MM,
)
from holomed.drift.evaluator import DriftEvaluator
from holomed.drift.exceptions import (
    DriftCapacityError,
    DriftEpochMismatchError,
    DriftError,
    DriftInterlockError,
    DriftLifecycleError,
    DriftRegistrationError,
    DriftSequenceError,
    DriftShutdownError,
    DriftValidationError,
)
from holomed.drift.geometry import (
    compute_dwell_stability,
    compute_effective_tolerance,
    compute_euclidean_residual,
    convert_point_mm_to_meters,
)
from holomed.drift.models import (
    DriftState,
    DriftStatusRecord,
    LandmarkDefinition,
    LandmarkObservation,
    LandmarkVerificationRecord,
)
from holomed.drift.service import DriftService

__all__ = [
    # Constants
    "MAX_ACTIVE_DRIFT_SESSIONS",
    "MAX_LANDMARKS_PER_SESSION",
    "MAX_DRIFT_HISTORY",
    "MAX_DWELL_BUFFER_SAMPLES",
    "MAX_ALLOWED_DRIFT_MM",
    "WARNING_DRIFT_THRESHOLD_MM",
    "MIN_LANDMARK_TOLERANCE_MM",
    "MAX_LANDMARK_TOLERANCE_MM",
    "MAX_POINT_COORDINATE_MM",
    "MIN_LANDMARK_CONFIDENCE",
    "MAX_LANDMARK_UNCERTAINTY_MM",
    "MAX_OBSERVATION_AGE_MS",
    "MAX_FUTURE_TIMESTAMP_SKEW_MS",
    "MIN_DWELL_DURATION_MS",
    "MAX_TIP_JITTER_MM",
    "MIN_STABLE_SAMPLES",
    "DEFAULT_PATIENT_TRACKER_FRAME",
    "LANDMARK_ID_REGEX",
    "SESSION_ID_REGEX",
    "SERVICE_NAME",
    # Exceptions
    "DriftError",
    "DriftLifecycleError",
    "DriftValidationError",
    "DriftCapacityError",
    "DriftRegistrationError",
    "DriftSequenceError",
    "DriftEpochMismatchError",
    "DriftInterlockError",
    "DriftShutdownError",
    # Models & Enums
    "DriftState",
    "LandmarkDefinition",
    "LandmarkObservation",
    "LandmarkVerificationRecord",
    "DriftStatusRecord",
    # Geometry
    "compute_euclidean_residual",
    "compute_effective_tolerance",
    "compute_dwell_stability",
    "convert_point_mm_to_meters",
    # Evaluator & Service
    "DriftEvaluator",
    "DriftService",
]
