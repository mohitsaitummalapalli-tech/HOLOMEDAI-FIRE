# -*- coding: utf-8 -*-
"""Canonical Constants and Operational Bounds for M16 Drift & Landmark Monitoring Subsystem."""

from __future__ import annotations

import re

# --------------------------------------------------------------------------
# Capacity Limits
# --------------------------------------------------------------------------
MAX_ACTIVE_DRIFT_SESSIONS: int = 16
MAX_LANDMARKS_PER_SESSION: int = 32
MAX_DRIFT_HISTORY: int = 256
MAX_DWELL_BUFFER_SAMPLES: int = 16

# --------------------------------------------------------------------------
# Spatial & Geometric Bounds (Millimeters)
# --------------------------------------------------------------------------
MAX_ALLOWED_DRIFT_MM: float = 2.0
WARNING_DRIFT_THRESHOLD_MM: float = 1.0
MIN_LANDMARK_TOLERANCE_MM: float = 0.1
MAX_LANDMARK_TOLERANCE_MM: float = 2.0
MAX_POINT_COORDINATE_MM: float = 2000.0

# --------------------------------------------------------------------------
# Confidence & Uncertainty Bounds
# --------------------------------------------------------------------------
MIN_LANDMARK_CONFIDENCE: float = 0.85
MAX_LANDMARK_UNCERTAINTY_MM: float = 1.5

# --------------------------------------------------------------------------
# Temporal & Freshness Bounds
# --------------------------------------------------------------------------
MAX_OBSERVATION_AGE_MS: float = 500.0
MAX_FUTURE_TIMESTAMP_SKEW_MS: float = 50.0
MIN_DWELL_DURATION_MS: float = 150.0
MAX_TIP_JITTER_MM: float = 0.5
MIN_STABLE_SAMPLES: int = 3

# --------------------------------------------------------------------------
# Regex Grammars
# --------------------------------------------------------------------------
LANDMARK_ID_REGEX = re.compile(r"^[a-z0-9]([a-z0-9._-]*[a-z0-9])?$")
SESSION_ID_REGEX = re.compile(r"^[a-zA-Z0-9_-]{1,64}$")

# --------------------------------------------------------------------------
# Coordinate Frame
# --------------------------------------------------------------------------
DEFAULT_PATIENT_TRACKER_FRAME: str = "patient_tracker_frame"

# --------------------------------------------------------------------------
# Service Identity
# --------------------------------------------------------------------------
SERVICE_NAME: str = "drift_service"
