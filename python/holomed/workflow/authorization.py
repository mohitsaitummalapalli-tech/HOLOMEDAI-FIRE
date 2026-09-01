# -*- coding: utf-8 -*-
"""Tool Authorization Gate and M07 Safety Reconciliation for M10."""

from __future__ import annotations

from typing import Optional

from holomed.tools.models import ToolSafetyClassification
from holomed.workflow.exceptions import WorkflowAuthorizationError
from holomed.workflow.models import (
    WorkflowPhase,
    WorkflowToolAuthorizationDecision,
    WorkflowToolAuthorizationStatus,
)

ALLOWED_M07_CLASSIFICATIONS: frozenset[ToolSafetyClassification] = frozenset([
    ToolSafetyClassification.READ_ONLY_INFORMATIVE,
    ToolSafetyClassification.VISUALIZATION_ADJUSTMENT,
    ToolSafetyClassification.TELEMETRY_RECORDING,
])

VISUALIZATION_PERMITTED_PHASES: frozenset[WorkflowPhase] = frozenset([
    WorkflowPhase.PRE_PROCEDURE_PLANNING,
    WorkflowPhase.REGISTRATION,
    WorkflowPhase.NAVIGATION,
    WorkflowPhase.INTERVENTION,
    WorkflowPhase.VERIFICATION,
])

SURGICAL_ACTUATION_KEYWORDS: tuple[str, ...] = (
    "cut",
    "cutting",
    "ablate",
    "resect",
    "cauterize",
    "energy",
    "robot",
    "actuate",
    "hardware_control",
    "drill",
)


class ToolAuthorizationGate:
    """Evaluates proposed tool invocations against workflow phase and safety interlocks."""

    @staticmethod
    def evaluate_authorization(
        tool_id: str,
        safety_classification: ToolSafetyClassification,
        current_phase: WorkflowPhase,
        has_blocking_interlocks: bool,
        epoch_id: int,
        session_id: str,
        is_surgical_actuation_intent: bool = False,
    ) -> WorkflowToolAuthorizationDecision:
        """Evaluate authorization mapping onto frozen M07 ToolSafetyClassification."""
        # 1. Categorical Block for Surgical / Physical Actuation
        if is_surgical_actuation_intent or any(kw in tool_id.lower() for kw in SURGICAL_ACTUATION_KEYWORDS):
            return WorkflowToolAuthorizationDecision(
                tool_id=tool_id,
                m07_safety_classification=safety_classification,
                status=WorkflowToolAuthorizationStatus.BLOCKED_CATEGORICAL,
                workflow_phase=current_phase,
                reason="Physical surgical actuation, robotics, or tissue cutting is categorically forbidden",
                epoch_id=epoch_id,
                session_id=session_id,
            )

        # 2. Validate classification belongs strictly to M07 frozen enum
        if safety_classification not in ALLOWED_M07_CLASSIFICATIONS:
            raise WorkflowAuthorizationError(
                f"Unknown or incompatible ToolSafetyClassification: {safety_classification!r}"
            )

        # 3. Interlock Block
        if has_blocking_interlocks:
            return WorkflowToolAuthorizationDecision(
                tool_id=tool_id,
                m07_safety_classification=safety_classification,
                status=WorkflowToolAuthorizationStatus.BLOCKED_INTERLOCK,
                workflow_phase=current_phase,
                reason="Active blocking safety interlock prevents tool invocation",
                epoch_id=epoch_id,
                session_id=session_id,
            )

        # 4. Phase-Specific Evaluation
        if safety_classification == ToolSafetyClassification.VISUALIZATION_ADJUSTMENT:
            if current_phase not in VISUALIZATION_PERMITTED_PHASES:
                return WorkflowToolAuthorizationDecision(
                    tool_id=tool_id,
                    m07_safety_classification=safety_classification,
                    status=WorkflowToolAuthorizationStatus.BLOCKED_PHASE,
                    workflow_phase=current_phase,
                    reason=f"Visualization adjustment not permitted during phase {current_phase.name}",
                    epoch_id=epoch_id,
                    session_id=session_id,
                )

        if current_phase in (WorkflowPhase.ABORTED, WorkflowPhase.RECOVERY_REQUIRED):
            if safety_classification != ToolSafetyClassification.TELEMETRY_RECORDING:
                return WorkflowToolAuthorizationDecision(
                    tool_id=tool_id,
                    m07_safety_classification=safety_classification,
                    status=WorkflowToolAuthorizationStatus.BLOCKED_PHASE,
                    workflow_phase=current_phase,
                    reason=f"Only telemetry recording permitted in {current_phase.name}",
                    epoch_id=epoch_id,
                    session_id=session_id,
                )

        return WorkflowToolAuthorizationDecision(
            tool_id=tool_id,
            m07_safety_classification=safety_classification,
            status=WorkflowToolAuthorizationStatus.PERMITTED,
            workflow_phase=current_phase,
            reason="Tool invocation authorized",
            epoch_id=epoch_id,
            session_id=session_id,
        )
