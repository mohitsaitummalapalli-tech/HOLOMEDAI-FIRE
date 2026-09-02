# -*- coding: utf-8 -*-
"""M18 — Unified Cross-Service Navigation Safety Authorization Gate Foundation.

This package provides a canonical, deterministic, fail-closed safety evaluation engine
aggregating multi-service evidence (M10 Workflow, M13 Registration, M14 Navigation,
M15 Proximity, M16 Drift, M17 Recovery) into authoritative pre-execution decisions.
"""

from __future__ import annotations

from holomed.safety_gate.constants import (
    MAX_ACTIVE_GATE_SESSIONS,
    MAX_GATE_HISTORY,
    SAFETY_GATE_SCHEMA_VERSION,
    SERVICE_NAME,
    STRUCTURAL_RESOURCE_IDS,
)
from holomed.safety_gate.evaluator import SafetyGateEvaluator
from holomed.safety_gate.exceptions import (
    SafetyGateCapacityError,
    SafetyGateError,
    SafetyGateEvaluationError,
    SafetyGateLifecycleError,
    SafetyGateShutdownError,
    SafetyGateValidationError,
)
from holomed.safety_gate.models import (
    GateDecision,
    GateReasonCode,
    GateRequest,
    GateSeverity,
    GateStatusRecord,
    SafetyGateAction,
    SubsystemSnapshot,
)
from holomed.safety_gate.service import SafetyGateService

__all__ = (
    # Constants
    "MAX_ACTIVE_GATE_SESSIONS",
    "MAX_GATE_HISTORY",
    "SAFETY_GATE_SCHEMA_VERSION",
    "SERVICE_NAME",
    "STRUCTURAL_RESOURCE_IDS",
    # Enums & Models
    "GateDecision",
    "GateReasonCode",
    "GateRequest",
    "GateSeverity",
    "GateStatusRecord",
    "SafetyGateAction",
    "SubsystemSnapshot",
    # Exceptions
    "SafetyGateCapacityError",
    "SafetyGateError",
    "SafetyGateEvaluationError",
    "SafetyGateLifecycleError",
    "SafetyGateShutdownError",
    "SafetyGateValidationError",
    # Evaluator & Service
    "SafetyGateEvaluator",
    "SafetyGateService",
)
