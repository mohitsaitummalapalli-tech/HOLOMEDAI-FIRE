# -*- coding: utf-8 -*-
"""Canonical Constants and Operational Bounds for M15 Proximity Monitoring Subsystem."""

from __future__ import annotations

import re

# --------------------------------------------------------------------------
# Capacity Limits
# --------------------------------------------------------------------------
MAX_MONITORED_ZONES: int = 64
MAX_ACTIVE_PROXIMITY_SESSIONS: int = 16
MAX_EVALUATION_HISTORY: int = 256
MAX_INSTRUMENTS_PER_EVALUATION: int = 8

# --------------------------------------------------------------------------
# Geometric & Safety Bounds (Millimeters)
# --------------------------------------------------------------------------
MIN_ZONE_RADIUS_MM: float = 0.5
MAX_ZONE_RADIUS_MM: float = 200.0
MIN_CLEARANCE_MM: float = 0.1
MAX_CLEARANCE_MM: float = 100.0
MIN_SHAFT_RADIUS_MM: float = 0.1
MAX_SHAFT_RADIUS_MM: float = 25.0
MIN_SHAFT_LENGTH_MM: float = 1.0
MAX_SHAFT_LENGTH_MM: float = 500.0
MAX_COORDINATE_MM: float = 2000.0

# --------------------------------------------------------------------------
# Registration Error Bounds
# --------------------------------------------------------------------------
MAX_REGISTRATION_ERROR_MM: float = 50.0
MAX_TRACKING_UNCERTAINTY_MM: float = 25.0
MAX_STATIC_MARGIN_MM: float = 50.0

# --------------------------------------------------------------------------
# Temporal Bounds
# --------------------------------------------------------------------------
MAX_POSE_AGE_MS: float = 500.0
MAX_RATE_DELTA_TIME_S: float = 0.5

# --------------------------------------------------------------------------
# Regex Grammars
# --------------------------------------------------------------------------
ZONE_ID_REGEX = re.compile(r"^[a-z0-9]([a-z0-9._-]*[a-z0-9])?$")
INSTRUMENT_ID_REGEX = re.compile(r"^[a-z0-9]([a-z0-9._-]*[a-z0-9])?$")
SESSION_ID_REGEX = re.compile(r"^[a-zA-Z0-9_-]{1,64}$")

# --------------------------------------------------------------------------
# Coordinate Frame
# --------------------------------------------------------------------------
DEFAULT_PATIENT_TRACKER_FRAME: str = "patient_tracker_frame"

# --------------------------------------------------------------------------
# Service Identity
# --------------------------------------------------------------------------
SERVICE_NAME: str = "proximity_service"
