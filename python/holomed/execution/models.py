# -*- coding: utf-8 -*-
"""Immutable Data Models, Enums, and Execution Records for M19 Execution Gateway."""

from __future__ import annotations

import enum
from dataclasses import dataclass
from typing import Any, Mapping, Optional, Tuple

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
    PlanVerificationRecord,
    SafetyExclusionZone,
    SurgicalLaterality,
    SurgicalPlanDefinition,
    TrajectoryPlan,
)
from holomed.recovery.models import (
    RecoveryAuthorization,
    RecoveryStatusRecord,
)
from holomed.registration.models import (
    FiducialCloud,
    RegistrationStatusRecord,
    RegistrationVerificationSnapshot,
)
from holomed.safety_gate.models import (
    GateDecision,
    GateReasonCode,
    SafetyGateAction,
)
from holomed.tools.models import TOOL_ID_REGEX, ToolResult, ToolSafetyClassification
from holomed.workflow.models import WorkflowStateSnapshot, WorkflowToolAuthorizationStatus


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


@dataclass(frozen=True)
class ToolExecutionRequest:
    """Explicit request to execute a clinical tool under dual-gate evaluation."""

    session_id: str
    tool_id: str
    sequence_number: int
    now_utc: str
    parameters: Mapping[str, Any]
    safety_classification: ToolSafetyClassification
    action: SafetyGateAction = SafetyGateAction.TOOL_INVOCATION
    correlation_id: Optional[str] = None
    causation_id: Optional[str] = None

    def __post_init__(self) -> None:
        if not isinstance(self.session_id, str) or not SESSION_ID_REGEX.match(self.session_id):
            raise ExecutionValidationError(f"Invalid session_id: {self.session_id!r}")
        if not isinstance(self.tool_id, str) or not TOOL_ID_REGEX.match(self.tool_id):
            raise ExecutionValidationError(f"Invalid tool_id: {self.tool_id!r}")
        if not isinstance(self.sequence_number, int) or self.sequence_number < 1:
            raise ExecutionValidationError("sequence_number must be an integer >= 1")
        if not isinstance(self.now_utc, str) or not self.now_utc.strip():
            raise ExecutionValidationError("now_utc must be a non-empty ISO-8601 string")
        if not isinstance(self.safety_classification, ToolSafetyClassification):
            raise ExecutionValidationError(
                f"safety_classification must be ToolSafetyClassification, got {self.safety_classification!r}"
            )
        if self.action != SafetyGateAction.TOOL_INVOCATION:
            raise ExecutionValidationError(f"ToolExecutionRequest requires TOOL_INVOCATION, got {self.action.value}")


@dataclass(frozen=True)
class ToolExecutionResult:
    """Canonical result of a coordinated clinical tool execution."""

    session_id: str
    tool_id: str
    execution_status: ExecutionStatus
    gate_decision: GateDecision
    gate_reason_code: GateReasonCode
    action: SafetyGateAction
    sequence_number: int
    executed_at_utc: str
    workflow_status: Optional[WorkflowToolAuthorizationStatus] = None
    tool_result: Optional[ToolResult] = None
    error_message: Optional[str] = None
    schema_version: str = EXECUTION_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if not isinstance(self.session_id, str) or not SESSION_ID_REGEX.match(self.session_id):
            raise ExecutionValidationError(f"Invalid session_id: {self.session_id!r}")
        if not isinstance(self.sequence_number, int) or self.sequence_number < 1:
            raise ExecutionValidationError("sequence_number must be an integer >= 1")


@dataclass(frozen=True)
class WorkflowResumptionExecutionRequest:
    """Explicit request to trigger clinical workflow recovery resumption via execution gateway."""

    session_id: str
    sequence_number: int
    now_utc: str
    recovery_revision: int
    cleared_interlock_ids: Tuple[str, ...]
    action: SafetyGateAction = SafetyGateAction.WORKFLOW_RESUMPTION

    def __post_init__(self) -> None:
        if not isinstance(self.session_id, str) or not SESSION_ID_REGEX.match(self.session_id):
            raise ExecutionValidationError(f"Invalid session_id: {self.session_id!r}")
        if not isinstance(self.sequence_number, int) or self.sequence_number < 1:
            raise ExecutionValidationError("sequence_number must be an integer >= 1")
        if not isinstance(self.now_utc, str) or not self.now_utc.strip():
            raise ExecutionValidationError("now_utc must be a non-empty ISO-8601 string")
        if not isinstance(self.recovery_revision, int) or self.recovery_revision < 1:
            raise ExecutionValidationError("recovery_revision must be an integer >= 1")
        if not isinstance(self.cleared_interlock_ids, tuple):
            raise ExecutionValidationError("cleared_interlock_ids must be a tuple of strings")
        if self.action != SafetyGateAction.WORKFLOW_RESUMPTION:
            raise ExecutionValidationError(
                f"WorkflowResumptionExecutionRequest requires WORKFLOW_RESUMPTION, got {self.action.value}"
            )


@dataclass(frozen=True)
class WorkflowResumptionExecutionResult:
    """Canonical result of a coordinated workflow recovery resumption."""

    session_id: str
    execution_status: ExecutionStatus
    sequence_number: int
    executed_at_utc: str
    snapshot: Optional[WorkflowStateSnapshot] = None
    error_message: Optional[str] = None
    schema_version: str = EXECUTION_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if not isinstance(self.session_id, str) or not SESSION_ID_REGEX.match(self.session_id):
            raise ExecutionValidationError(f"Invalid session_id: {self.session_id!r}")
        if not isinstance(self.sequence_number, int) or self.sequence_number < 1:
            raise ExecutionValidationError("sequence_number must be an integer >= 1")


@dataclass(frozen=True)
class RegistrationExecutionRequest:
    """Explicit request to execute initial spatial registration operation via execution gateway."""

    session_id: str
    sequence_number: int
    now_utc: str
    operation: str  # "SUBMIT", "SOLVE", "VERIFY"
    plan_id: Optional[str] = None
    cloud: Optional[FiducialCloud] = None
    operator_id: Optional[str] = None
    checkpoint_plan_mm: Optional[Tuple[float, float, float]] = None
    checkpoint_measured_mm: Optional[Tuple[float, float, float]] = None
    action: SafetyGateAction = SafetyGateAction.TRAJECTORY_ALIGNMENT
    tool_safety_classification: Optional[ToolSafetyClassification] = None

    def __post_init__(self) -> None:
        if not isinstance(self.session_id, str) or not SESSION_ID_REGEX.match(self.session_id):
            raise ExecutionValidationError(f"Invalid session_id: {self.session_id!r}")
        if not isinstance(self.sequence_number, int) or self.sequence_number < 1:
            raise ExecutionValidationError("sequence_number must be an integer >= 1")
        if not isinstance(self.now_utc, str) or not self.now_utc.strip():
            raise ExecutionValidationError("now_utc must be a non-empty ISO-8601 string")
        if not isinstance(self.operation, str) or self.operation.upper() not in ("SUBMIT", "SOLVE", "VERIFY"):
            raise ExecutionValidationError(f"Invalid registration operation: {self.operation!r}")
        if self.action != SafetyGateAction.TRAJECTORY_ALIGNMENT:
            raise ExecutionValidationError(
                f"RegistrationExecutionRequest requires TRAJECTORY_ALIGNMENT, got {self.action.value}"
            )


@dataclass(frozen=True)
class RegistrationExecutionResult:
    """Canonical result of a coordinated registration execution operation."""

    session_id: str
    execution_status: ExecutionStatus
    gate_decision: GateDecision
    gate_reason_code: GateReasonCode
    action: SafetyGateAction
    sequence_number: int
    operation: str
    executed_at_utc: str
    workflow_status: Optional[WorkflowToolAuthorizationStatus] = None
    registration_record: Optional[RegistrationStatusRecord] = None
    verification_snapshot: Optional[RegistrationVerificationSnapshot] = None
    error_message: Optional[str] = None
    schema_version: str = EXECUTION_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if not isinstance(self.session_id, str) or not SESSION_ID_REGEX.match(self.session_id):
            raise ExecutionValidationError(f"Invalid session_id: {self.session_id!r}")
        if not isinstance(self.sequence_number, int) or self.sequence_number < 1:
            raise ExecutionValidationError("sequence_number must be an integer >= 1")


@dataclass(frozen=True)
class PlanningExecutionRequest:
    """Explicit request to execute preoperative planning operation via execution gateway (M24)."""

    session_id: str
    sequence_number: int
    now_utc: str
    operation: str  # "SUBMIT", "LOCK", "VERIFY"
    plan: Optional[SurgicalPlanDefinition] = None
    plan_id: Optional[str] = None
    operator_id: Optional[str] = None
    patient_hash: Optional[str] = None
    procedure_code: Optional[str] = None
    laterality: Optional[SurgicalLaterality] = None
    action: SafetyGateAction = SafetyGateAction.TRAJECTORY_ALIGNMENT
    tool_safety_classification: Optional[ToolSafetyClassification] = None

    def __post_init__(self) -> None:
        if not isinstance(self.session_id, str) or not SESSION_ID_REGEX.match(self.session_id):
            raise ExecutionValidationError(f"Invalid session_id: {self.session_id!r}")
        if not isinstance(self.sequence_number, int) or self.sequence_number < 1:
            raise ExecutionValidationError("sequence_number must be an integer >= 1")
        if not isinstance(self.now_utc, str) or not self.now_utc.strip():
            raise ExecutionValidationError("now_utc must be a non-empty ISO-8601 string")
        if not isinstance(self.operation, str) or self.operation.upper() not in ("SUBMIT", "LOCK", "VERIFY"):
            raise ExecutionValidationError(f"Invalid planning operation: {self.operation!r}")
        if self.action != SafetyGateAction.TRAJECTORY_ALIGNMENT:
            raise ExecutionValidationError(
                f"PlanningExecutionRequest requires TRAJECTORY_ALIGNMENT, got {self.action.value}"
            )


@dataclass(frozen=True)
class PlanningExecutionResult:
    """Canonical result of a coordinated preoperative planning execution operation (M24)."""

    session_id: str
    execution_status: ExecutionStatus
    gate_decision: GateDecision
    gate_reason_code: GateReasonCode
    action: SafetyGateAction
    sequence_number: int
    operation: str
    executed_at_utc: str
    workflow_status: Optional[WorkflowToolAuthorizationStatus] = None
    plan: Optional[SurgicalPlanDefinition] = None
    verification_record: Optional[PlanVerificationRecord] = None
    error_message: Optional[str] = None
    schema_version: str = EXECUTION_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if not isinstance(self.session_id, str) or not SESSION_ID_REGEX.match(self.session_id):
            raise ExecutionValidationError(f"Invalid session_id: {self.session_id!r}")
        if not isinstance(self.sequence_number, int) or self.sequence_number < 1:
            raise ExecutionValidationError("sequence_number must be an integer >= 1")
