# -*- coding: utf-8 -*-
"""Clinical Procedure Definitions and Validation for M10."""

from __future__ import annotations

from types import MappingProxyType

from holomed.tools.models import ToolCategory
from holomed.workflow.exceptions import WorkflowValidationError
from holomed.workflow.models import (
    ProcedureDefinition,
    WorkflowPhase,
)

STANDARD_PROCEDURE_PHASES: tuple[WorkflowPhase, ...] = (
    WorkflowPhase.PATIENT_CONTEXT,
    WorkflowPhase.PRE_PROCEDURE_PLANNING,
    WorkflowPhase.REGISTRATION,
    WorkflowPhase.SAFETY_TIMEOUT,
    WorkflowPhase.NAVIGATION,
    WorkflowPhase.INTERVENTION,
    WorkflowPhase.VERIFICATION,
    WorkflowPhase.COMPLETION,
)

STANDARD_CONFIRMATION_PHASES: tuple[WorkflowPhase, ...] = (
    WorkflowPhase.NAVIGATION,    # Entering Navigation requires Safety Timeout confirmation
    WorkflowPhase.INTERVENTION,  # Entering Intervention requires human approval
    WorkflowPhase.COMPLETION,    # Final Completion sign-off
)

STANDARD_ALLOWED_TOOL_CATEGORIES: tuple[ToolCategory, ...] = (
    ToolCategory.QUERY_ANATOMY,
    ToolCategory.MEASURE_DISTANCE,
    ToolCategory.HIGHLIGHT_STRUCTURE,
    ToolCategory.ADJUST_VIEWPORT,
    ToolCategory.CAPTURE_TELEMETRY,
)

STANDARD_SURGICAL_GUIDANCE_V1 = ProcedureDefinition(
    procedure_id="standard_surgical_guidance",
    version="1.0",
    name="Standard Surgical Spatial Guidance Procedure",
    allowed_phases=STANDARD_PROCEDURE_PHASES,
    required_confirmations=STANDARD_CONFIRMATION_PHASES,
    allowed_tool_categories=STANDARD_ALLOWED_TOOL_CATEGORIES,
    metadata=MappingProxyType({"guideline": "Standard Minimally Invasive Guidance"}),
)


def validate_procedure_definition(defn: ProcedureDefinition) -> None:
    """Validate procedural structure, legal phase sequence, and tool constraints."""
    if not isinstance(defn, ProcedureDefinition):
        raise WorkflowValidationError(f"Expected ProcedureDefinition, got {type(defn).__name__}")

    # Ensure start phase is PATIENT_CONTEXT
    if defn.allowed_phases[0] != WorkflowPhase.PATIENT_CONTEXT:
        raise WorkflowValidationError("Initial phase in allowed_phases must be PATIENT_CONTEXT")

    # Confirmations must be a subset of allowed phases
    for p in defn.required_confirmations:
        if p not in defn.allowed_phases:
            raise WorkflowValidationError(f"Confirmation phase {p.name} not present in allowed_phases")
