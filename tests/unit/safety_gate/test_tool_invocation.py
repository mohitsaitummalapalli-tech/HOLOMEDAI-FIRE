# -*- coding: utf-8 -*-
"""Unit Tests for SafetyGateAction.TOOL_INVOCATION Precedence & Gating."""

from typing import Any
from unittest.mock import MagicMock
import pytest

from holomed.registration.models import RegistrationState
from holomed.safety_gate.evaluator import SafetyGateEvaluator
from holomed.safety_gate.models import (
    GateDecision,
    GateReasonCode,
    GateRequest,
    GateSeverity,
    SafetyGateAction,
)
from holomed.tools.models import ToolSafetyClassification
from holomed.workflow.models import WorkflowPhase


def make_tool_request(
    tool_id: str = "anatomy.query_organ",
    classification: ToolSafetyClassification = ToolSafetyClassification.READ_ONLY_INFORMATIVE,
    session_id: str = "session-01",
    seq: int = 1,
) -> GateRequest:
    return GateRequest(
        session_id=session_id,
        action=SafetyGateAction.TOOL_INVOCATION,
        sequence_number=seq,
        now_utc="2026-09-02T12:00:00.000Z",
        tool_id=tool_id,
        safety_classification=classification,
    )


def test_tool_invocation_nominal_permitted(
    mock_workflow_service: Any,
    mock_registration_service: Any,
    mock_navigation_service: Any,
    mock_proximity_service: Any,
    mock_drift_service: Any,
    mock_recovery_service: Any,
) -> None:
    req = make_tool_request()
    record = SafetyGateEvaluator.evaluate(
        request=req,
        epoch_id=1,
        workflow_service=mock_workflow_service,
        registration_service=mock_registration_service,
        navigation_service=mock_navigation_service,
        proximity_service=mock_proximity_service,
        drift_service=mock_drift_service,
        recovery_service=mock_recovery_service,
    )

    assert record.decision == GateDecision.PERMITTED_CLEAR
    assert record.severity == GateSeverity.NONE
    assert record.action == SafetyGateAction.TOOL_INVOCATION


def test_tool_invocation_critical_breach_denied(
    mock_workflow_service: Any,
    mock_registration_service: Any,
    mock_navigation_service: Any,
    mock_proximity_service: Any,
    mock_drift_service: Any,
    mock_recovery_service: Any,
) -> None:
    mock_proximity_service.get_proximity_status.return_value.state.value = "CRITICAL_BREACH"

    req = make_tool_request()
    record = SafetyGateEvaluator.evaluate(
        request=req,
        epoch_id=1,
        workflow_service=mock_workflow_service,
        registration_service=mock_registration_service,
        navigation_service=mock_navigation_service,
        proximity_service=mock_proximity_service,
        drift_service=mock_drift_service,
        recovery_service=mock_recovery_service,
    )

    assert record.decision == GateDecision.DENIED_CRITICAL
    assert record.severity == GateSeverity.CRITICAL
    assert record.reason_code == GateReasonCode.CRITICAL_EXCLUSION_ZONE_BREACH


def test_tool_invocation_drift_exceeded_denied(
    mock_workflow_service: Any,
    mock_registration_service: Any,
    mock_navigation_service: Any,
    mock_proximity_service: Any,
    mock_drift_service: Any,
    mock_recovery_service: Any,
) -> None:
    mock_drift_service.get_drift_status.return_value.state.value = "DRIFT_EXCEEDED"

    req = make_tool_request()
    record = SafetyGateEvaluator.evaluate(
        request=req,
        epoch_id=1,
        workflow_service=mock_workflow_service,
        registration_service=mock_registration_service,
        navigation_service=mock_navigation_service,
        proximity_service=mock_proximity_service,
        drift_service=mock_drift_service,
        recovery_service=mock_recovery_service,
    )

    assert record.decision == GateDecision.DENIED_INTERLOCKED
    assert record.severity == GateSeverity.BLOCKING
    assert record.reason_code == GateReasonCode.LANDMARK_DRIFT_EXCEEDED


def test_tool_invocation_recovery_phase_telemetry_permitted(
    mock_workflow_service: Any,
    mock_registration_service: Any,
    mock_navigation_service: Any,
    mock_proximity_service: Any,
    mock_drift_service: Any,
    mock_recovery_service: Any,
) -> None:
    mock_workflow_service.get_workflow_state.return_value.current_phase = WorkflowPhase.RECOVERY_REQUIRED
    mock_registration_service.get_registration.return_value.state = RegistrationState.INVALIDATED

    # Telemetry request
    req = make_tool_request(
        tool_id="system.capture_telemetry",
        classification=ToolSafetyClassification.TELEMETRY_RECORDING,
    )
    record = SafetyGateEvaluator.evaluate(
        request=req,
        epoch_id=1,
        workflow_service=mock_workflow_service,
        registration_service=mock_registration_service,
        navigation_service=mock_navigation_service,
        proximity_service=mock_proximity_service,
        drift_service=mock_drift_service,
        recovery_service=mock_recovery_service,
    )

    assert record.decision == GateDecision.PERMITTED_CLEAR
    assert record.severity == GateSeverity.NONE


def test_tool_invocation_recovery_phase_non_telemetry_denied(
    mock_workflow_service: Any,
    mock_registration_service: Any,
    mock_navigation_service: Any,
    mock_proximity_service: Any,
    mock_drift_service: Any,
    mock_recovery_service: Any,
) -> None:
    mock_workflow_service.get_workflow_state.return_value.current_phase = WorkflowPhase.RECOVERY_REQUIRED

    # Informative request
    req = make_tool_request(
        tool_id="anatomy.query_organ",
        classification=ToolSafetyClassification.READ_ONLY_INFORMATIVE,
    )
    record = SafetyGateEvaluator.evaluate(
        request=req,
        epoch_id=1,
        workflow_service=mock_workflow_service,
        registration_service=mock_registration_service,
        navigation_service=mock_navigation_service,
        proximity_service=mock_proximity_service,
        drift_service=mock_drift_service,
        recovery_service=mock_recovery_service,
    )

    assert record.decision == GateDecision.DENIED_INTERLOCKED
    assert record.severity == GateSeverity.BLOCKING
    assert record.reason_code == GateReasonCode.WORKFLOW_PHASE_BLOCKED
