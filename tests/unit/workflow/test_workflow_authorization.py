# -*- coding: utf-8 -*-
"""Unit Tests for ToolAuthorizationGate, M07 Safety Reconciliation, and Categorical Blocks."""

from __future__ import annotations

import pytest

from holomed.tools.models import ToolSafetyClassification
from holomed.workflow.authorization import ToolAuthorizationGate
from holomed.workflow.exceptions import WorkflowAuthorizationError
from holomed.workflow.models import (
    WorkflowPhase,
    WorkflowToolAuthorizationStatus,
)


def test_m07_classification_mapping_integrity() -> None:
    """Verify all permitted tool authorizations map to valid frozen M07 classifications."""
    decision = ToolAuthorizationGate.evaluate_authorization(
        tool_id="anatomy.query",
        safety_classification=ToolSafetyClassification.READ_ONLY_INFORMATIVE,
        current_phase=WorkflowPhase.NAVIGATION,
        has_blocking_interlocks=False,
        epoch_id=1,
        session_id="sess_01",
    )
    assert decision.status == WorkflowToolAuthorizationStatus.PERMITTED
    assert decision.m07_safety_classification == ToolSafetyClassification.READ_ONLY_INFORMATIVE


def test_surgical_actuation_categorically_blocked() -> None:
    """Verify physical actuation, cutting, robotics, or energy delivery are categorically blocked."""
    surgical_intents = [
        ("robot.arm.move", False),
        ("tissue.cut", False),
        ("cauterize.apply", False),
        ("standard_tool", True),  # explicit is_surgical_actuation_intent flag
    ]

    for tool_name, flag in surgical_intents:
        decision = ToolAuthorizationGate.evaluate_authorization(
            tool_id=tool_name,
            safety_classification=ToolSafetyClassification.READ_ONLY_INFORMATIVE,
            current_phase=WorkflowPhase.INTERVENTION,
            has_blocking_interlocks=False,
            epoch_id=1,
            session_id="sess_01",
            is_surgical_actuation_intent=flag,
        )
        assert decision.status == WorkflowToolAuthorizationStatus.BLOCKED_CATEGORICAL


def test_visualization_adjustment_restricted_to_permitted_phases() -> None:
    """Verify VISUALIZATION_ADJUSTMENT is blocked during PATIENT_CONTEXT and permitted in NAVIGATION."""
    # In PATIENT_CONTEXT -> BLOCKED_PHASE
    d_ctx = ToolAuthorizationGate.evaluate_authorization(
        tool_id="viewport.adjust",
        safety_classification=ToolSafetyClassification.VISUALIZATION_ADJUSTMENT,
        current_phase=WorkflowPhase.PATIENT_CONTEXT,
        has_blocking_interlocks=False,
        epoch_id=1,
        session_id="sess_01",
    )
    assert d_ctx.status == WorkflowToolAuthorizationStatus.BLOCKED_PHASE

    # In NAVIGATION -> PERMITTED
    d_nav = ToolAuthorizationGate.evaluate_authorization(
        tool_id="viewport.adjust",
        safety_classification=ToolSafetyClassification.VISUALIZATION_ADJUSTMENT,
        current_phase=WorkflowPhase.NAVIGATION,
        has_blocking_interlocks=False,
        epoch_id=1,
        session_id="sess_01",
    )
    assert d_nav.status == WorkflowToolAuthorizationStatus.PERMITTED


def test_interlock_blocks_tool_authorization() -> None:
    """Verify active blocking interlock prevents tool invocation."""
    decision = ToolAuthorizationGate.evaluate_authorization(
        tool_id="anatomy.query",
        safety_classification=ToolSafetyClassification.READ_ONLY_INFORMATIVE,
        current_phase=WorkflowPhase.NAVIGATION,
        has_blocking_interlocks=True,
        epoch_id=1,
        session_id="sess_01",
    )
    assert decision.status == WorkflowToolAuthorizationStatus.BLOCKED_INTERLOCK


def test_unknown_classification_rejected() -> None:
    """Verify non-existent safety classifications raise WorkflowAuthorizationError."""
    with pytest.raises(WorkflowAuthorizationError):
        ToolAuthorizationGate.evaluate_authorization(
            tool_id="unknown.tool",
            safety_classification="DIAGNOSTIC_INTERACTIVE",  # type: ignore
            current_phase=WorkflowPhase.NAVIGATION,
            has_blocking_interlocks=False,
            epoch_id=1,
            session_id="sess_01",
        )
