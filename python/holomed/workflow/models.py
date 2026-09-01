# -*- coding: utf-8 -*-
"""Immutable Data Models, Enums, and Hard Bounds for M10 Clinical Workflow."""

from __future__ import annotations

from dataclasses import dataclass, field
import enum
import math
import re
from types import MappingProxyType
from typing import Any, Mapping, Optional, Sequence

from holomed.tools.models import ToolCategory, ToolSafetyClassification
from holomed.workflow.exceptions import WorkflowValidationError

# Canonical Hard Bounds
WORKFLOW_SCHEMA_VERSION: str = "1.0"
MAX_ACTIVE_WORKFLOWS: int = 16
MAX_TRANSITIONS_PER_WORKFLOW: int = 1024
MAX_PENDING_CONFIRMATIONS: int = 16
MAX_REGISTERED_CHECKPOINTS: int = 32
MAX_WORKFLOW_EVENT_LOG: int = 1000
MAX_PROCEDURE_PHASES: int = 16

PROCEDURE_ID_REGEX = re.compile(r"^[a-zA-Z0-9_-]{1,64}$")
VERSION_REGEX = re.compile(r"^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)$")
UUID_REGEX = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")


class WorkflowPhase(str, enum.Enum):
    """Clinical surgical workflow phases."""

    PATIENT_CONTEXT = "PATIENT_CONTEXT"
    PRE_PROCEDURE_PLANNING = "PRE_PROCEDURE_PLANNING"
    REGISTRATION = "REGISTRATION"
    SAFETY_TIMEOUT = "SAFETY_TIMEOUT"
    NAVIGATION = "NAVIGATION"
    INTERVENTION = "INTERVENTION"
    VERIFICATION = "VERIFICATION"
    COMPLETION = "COMPLETION"
    ABORTED = "ABORTED"
    RECOVERY_REQUIRED = "RECOVERY_REQUIRED"


class InterlockSeverity(str, enum.Enum):
    """Severity hierarchy for safety interlocks."""

    INFO = "INFO"
    WARNING = "WARNING"
    BLOCKING = "BLOCKING"
    CRITICAL = "CRITICAL"


class WorkflowToolAuthorizationStatus(str, enum.Enum):
    """Workflow-level authorization evaluation for tool invocations."""

    PERMITTED = "PERMITTED"
    REQUIRES_CONFIRMATION = "REQUIRES_CONFIRMATION"
    BLOCKED_PHASE = "BLOCKED_PHASE"
    BLOCKED_INTERLOCK = "BLOCKED_INTERLOCK"
    BLOCKED_CATEGORICAL = "BLOCKED_CATEGORICAL"


class WorkflowHealthStatus(str, enum.Enum):
    """Health classification of workflow supervisor."""

    HEALTHY = "HEALTHY"
    DEGRADED = "DEGRADED"
    UNHEALTHY = "UNHEALTHY"
    FAILED = "FAILED"
    RECOVERY_REQUIRED = "RECOVERY_REQUIRED"


@dataclass(frozen=True)
class ProcedureDefinition:
    """Immutable template defining legal procedural phases, confirmations, and tool scopes."""

    procedure_id: str
    version: str
    name: str
    allowed_phases: tuple[WorkflowPhase, ...]
    required_confirmations: tuple[WorkflowPhase, ...]
    allowed_tool_categories: tuple[ToolCategory, ...]
    schema_version: str = WORKFLOW_SCHEMA_VERSION
    metadata: Mapping[str, Any] = field(default_factory=lambda: MappingProxyType({}))

    def __post_init__(self) -> None:
        if not isinstance(self.procedure_id, str) or not PROCEDURE_ID_REGEX.match(self.procedure_id):
            raise WorkflowValidationError(f"Invalid procedure_id: {self.procedure_id!r}")
        if not isinstance(self.version, str) or not VERSION_REGEX.match(self.version):
            raise WorkflowValidationError(f"Invalid semantic version: {self.version!r}")
        if not isinstance(self.name, str) or not self.name.strip() or len(self.name) > 128:
            raise WorkflowValidationError("name must be non-empty string <= 128 characters")
        if not self.allowed_phases or len(self.allowed_phases) > MAX_PROCEDURE_PHASES:
            raise WorkflowValidationError(f"allowed_phases count must be between 1 and {MAX_PROCEDURE_PHASES}")
        if not isinstance(self.allowed_phases, tuple):
            object.__setattr__(self, "allowed_phases", tuple(self.allowed_phases))
        if not isinstance(self.required_confirmations, tuple):
            object.__setattr__(self, "required_confirmations", tuple(self.required_confirmations))
        if not isinstance(self.allowed_tool_categories, tuple):
            object.__setattr__(self, "allowed_tool_categories", tuple(self.allowed_tool_categories))
        if not isinstance(self.metadata, MappingProxyType):
            object.__setattr__(self, "metadata", MappingProxyType(dict(self.metadata)))


@dataclass(frozen=True)
class ConfirmationRequest:
    """Structured human-in-the-loop confirmation request."""

    confirmation_id: str
    session_id: str
    epoch_id: int
    workflow_id: str
    target_phase: WorkflowPhase
    rationale: str
    timestamp_utc: str
    sequence_number: int

    def __post_init__(self) -> None:
        if not isinstance(self.confirmation_id, str) or not self.confirmation_id.strip():
            raise WorkflowValidationError("confirmation_id must be non-empty")
        if self.epoch_id < 0:
            raise WorkflowValidationError("epoch_id must be non-negative")
        if self.sequence_number < 0:
            raise WorkflowValidationError("sequence_number must be non-negative")


@dataclass(frozen=True)
class ConfirmationResponse:
    """Explicit structured response from clinical operator."""

    confirmation_id: str
    session_id: str
    epoch_id: int
    approved: bool
    operator_id: str
    timestamp_utc: str
    sequence_number: int

    def __post_init__(self) -> None:
        if not isinstance(self.confirmation_id, str) or not self.confirmation_id.strip():
            raise WorkflowValidationError("confirmation_id must be non-empty")
        if not isinstance(self.operator_id, str) or not self.operator_id.strip():
            raise WorkflowValidationError("operator_id must be non-empty")
        if self.epoch_id < 0:
            raise WorkflowValidationError("epoch_id must be non-negative")


@dataclass(frozen=True)
class SafetyInterlock:
    """Immutable safety interlock evaluation record."""

    interlock_id: str
    severity: InterlockSeverity
    condition_name: str
    status: bool  # True = Clear / Safe, False = Tripped / Unsafe
    reason: str
    source_service: str
    epoch_id: int
    session_id: str

    def __post_init__(self) -> None:
        if not isinstance(self.interlock_id, str) or not self.interlock_id.strip():
            raise WorkflowValidationError("interlock_id must be non-empty")
        if self.epoch_id < 0:
            raise WorkflowValidationError("epoch_id must be non-negative")


@dataclass(frozen=True)
class AnatomicalCheckpoint:
    """Immutable checkpoint validating anatomical targets and spatial tolerance."""

    checkpoint_id: str
    entity_id: str
    expected_relation: str
    max_tolerance_mm: float
    min_confidence: float
    max_uncertainty: float

    def __post_init__(self) -> None:
        if not isinstance(self.checkpoint_id, str) or not self.checkpoint_id.strip():
            raise WorkflowValidationError("checkpoint_id must be non-empty")
        if not (0.0 <= self.min_confidence <= 1.0) or not math.isfinite(self.min_confidence):
            raise WorkflowValidationError("min_confidence must be finite in [0.0, 1.0]")
        if not (0.0 <= self.max_uncertainty <= 1.0) or not math.isfinite(self.max_uncertainty):
            raise WorkflowValidationError("max_uncertainty must be finite in [0.0, 1.0]")
        if self.max_tolerance_mm <= 0.0 or not math.isfinite(self.max_tolerance_mm):
            raise WorkflowValidationError("max_tolerance_mm must be positive finite")


@dataclass(frozen=True)
class WorkflowToolAuthorizationDecision:
    """Immutable outcome of M10 tool authorization evaluation."""

    tool_id: str
    m07_safety_classification: ToolSafetyClassification
    status: WorkflowToolAuthorizationStatus
    workflow_phase: WorkflowPhase
    reason: str
    epoch_id: int
    session_id: str


@dataclass(frozen=True)
class WorkflowStateSnapshot:
    """Immutable snapshot representing the active workflow instance state."""

    workflow_id: str
    session_id: str
    epoch_id: int
    procedure_id: str
    current_phase: WorkflowPhase
    previous_phase: Optional[WorkflowPhase]
    transition_count: int
    sequence_number: int
    is_terminal: bool
    schema_version: str = WORKFLOW_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.transition_count < 0:
            raise WorkflowValidationError("transition_count must be non-negative")
        if self.sequence_number < 0:
            raise WorkflowValidationError("sequence_number must be non-negative")
