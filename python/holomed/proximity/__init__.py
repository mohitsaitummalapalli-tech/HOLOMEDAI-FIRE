# -*- coding: utf-8 -*-
"""HoloMed AI - Proximity Monitoring, Safety Exclusion Zone Evaluation & Critical Structure Protection (M15)."""

from __future__ import annotations

from holomed.proximity.constants import (
    DEFAULT_PATIENT_TRACKER_FRAME,
    INSTRUMENT_ID_REGEX,
    MAX_ACTIVE_PROXIMITY_SESSIONS,
    MAX_CLEARANCE_MM,
    MAX_COORDINATE_MM,
    MAX_EVALUATION_HISTORY,
    MAX_INSTRUMENTS_PER_EVALUATION,
    MAX_MONITORED_ZONES,
    MAX_POSE_AGE_MS,
    MAX_RATE_DELTA_TIME_S,
    MAX_REGISTRATION_ERROR_MM,
    MAX_SHAFT_LENGTH_MM,
    MAX_SHAFT_RADIUS_MM,
    MAX_STATIC_MARGIN_MM,
    MAX_TRACKING_UNCERTAINTY_MM,
    MAX_ZONE_RADIUS_MM,
    MIN_CLEARANCE_MM,
    MIN_SHAFT_LENGTH_MM,
    MIN_SHAFT_RADIUS_MM,
    MIN_ZONE_RADIUS_MM,
    SERVICE_NAME,
    SESSION_ID_REGEX,
    ZONE_ID_REGEX,
)
from holomed.proximity.evaluator import ProximityEvaluator
from holomed.proximity.exceptions import (
    ProximityCapacityError,
    ProximityError,
    ProximityInterlockError,
    ProximityLifecycleError,
    ProximityRegistrationError,
    ProximitySequenceError,
    ProximityShutdownError,
    ProximityValidationError,
)
from holomed.proximity.geometry import (
    capsule_to_sphere_clearance,
    compute_effective_radius,
    convert_mm_to_meters,
    convert_point_mm_to_meters,
    point_to_sphere_clearance,
)
from holomed.proximity.models import (
    ProximityEvaluationRecord,
    ProximitySeverity,
    ProximityState,
    ProximityStatusRecord,
    ToolClearanceGeometry,
    ZoneBreachRecord,
    proximity_severity_to_interlock,
)
from holomed.proximity.service import ProximityService

__all__ = [
    # Constants
    "MAX_MONITORED_ZONES",
    "MAX_ACTIVE_PROXIMITY_SESSIONS",
    "MAX_EVALUATION_HISTORY",
    "MAX_INSTRUMENTS_PER_EVALUATION",
    "MIN_ZONE_RADIUS_MM",
    "MAX_ZONE_RADIUS_MM",
    "MIN_CLEARANCE_MM",
    "MAX_CLEARANCE_MM",
    "MIN_SHAFT_RADIUS_MM",
    "MAX_SHAFT_RADIUS_MM",
    "MIN_SHAFT_LENGTH_MM",
    "MAX_SHAFT_LENGTH_MM",
    "MAX_COORDINATE_MM",
    "MAX_REGISTRATION_ERROR_MM",
    "MAX_TRACKING_UNCERTAINTY_MM",
    "MAX_STATIC_MARGIN_MM",
    "MAX_POSE_AGE_MS",
    "MAX_RATE_DELTA_TIME_S",
    "DEFAULT_PATIENT_TRACKER_FRAME",
    "INSTRUMENT_ID_REGEX",
    "SESSION_ID_REGEX",
    "ZONE_ID_REGEX",
    "SERVICE_NAME",
    # Exceptions
    "ProximityError",
    "ProximityLifecycleError",
    "ProximityValidationError",
    "ProximityCapacityError",
    "ProximityRegistrationError",
    "ProximitySequenceError",
    "ProximityInterlockError",
    "ProximityShutdownError",
    # Models & Enums
    "ProximityState",
    "ProximitySeverity",
    "ToolClearanceGeometry",
    "ZoneBreachRecord",
    "ProximityEvaluationRecord",
    "ProximityStatusRecord",
    "proximity_severity_to_interlock",
    # Geometry
    "point_to_sphere_clearance",
    "capsule_to_sphere_clearance",
    "compute_effective_radius",
    "convert_mm_to_meters",
    "convert_point_mm_to_meters",
    # Evaluator & Service
    "ProximityEvaluator",
    "ProximityService",
]
