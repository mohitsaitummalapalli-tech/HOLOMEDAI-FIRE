# -*- coding: utf-8 -*-
"""Canonical Constants and Structural Resource Identifiers for M19 Execution Gateway."""

from __future__ import annotations

import re

# Protocol & Schema Version
EXECUTION_SCHEMA_VERSION: str = "1.0"
SERVICE_NAME: str = "navigation_execution_service"

# Canonical Structural Resource Identifiers (Exactly 4)
STRUCTURAL_RESOURCE_IDS: tuple[str, ...] = (
    "execution.coordinator",
    "execution.gatekeeper",
    "execution.evaluator",
    "execution.auditor",
)

# Capacity Limits
MAX_ACTIVE_EXECUTION_SESSIONS: int = 16
MAX_EXECUTION_HISTORY: int = 256

# Identification Regex Patterns
SESSION_ID_REGEX = re.compile(r"^[a-zA-Z0-9_-]{1,64}$")
INSTRUMENT_ID_REGEX = re.compile(r"^[a-zA-Z0-9_-]{1,64}$")
TRAJECTORY_ID_REGEX = re.compile(r"^[a-zA-Z0-9_-]{1,64}$")
