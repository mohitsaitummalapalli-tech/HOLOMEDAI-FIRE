# -*- coding: utf-8 -*-
"""Canonical Constants and Operational Bounds for M14 Navigation Subsystem."""

from __future__ import annotations

import re

# Temporal Freshness & Clock Skew Bounds (Milliseconds)
MAX_ALLOWED_POSE_AGE_MS: float = 200.0
MAX_FUTURE_TIMESTAMP_SKEW_MS: float = 50.0

# Coordinate Operational Bounds (Millimeters)
MAX_TOOL_COORDINATE_MM: float = 2000.0

# Quaternion Validation Tolerances
QUATERNION_UNIT_NORM_TOLERANCE: float = 1e-4

# Capacity Limits
MAX_TRACKED_INSTRUMENTS_PER_SESSION: int = 8
MAX_ACTIVE_NAVIGATION_SESSIONS: int = 16
MAX_DEVIATION_HISTORY_RECORDS: int = 256

# Coordinate Frame Identifiers
DEFAULT_PATIENT_TRACKER_FRAME: str = "patient_tracker_frame"

# Regex Grammars
INSTRUMENT_ID_REGEX = re.compile(r"^[a-z0-9]([a-z0-9._-]*[a-z0-9])?$")
SESSION_ID_REGEX = re.compile(r"^[a-zA-Z0-9_-]{1,64}$")
