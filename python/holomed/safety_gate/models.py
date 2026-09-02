# -*- coding: utf-8 -*-
"""Data Models, Action Enums, and Decision Records for M18 Safety Gate."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any, Mapping, Optional, Tuple

from holomed.safety_gate.constants import (
    INSTRUMENT_ID_REGEX,
    SAFETY_GATE_SCHEMA_VERSION,
    SESSION_ID_REGEX,
    TRAJECTORY_ID_REGEX,
)
from holomed.safety_gate.exceptions import SafetyGateValidationError
from holomed.tools.models import TOOL_ID_REGEX, ToolSafetyClassification


class SafetyGateAction(str, Enum):
    """Explicit requested software operation being evaluated."""

    TOOL_NAVIGATION = "TOOL_NAVIGATION"
    TRAJECTORY_ALIGNMENT = "TRAJECTORY_ALIGNMENT"
    RECOVERY_REORIENTATION = "RECOVERY_REORIENTATION"
    WORKFLOW_RESUMPTION = "WORKFLOW_RESUMPTION"
    TOOL_INVOCATION = "TOOL_INVOCATION"


class GateDecision(str, Enum):
    """Authoritative safety gate evaluation outcome."""

    PERMITTED_CLEAR = "PERMITTED_CLEAR"
    PERMITTED_WITH_CAUTION = "PERMITTED_WITH_CAUTION"
    DENIED_INTERLOCKED = "DENIED_INTERLOCKED"
    DENIED_CRITICAL = "DENIED_CRITICAL"


class GateSeverity(str, Enum):
    """Severity classification of safety gate condition."""

    NONE = "NONE"
    WARNING = "WARNING"
    BLOCKING = "BLOCKING"
    CRITICAL = "CRITICAL"


class GateReasonCode(str, Enum):
    """Machine-readable canonical cause for safety gate decision."""

    NONE = "NONE"
    APPROACHING_MARGIN = "APPROACHING_MARGIN"
    TRACKING_DEVIATION_WARNING = "TRACKING_DEVIATION_WARNING"
    LANDMARK_DRIFT_WARNING = "LANDMARK_DRIFT_WARNING"
    CRITICAL_EXCLUSION_ZONE_BREACH = "CRITICAL_EXCLUSION_ZONE_BREACH"
    LANDMARK_DRIFT_EXCEEDED = "LANDMARK_DRIFT_EXCEEDED"
    LANDMARK_INTEGRITY_INTERLOCKED = "LANDMARK_INTEGRITY_INTERLOCKED"
    SPATIAL_RECOVERY_FAILED = "SPATIAL_RECOVERY_FAILED"
    REGISTRATION_UNVERIFIED = "REGISTRATION_UNVERIFIED"
    WORKFLOW_PHASE_BLOCKED = "WORKFLOW_PHASE_BLOCKED"
    NAVIGATION_UNAVAILABLE = "NAVIGATION_UNAVAILABLE"
    RUNTIME_EPOCH_MISMATCH = "RUNTIME_EPOCH_MISMATCH"
    SESSION_MISMATCH = "SESSION_MISMATCH"


@dataclass(frozen=True)
class GateRequest:
    """Immutable evaluation request submitted to the safety gate."""

    session_id: str
    action: SafetyGateAction
    sequence_number: int
    now_utc: str
    instrument_id: Optional[str] = None
    target_trajectory_id: Optional[str] = None
    recovery_revision: Optional[int] = None
    tool_id: Optional[str] = None
    safety_classification: Optional[ToolSafetyClassification] = None

    def __post_init__(self) -> None:
        if not isinstance(self.session_id, str) or not SESSION_ID_REGEX.match(self.session_id):
            raise SafetyGateValidationError(f"Invalid session_id: {self.session_id!r}")
        if not isinstance(self.action, SafetyGateAction):
            raise SafetyGateValidationError(f"action must be an instance of SafetyGateAction, got {self.action!r}")
        if not isinstance(self.sequence_number, int) or self.sequence_number < 1:
            raise SafetyGateValidationError("sequence_number must be a positive integer >= 1")
        if not isinstance(self.now_utc, str) or not self.now_utc.strip():
            raise SafetyGateValidationError("now_utc must be a non-empty ISO-8601 string")
        if self.tool_id is not None:
            if not isinstance(self.tool_id, str) or not TOOL_ID_REGEX.match(self.tool_id):
                raise SafetyGateValidationError(f"Invalid tool_id: {self.tool_id!r}")
        if self.safety_classification is not None:
            if not isinstance(self.safety_classification, ToolSafetyClassification):
                raise SafetyGateValidationError(
                    f"safety_classification must be ToolSafetyClassification, got {self.safety_classification!r}"
                )
        if self.instrument_id is not None:
            if not isinstance(self.instrument_id, str) or not INSTRUMENT_ID_REGEX.match(self.instrument_id):
                raise SafetyGateValidationError(f"Invalid instrument_id: {self.instrument_id!r}")
        if self.target_trajectory_id is not None:
            if not isinstance(self.target_trajectory_id, str) or not TRAJECTORY_ID_REGEX.match(self.target_trajectory_id):
                raise SafetyGateValidationError(f"Invalid target_trajectory_id: {self.target_trajectory_id!r}")
        if self.recovery_revision is not None:
            if not isinstance(self.recovery_revision, int) or self.recovery_revision < 1:
                raise SafetyGateValidationError("recovery_revision must be a positive integer >= 1")


@dataclass(frozen=True)
class SubsystemSnapshot:
    """Captured status snapshot of an individual subsystem during gate evaluation."""

    subsystem_name: str
    state: str
    epoch_id: Optional[int]
    is_nominal: bool
    details: Mapping[str, Any]


@dataclass(frozen=True)
class GateStatusRecord:
    """Canonical safety decision snapshot for a clinical session."""

    session_id: str
    decision: GateDecision
    severity: GateSeverity
    reason_code: GateReasonCode
    action: SafetyGateAction
    sequence_number: int
    subsystem_snapshots: Tuple[SubsystemSnapshot, ...]
    evaluated_at_utc: str
    schema_version: str = SAFETY_GATE_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if not isinstance(self.session_id, str) or not SESSION_ID_REGEX.match(self.session_id):
            raise SafetyGateValidationError(f"Invalid session_id: {self.session_id!r}")
        if not isinstance(self.sequence_number, int) or self.sequence_number < 1:
            raise SafetyGateValidationError("sequence_number must be an integer >= 1")
