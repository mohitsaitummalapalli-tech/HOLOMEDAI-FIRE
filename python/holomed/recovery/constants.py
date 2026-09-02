# -*- coding: utf-8 -*-
"""Canonical Constants and Physical Hard Bounds for M17 Spatial Recovery."""

from __future__ import annotations

import re

# Protocol & Schema Version
RECOVERY_SCHEMA_VERSION: str = "1.0"
SERVICE_NAME: str = "recovery_service"

# Canonical Structural Resource Identifiers (Exactly 4)
STRUCTURAL_RESOURCE_IDS: tuple[str, ...] = (
    "recovery.registry",
    "recovery.stager",
    "recovery.verifier",
    "recovery.activator",
)

# Capacity Limits
MAX_ACTIVE_RECOVERY_SESSIONS: int = 16
MAX_RECOVERY_HISTORY: int = 256
MAX_STAGED_FIDUCIALS: int = 64

# Clinical Thresholds (Grounded in M13 Physical Registration Standards)
MAX_ALLOWED_RECOVERY_FRE_MM: float = 1.5
MAX_ALLOWED_RECOVERY_DRIFT_MM: float = 1.5

# Operator Authorization Temporal Bound (5 minutes)
MAX_AUTHORIZATION_AGE_MS: float = 300000.0
MAX_FUTURE_TIMESTAMP_SKEW_MS: float = 50.0

# Coordinate Frames
DEFAULT_PLAN_FRAME: str = "plan_frame"
DEFAULT_PATIENT_TRACKER_FRAME: str = "patient_tracker_frame"

# Identification Regex Patterns
SESSION_ID_REGEX = re.compile(r"^[a-zA-Z0-9_-]{1,64}$")
PLAN_ID_REGEX = re.compile(r"^[a-zA-Z0-9_-]{1,64}$")
OPERATOR_ID_REGEX = re.compile(r"^[a-zA-Z0-9_.-]{1,64}$")
