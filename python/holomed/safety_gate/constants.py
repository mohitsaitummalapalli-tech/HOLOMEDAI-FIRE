# -*- coding: utf-8 -*-
"""Canonical Constants and Structural Handle Definitions for M18 Safety Gate."""

from __future__ import annotations

import re

# Protocol & Schema Version
SAFETY_GATE_SCHEMA_VERSION: str = "1.0"
SERVICE_NAME: str = "safety_gate_service"

# Canonical Structural Resource Identifiers (Exactly 4)
STRUCTURAL_RESOURCE_IDS: tuple[str, ...] = (
    "gate.registry",
    "gate.evaluator",
    "gate.interlock",
    "gate.auditor",
)

# Capacity Limits
MAX_ACTIVE_GATE_SESSIONS: int = 16
MAX_GATE_HISTORY: int = 256

# Identification Regex Patterns
SESSION_ID_REGEX = re.compile(r"^[a-zA-Z0-9_-]{1,64}$")
INSTRUMENT_ID_REGEX = re.compile(r"^[a-zA-Z0-9_-]{1,64}$")
TRAJECTORY_ID_REGEX = re.compile(r"^[a-zA-Z0-9_-]{1,64}$")
