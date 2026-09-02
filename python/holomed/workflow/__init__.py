# -*- coding: utf-8 -*-
"""HoloMed AI Clinical Workflow & Safety Interlock Foundation (M10)."""

from __future__ import annotations

from holomed.workflow.authorization import ToolAuthorizationGate
from holomed.workflow.checkpoints import AnatomicalCheckpointValidator
from holomed.workflow.confirmation import ConfirmationManager
from holomed.workflow.events import RecordingWorkflowEventSink
from holomed.workflow.exceptions import (
    WorkflowAuthorizationError,
    WorkflowCapacityError,
    WorkflowConfirmationError,
    WorkflowConfirmationRequiredError,
    WorkflowEpochMismatchError,
    WorkflowError,
    WorkflowLifecycleError,
    WorkflowPrerequisiteError,
    WorkflowRecoveryError,
    WorkflowResourceIntegrityError,
    WorkflowSafetyInterlockError,
    WorkflowSequenceError,
    WorkflowSessionError,
    WorkflowShutdownError,
    WorkflowTransitionError,
    WorkflowValidationError,
)
from holomed.workflow.interlocks import SafetyInterlockEngine
from holomed.workflow.models import (
    MAX_ACTIVE_WORKFLOWS,
    MAX_PENDING_CONFIRMATIONS,
    MAX_PROCEDURE_PHASES,
    MAX_REGISTERED_CHECKPOINTS,
    MAX_TRANSITIONS_PER_WORKFLOW,
    MAX_WORKFLOW_EVENT_LOG,
    WORKFLOW_SCHEMA_VERSION,
    AnatomicalCheckpoint,
    ConfirmationRequest,
    ConfirmationResponse,
    InterlockSeverity,
    ProcedureDefinition,
    SafetyInterlock,
    WorkflowHealthStatus,
    WorkflowPhase,
    WorkflowResumptionRequest,
    WorkflowStateSnapshot,
    WorkflowToolAuthorizationDecision,
    WorkflowToolAuthorizationStatus,
)
from holomed.workflow.procedure import (
    STANDARD_ALLOWED_TOOL_CATEGORIES,
    STANDARD_CONFIRMATION_PHASES,
    STANDARD_PROCEDURE_PHASES,
    STANDARD_SURGICAL_GUIDANCE_V1,
    validate_procedure_definition,
)
from holomed.workflow.recovery import WorkflowRecoveryEngine
from holomed.workflow.serialization import (
    normalize_workflow_value,
    serialize_canonical_workflow_bytes,
)
from holomed.workflow.service import WorkflowService
from holomed.workflow.state_machine import WorkflowStateMachine

__all__ = [
    # Exceptions
    "WorkflowError",
    "WorkflowLifecycleError",
    "WorkflowValidationError",
    "WorkflowCapacityError",
    "WorkflowResourceIntegrityError",
    "WorkflowShutdownError",
    "WorkflowEpochMismatchError",
    "WorkflowSessionError",
    "WorkflowSequenceError",
    "WorkflowTransitionError",
    "WorkflowPrerequisiteError",
    "WorkflowConfirmationRequiredError",
    "WorkflowConfirmationError",
    "WorkflowSafetyInterlockError",
    "WorkflowAuthorizationError",
    "WorkflowRecoveryError",
    # Enums
    "WorkflowPhase",
    "InterlockSeverity",
    "WorkflowToolAuthorizationStatus",
    "WorkflowHealthStatus",
    # Models
    "ProcedureDefinition",
    "ConfirmationRequest",
    "ConfirmationResponse",
    "SafetyInterlock",
    "AnatomicalCheckpoint",
    "WorkflowToolAuthorizationDecision",
    "WorkflowStateSnapshot",
    "WorkflowResumptionRequest",
    # Constants
    "WORKFLOW_SCHEMA_VERSION",
    "MAX_ACTIVE_WORKFLOWS",
    "MAX_TRANSITIONS_PER_WORKFLOW",
    "MAX_PENDING_CONFIRMATIONS",
    "MAX_REGISTERED_CHECKPOINTS",
    "MAX_WORKFLOW_EVENT_LOG",
    "MAX_PROCEDURE_PHASES",
    # Procedures
    "STANDARD_PROCEDURE_PHASES",
    "STANDARD_CONFIRMATION_PHASES",
    "STANDARD_ALLOWED_TOOL_CATEGORIES",
    "STANDARD_SURGICAL_GUIDANCE_V1",
    "validate_procedure_definition",
    # Components
    "WorkflowStateMachine",
    "ConfirmationManager",
    "SafetyInterlockEngine",
    "AnatomicalCheckpointValidator",
    "ToolAuthorizationGate",
    "WorkflowRecoveryEngine",
    "RecordingWorkflowEventSink",
    "WorkflowService",
    # Serialization
    "normalize_workflow_value",
    "serialize_canonical_workflow_bytes",
]
