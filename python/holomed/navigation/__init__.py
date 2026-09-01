# -*- coding: utf-8 -*-
"""HoloMed AI - Tracked Instrument Pose, Trajectory Deviation & Navigation Safety Foundation (M14)."""

from __future__ import annotations

from holomed.navigation.constants import (
    DEFAULT_PATIENT_TRACKER_FRAME,
    INSTRUMENT_ID_REGEX,
    MAX_ACTIVE_NAVIGATION_SESSIONS,
    MAX_ALLOWED_POSE_AGE_MS,
    MAX_DEVIATION_HISTORY_RECORDS,
    MAX_FUTURE_TIMESTAMP_SKEW_MS,
    MAX_TOOL_COORDINATE_MM,
    MAX_TRACKED_INSTRUMENTS_PER_SESSION,
    QUATERNION_UNIT_NORM_TOLERANCE,
    SESSION_ID_REGEX,
)
from holomed.navigation.evaluator import TrajectoryDeviationEvaluator
from holomed.navigation.exceptions import (
    NavigationAuthorizationError,
    NavigationCapacityError,
    NavigationDegeneracyError,
    NavigationError,
    NavigationFrameMismatchError,
    NavigationLifecycleError,
    NavigationRegistrationMismatchError,
    NavigationSequenceError,
    NavigationShutdownError,
    NavigationStalenessError,
    NavigationValidationError,
)
from holomed.navigation.geometry import (
    compute_angular_deviation,
    compute_lateral_and_axial_deviation,
    compute_trajectory_geometry,
    convert_mm_to_meters,
    convert_point_mm_to_meters,
    quaternion_to_shaft_direction,
)
from holomed.navigation.models import (
    NavigationState,
    NavigationStatusRecord,
    PositionClass,
    TrackedInstrumentPose,
    TrajectoryDeviationRecord,
)
from holomed.navigation.service import NavigationService

__all__ = [
    # Constants
    "MAX_ALLOWED_POSE_AGE_MS",
    "MAX_FUTURE_TIMESTAMP_SKEW_MS",
    "MAX_TOOL_COORDINATE_MM",
    "QUATERNION_UNIT_NORM_TOLERANCE",
    "MAX_TRACKED_INSTRUMENTS_PER_SESSION",
    "MAX_ACTIVE_NAVIGATION_SESSIONS",
    "MAX_DEVIATION_HISTORY_RECORDS",
    "DEFAULT_PATIENT_TRACKER_FRAME",
    "INSTRUMENT_ID_REGEX",
    "SESSION_ID_REGEX",
    # Exceptions
    "NavigationError",
    "NavigationLifecycleError",
    "NavigationValidationError",
    "NavigationDegeneracyError",
    "NavigationStalenessError",
    "NavigationSequenceError",
    "NavigationFrameMismatchError",
    "NavigationRegistrationMismatchError",
    "NavigationCapacityError",
    "NavigationAuthorizationError",
    "NavigationShutdownError",
    # Models & Enums
    "NavigationState",
    "PositionClass",
    "TrackedInstrumentPose",
    "TrajectoryDeviationRecord",
    "NavigationStatusRecord",
    # Geometry & Math
    "quaternion_to_shaft_direction",
    "compute_trajectory_geometry",
    "compute_lateral_and_axial_deviation",
    "compute_angular_deviation",
    "convert_mm_to_meters",
    "convert_point_mm_to_meters",
    # Evaluator & Service
    "TrajectoryDeviationEvaluator",
    "NavigationService",
]
