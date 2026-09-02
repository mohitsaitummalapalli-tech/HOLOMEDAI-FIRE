# -*- coding: utf-8 -*-
"""Immutable Data Models, Enums, and Execution Records for M19 Execution Gateway."""

from __future__ import annotations

import enum
from dataclasses import dataclass
from typing import Optional, Tuple

from holomed.drift.models import LandmarkDefinition
from holomed.execution.constants import (
    EXECUTION_SCHEMA_VERSION,
    INSTRUMENT_ID_REGEX,
    SESSION_ID_REGEX,
    TRAJECTORY_ID_REGEX,
)
from holomed.execution.exceptions import ExecutionValidationError
from holomed.navigation.models import (
    TrackedInstrumentPose,
    TrajectoryDeviationRecord,
)
from holomed.planning.models import (
    SafetyExclusionZone,
    TrajectoryPlan,
)
from holomed.recovery.models import (
    RecoveryAuthorization,
    RecoveryStatusRecord,
)
from holomed.registration.models import FiducialCloud
from holomed.safety_gate.models import (
    GateDecision,
    GateReasonCode,
    SafetyGateAction,
)
from holomed.tools.models import ToolSafetyClassification
from holomed.workflow.models import WorkflowToolAuthorizationStatus


class ExecutionStatus(str, enum.Enum):
    """Execution status resulting from dual-gate coordination and subsystem execution."""

    EXECUTED_CLEAR = "EXECUTED_CLEAR"
    EXECUTED_WITH_CAUTION = "EXECUTED_WITH_CAUTION"
    BLOCKED_SAFETY_GATE = "BLOCKED_SAFETY_GATE"
    BLOCKED_WORKFLOW = "BLOCKED_WORKFLOW"
    FAILED_NAVIGATION_GEOMETRY = "FAILED_NAVIGATION_GEOMETRY"


@dataclass(frozen=True)
class NavigationExecutionRequest:
    """Explicit request to execute real-time navigation under dual-gate evaluation."""

    session_id: str
    sequence_number: int
    now_utc: str
    instrument_id: str
    target_trajectory_id: str
    pose: TrackedInstrumentPose
    action: SafetyGateAction = SafetyGateAction.TOOL_NAVIGATION
    tool_safety_classification: Optional[ToolSafetyClassification] = None

    def __post_init__(self) -> None:
        if not isinstance(self.session_id, str) or not SESSION_ID_REGEX.match(self.session_id):
            raise ExecutionValidationError(f"Invalid session_id: {self.session_id!r}")
        if not isinstance(self.sequence_number, int) or self.sequence_number < 1:
            raise ExecutionValidationError("sequence_number must be an integer >= 1")
        if not isinstance(self.now_utc, str) or not self.now_utc.strip():
            raise ExecutionValidationError("now_utc must be a non-empty ISO-8601 string")
        if not isinstance(self.instrument_id, str) or not INSTRUMENT_ID_REGEX.match(self.instrument_id):
            raise ExecutionValidationError(f"Invalid instrument_id: {self.instrument_id!r}")
        if not isinstance(self.target_trajectory_id, str) or not TRAJECTORY_ID_REGEX.match(self.target_trajectory_id):
            raise ExecutionValidationError(f"Invalid target_trajectory_id: {self.target_trajectory_id!r}")
        if not isinstance(self.pose, TrackedInstrumentPose):
            raise ExecutionValidationError(f"pose must be a TrackedInstrumentPose, got {type(self.pose).__name__}")
        if self.action != SafetyGateAction.TOOL_NAVIGATION:
            raise ExecutionValidationError(f"NavigationExecutionRequest requires TOOL_NAVIGATION, got {self.action.value}")


@dataclass(frozen=True)
class NavigationExecutionResult:
    """Canonical result of a coordinated navigation execution cycle."""

    session_id: str
    execution_status: ExecutionStatus
    gate_decision: GateDecision
    gate_reason_code: GateReasonCode
    action: SafetyGateAction
    sequence_number: int
    instrument_id: str
    target_trajectory_id: str
    executed_at_utc: str
    workflow_status: Optional[WorkflowToolAuthorizationStatus] = None
    deviation_record: Optional[TrajectoryDeviationRecord] = None
    error_message: Optional[str] = None
    schema_version: str = EXECUTION_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if not isinstance(self.session_id, str) or not SESSION_ID_REGEX.match(self.session_id):
            raise ExecutionValidationError(f"Invalid session_id: {self.session_id!r}")
        if not isinstance(self.sequence_number, int) or self.sequence_number < 1:
            raise ExecutionValidationError("sequence_number must be an integer >= 1")


@dataclass(frozen=True)
class TrajectoryBindingExecutionRequest:
    """Request to bind and align a plan trajectory through the execution coordinator."""

    session_id: str
    trajectory_id: str
    plan_trajectory: TrajectoryPlan
    sequence_number: int
    now_utc: str
    action: SafetyGateAction = SafetyGateAction.TRAJECTORY_ALIGNMENT
    tool_safety_classification: ToolSafetyClassification = ToolSafetyClassification.VISUALIZATION_ADJUSTMENT

    def __post_init__(self) -> None:
        if not isinstance(self.session_id, str) or not SESSION_ID_REGEX.match(self.session_id):
            raise ExecutionValidationError(f"Invalid session_id: {self.session_id!r}")
        if not isinstance(self.trajectory_id, str) or not TRAJECTORY_ID_REGEX.match(self.trajectory_id):
            raise ExecutionValidationError(f"Invalid trajectory_id: {self.trajectory_id!r}")
        if not isinstance(self.plan_trajectory, TrajectoryPlan):
            raise ExecutionValidationError("plan_trajectory must be a TrajectoryPlan instance")
        if not isinstance(self.sequence_number, int) or self.sequence_number < 1:
            raise ExecutionValidationError("sequence_number must be an integer >= 1")
        if not isinstance(self.now_utc, str) or not self.now_utc.strip():
            raise ExecutionValidationError("now_utc must be a non-empty ISO-8601 string")
        if self.action != SafetyGateAction.TRAJECTORY_ALIGNMENT:
            raise ExecutionValidationError(
                f"TrajectoryBindingExecutionRequest requires TRAJECTORY_ALIGNMENT, got {self.action.value}"
            )


@dataclass(frozen=True)
class TrajectoryBindingExecutionResult:
    """Result of a coordinated trajectory binding operation."""

    session_id: str
    trajectory_id: str
    execution_status: ExecutionStatus
    gate_decision: GateDecision
    gate_reason_code: GateReasonCode
    action: SafetyGateAction
    sequence_number: int
    executed_at_utc: str
    workflow_status: Optional[WorkflowToolAuthorizationStatus] = None
    error_message: Optional[str] = None
    schema_version: str = EXECUTION_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if not isinstance(self.session_id, str) or not SESSION_ID_REGEX.match(self.session_id):
            raise ExecutionValidationError(f"Invalid session_id: {self.session_id!r}")
        if not isinstance(self.sequence_number, int) or self.sequence_number < 1:
            raise ExecutionValidationError("sequence_number must be an integer >= 1")


@dataclass(frozen=True)
class RecoveryReorientationExecutionRequest:
    """Explicit request for spatial recovery reorientation through the execution coordinator."""

    session_id: str
    sequence_number: int
    now_utc: str
    action: SafetyGateAction = SafetyGateAction.RECOVERY_REORIENTATION
    tool_safety_classification: ToolSafetyClassification = ToolSafetyClassification.TELEMETRY_RECORDING
    plan_id: Optional[str] = None
    cloud: Optional[FiducialCloud] = None
    authorization: Optional[RecoveryAuthorization] = None
    checkpoint_plan_mm: Optional[Tuple[float, float, float]] = None
    checkpoint_measured_mm: Optional[Tuple[float, float, float]] = None
    plan_trajectory: Optional[TrajectoryPlan] = None
    zones: Optional[Tuple[SafetyExclusionZone, ...]] = None
    landmarks: Optional[Tuple[LandmarkDefinition, ...]] = None
    registration_error_mm: float = 0.5
    static_margin_mm: float = 0.0
    recovery_operation: str = "STATUS"  # "STAGE" | "VERIFY" | "ACTIVATE" | "RESET" | "STATUS"

    def __post_init__(self) -> None:
        if not isinstance(self.session_id, str) or not SESSION_ID_REGEX.match(self.session_id):
            raise ExecutionValidationError(f"Invalid session_id: {self.session_id!r}")
        if not isinstance(self.sequence_number, int) or self.sequence_number < 1:
            raise ExecutionValidationError("sequence_number must be an integer >= 1")
        if not isinstance(self.now_utc, str) or not self.now_utc.strip():
            raise ExecutionValidationError("now_utc must be a non-empty ISO-8601 string")
        if self.action != SafetyGateAction.RECOVERY_REORIENTATION:
            raise ExecutionValidationError(
                f"RecoveryReorientationExecutionRequest requires RECOVERY_REORIENTATION, got {self.action.value}"
            )


@dataclass(frozen=True)
class RecoveryReorientationExecutionResult:
    """Result of a coordinated recovery reorientation execution."""

    session_id: str
    execution_status: ExecutionStatus
    gate_decision: GateDecision
    gate_reason_code: GateReasonCode
    action: SafetyGateAction
    sequence_number: int
    executed_at_utc: str
    workflow_status: Optional[WorkflowToolAuthorizationStatus] = None
    recovery_status: Optional[RecoveryStatusRecord] = None
    error_message: Optional[str] = None
    schema_version: str = EXECUTION_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if not isinstance(self.session_id, str) or not SESSION_ID_REGEX.match(self.session_id):
            raise ExecutionValidationError(f"Invalid session_id: {self.session_id!r}")
        if not isinstance(self.sequence_number, int) or self.sequence_number < 1:
            raise ExecutionValidationError("sequence_number must be an integer >= 1")


@dataclass(frozen=True)
class NavigationExecutionStatusRecord:
    """Active execution status snapshot for a clinical session."""

    session_id: str
    latest_result: NavigationExecutionResult
    epoch_id: int
    updated_at_utc: str
