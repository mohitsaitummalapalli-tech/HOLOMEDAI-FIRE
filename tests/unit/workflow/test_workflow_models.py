# -*- coding: utf-8 -*-
"""Unit Tests for M10 Workflow Data Models, Checkpoints, and Invariants."""

from __future__ import annotations

import pytest

from holomed.tools.models import ToolCategory
from holomed.workflow.exceptions import WorkflowValidationError
from holomed.workflow.models import (
    AnatomicalCheckpoint,
    ConfirmationRequest,
    ConfirmationResponse,
    InterlockSeverity,
    ProcedureDefinition,
    SafetyInterlock,
    WorkflowPhase,
    WorkflowStateSnapshot,
)


def test_procedure_definition_validation() -> None:
    """Verify ProcedureDefinition validates syntax, bounds, and immutability."""
    proc = ProcedureDefinition(
        procedure_id="proc_valid_01",
        version="1.0",
        name="Valid Procedure",
        allowed_phases=(WorkflowPhase.PATIENT_CONTEXT, WorkflowPhase.COMPLETION),
        required_confirmations=(WorkflowPhase.COMPLETION,),
        allowed_tool_categories=(ToolCategory.QUERY_ANATOMY,),
    )
    assert proc.procedure_id == "proc_valid_01"
    assert proc.schema_version == "1.0"

    # Invalid ID
    with pytest.raises(WorkflowValidationError):
        ProcedureDefinition(
            procedure_id="invalid/id",
            version="1.0",
            name="Valid Procedure",
            allowed_phases=(WorkflowPhase.PATIENT_CONTEXT,),
            required_confirmations=(),
            allowed_tool_categories=(),
        )

    # Invalid semantic version
    with pytest.raises(WorkflowValidationError):
        ProcedureDefinition(
            procedure_id="proc_1",
            version="v1.0",
            name="Valid Procedure",
            allowed_phases=(WorkflowPhase.PATIENT_CONTEXT,),
            required_confirmations=(),
            allowed_tool_categories=(),
        )


def test_workflow_phase_enum_integrity() -> None:
    """Verify all 10 authoritative workflow phases exist in enum."""
    expected = {
        "PATIENT_CONTEXT",
        "PRE_PROCEDURE_PLANNING",
        "REGISTRATION",
        "SAFETY_TIMEOUT",
        "NAVIGATION",
        "INTERVENTION",
        "VERIFICATION",
        "COMPLETION",
        "ABORTED",
        "RECOVERY_REQUIRED",
    }
    actual = {p.value for p in WorkflowPhase}
    assert actual == expected


def test_anatomical_checkpoint_validation() -> None:
    """Verify AnatomicalCheckpoint enforces confidence, uncertainty, and tolerance bounds."""
    chk = AnatomicalCheckpoint(
        checkpoint_id="chk_femur_01",
        entity_id="femur_head",
        expected_relation="within_bounds",
        max_tolerance_mm=2.5,
        min_confidence=0.85,
        max_uncertainty=0.15,
    )
    assert chk.max_tolerance_mm == 2.5

    # Negative tolerance rejected
    with pytest.raises(WorkflowValidationError):
        AnatomicalCheckpoint(
            checkpoint_id="chk_invalid",
            entity_id="femur_head",
            expected_relation="rel",
            max_tolerance_mm=-1.0,
            min_confidence=0.8,
            max_uncertainty=0.2,
        )

    # Invalid confidence range
    with pytest.raises(WorkflowValidationError):
        AnatomicalCheckpoint(
            checkpoint_id="chk_invalid",
            entity_id="femur_head",
            expected_relation="rel",
            max_tolerance_mm=1.0,
            min_confidence=1.5,
            max_uncertainty=0.2,
        )
