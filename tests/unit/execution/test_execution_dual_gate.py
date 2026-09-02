# -*- coding: utf-8 -*-
"""Unit Tests for M19 Dual-Gate Ordering, Short-Circuiting, and Final Safety Authority."""

from __future__ import annotations

from unittest.mock import MagicMock
import pytest

from holomed.execution.models import ExecutionStatus
from holomed.execution.service import NavigationExecutionService
from holomed.navigation.exceptions import NavigationRegistrationMismatchError
from holomed.safety_gate.models import (
    GateDecision,
    GateReasonCode,
    GateSeverity,
    GateStatusRecord,
    SafetyGateAction,
)
from holomed.tools.models import ToolSafetyClassification
from holomed.workflow.models import (
    WorkflowPhase,
    WorkflowToolAuthorizationDecision,
    WorkflowToolAuthorizationStatus,
)
from tests.unit.execution.conftest import make_execution_request


def _make_started_service(
    mock_dispatcher,
    mock_safety_gate_service,
    mock_workflow_service,
    mock_navigation_service,
    mock_persistence_service,
    secret_filter,
    logger,
    runtime_context,
    mock_recovery_service=None,
) -> NavigationExecutionService:
    svc = NavigationExecutionService(
        dispatcher=mock_dispatcher,
        safety_gate_service=mock_safety_gate_service,
        workflow_service=mock_workflow_service,
        navigation_service=mock_navigation_service,
        persistence_service=mock_persistence_service,
        recovery_service=mock_recovery_service,
        secret_filter=secret_filter,
        logger=logger,
    )
    svc.initialize(runtime_context)
    svc.start()
    return svc


class TestDualGateOrdering:
    """Tests confirming dual-gate evaluation ordering and short-circuiting."""

    def test_case_a_m18_denied_short_circuits_m10_and_m14(
        self,
        mock_dispatcher,
        mock_safety_gate_service,
        mock_workflow_service,
        mock_navigation_service,
        mock_persistence_service,
        secret_filter,
        logger,
        runtime_context,
    ) -> None:
        """Case A: M18 DENIED -> M10 MUST NOT be invoked, M14 MUST NOT execute."""
        mock_safety_gate_service.evaluate.return_value = GateStatusRecord(
            session_id="session-01",
            decision=GateDecision.DENIED_INTERLOCKED,
            severity=GateSeverity.BLOCKING,
            reason_code=GateReasonCode.LANDMARK_DRIFT_EXCEEDED,
            action=SafetyGateAction.TOOL_NAVIGATION,
            sequence_number=1,
            subsystem_snapshots=(),
            evaluated_at_utc="2025-01-01T12:00:00Z",
        )
        svc = _make_started_service(
            mock_dispatcher, mock_safety_gate_service, mock_workflow_service,
            mock_navigation_service, mock_persistence_service, secret_filter,
            logger, runtime_context,
        )

        mock_workflow_service.authorize_tool.reset_mock()
        mock_navigation_service.submit_pose.reset_mock()
        mock_navigation_service.evaluate.reset_mock()

        req = make_execution_request()
        res = svc.execute_navigation(req)

        assert res.execution_status == ExecutionStatus.BLOCKED_SAFETY_GATE
        assert res.gate_decision == GateDecision.DENIED_INTERLOCKED
        assert res.gate_reason_code == GateReasonCode.LANDMARK_DRIFT_EXCEEDED

        # Proved: M10 and M14 were NEVER invoked!
        mock_workflow_service.authorize_tool.assert_not_called()
        mock_navigation_service.submit_pose.assert_not_called()
        mock_navigation_service.evaluate.assert_not_called()

    def test_case_b_m18_permitted_m10_denied_short_circuits_m14(
        self,
        mock_dispatcher,
        mock_safety_gate_service,
        mock_workflow_service,
        mock_navigation_service,
        mock_persistence_service,
        secret_filter,
        logger,
        runtime_context,
    ) -> None:
        """Case B: M18 permitted, M10 denied -> M14 MUST NOT execute."""
        mock_safety_gate_service.evaluate.return_value = GateStatusRecord(
            session_id="session-01",
            decision=GateDecision.PERMITTED_CLEAR,
            severity=GateSeverity.NONE,
            reason_code=GateReasonCode.NONE,
            action=SafetyGateAction.TOOL_NAVIGATION,
            sequence_number=1,
            subsystem_snapshots=(),
            evaluated_at_utc="2025-01-01T12:00:00Z",
        )
        mock_workflow_service.get_workflow_state.return_value.current_phase.value = "ABORTED"

        svc = _make_started_service(
            mock_dispatcher, mock_safety_gate_service, mock_workflow_service,
            mock_navigation_service, mock_persistence_service, secret_filter,
            logger, runtime_context,
        )

        mock_navigation_service.submit_pose.reset_mock()
        mock_navigation_service.evaluate.reset_mock()

        req = make_execution_request()
        res = svc.execute_navigation(req)

        assert res.execution_status == ExecutionStatus.BLOCKED_WORKFLOW

        # Proved: M14 was NEVER executed!
        mock_navigation_service.submit_pose.assert_not_called()
        mock_navigation_service.evaluate.assert_not_called()

    def test_case_c_d_m14_failure_preserved_and_not_masked_by_earlier_permits(
        self,
        mock_dispatcher,
        mock_safety_gate_service,
        mock_workflow_service,
        mock_navigation_service,
        mock_persistence_service,
        secret_filter,
        logger,
        runtime_context,
    ) -> None:
        """Case C/D: M18 and M10 permit, but M14 evaluate throws -> M19 returns failure and does NOT preserve clear."""
        mock_safety_gate_service.evaluate.return_value = GateStatusRecord(
            session_id="session-01",
            decision=GateDecision.PERMITTED_CLEAR,
            severity=GateSeverity.NONE,
            reason_code=GateReasonCode.NONE,
            action=SafetyGateAction.TOOL_NAVIGATION,
            sequence_number=1,
            subsystem_snapshots=(),
            evaluated_at_utc="2025-01-01T12:00:00Z",
        )
        mock_navigation_service.evaluate.side_effect = NavigationRegistrationMismatchError(
            "Registration unverified; navigation interlocked"
        )

        svc = _make_started_service(
            mock_dispatcher, mock_safety_gate_service, mock_workflow_service,
            mock_navigation_service, mock_persistence_service, secret_filter,
            logger, runtime_context,
        )

        req = make_execution_request()
        res = svc.execute_navigation(req)

        assert res.execution_status == ExecutionStatus.FAILED_NAVIGATION_GEOMETRY
        assert "Registration unverified" in str(res.error_message)

    def test_case_e_m18_caution_results_in_executed_with_caution(
        self,
        mock_dispatcher,
        mock_safety_gate_service,
        mock_workflow_service,
        mock_navigation_service,
        mock_persistence_service,
        secret_filter,
        logger,
        runtime_context,
    ) -> None:
        """Case E: M18 caution, M10 permit, M14 success -> EXECUTED_WITH_CAUTION."""
        mock_safety_gate_service.evaluate.return_value = GateStatusRecord(
            session_id="session-01",
            decision=GateDecision.PERMITTED_WITH_CAUTION,
            severity=GateSeverity.WARNING,
            reason_code=GateReasonCode.APPROACHING_MARGIN,
            action=SafetyGateAction.TOOL_NAVIGATION,
            sequence_number=1,
            subsystem_snapshots=(),
            evaluated_at_utc="2025-01-01T12:00:00Z",
        )

        svc = _make_started_service(
            mock_dispatcher, mock_safety_gate_service, mock_workflow_service,
            mock_navigation_service, mock_persistence_service, secret_filter,
            logger, runtime_context,
        )

        req = make_execution_request()
        res = svc.execute_navigation(req)

        assert res.execution_status == ExecutionStatus.EXECUTED_WITH_CAUTION
        assert res.gate_decision == GateDecision.PERMITTED_WITH_CAUTION
        assert res.gate_reason_code == GateReasonCode.APPROACHING_MARGIN

    def test_m14_failure_is_not_masked_when_m18_and_m10_were_clear(
        self,
        mock_dispatcher,
        mock_safety_gate_service,
        mock_workflow_service,
        mock_navigation_service,
        mock_persistence_service,
        secret_filter,
        logger,
        runtime_context,
    ) -> None:
        """CRITICAL: If M18 is PERMITTED_CLEAR and M10 is PERMITTED, but M14 submit_pose fails,
        M19 MUST return FAILED_NAVIGATION_GEOMETRY and NOT claim successful execution.
        """
        mock_safety_gate_service.evaluate.return_value = GateStatusRecord(
            session_id="session-01",
            decision=GateDecision.PERMITTED_CLEAR,
            severity=GateSeverity.NONE,
            reason_code=GateReasonCode.NONE,
            action=SafetyGateAction.TOOL_NAVIGATION,
            sequence_number=1,
            subsystem_snapshots=(),
            evaluated_at_utc="2025-01-01T12:00:00Z",
        )
        mock_navigation_service.submit_pose.side_effect = Exception("Pose sequence corrupted")

        svc = _make_started_service(
            mock_dispatcher, mock_safety_gate_service, mock_workflow_service,
            mock_navigation_service, mock_persistence_service, secret_filter,
            logger, runtime_context,
        )

        req = make_execution_request()
        res = svc.execute_navigation(req)

        assert res.execution_status == ExecutionStatus.FAILED_NAVIGATION_GEOMETRY
        assert "Pose sequence corrupted" in str(res.error_message)


class TestDualGateWorkflowToolAuthorization:
    """Tests for M10 tool safety classification and phase authorization."""

    def test_m10_tool_authorization_blocked(
        self,
        mock_dispatcher,
        mock_safety_gate_service,
        mock_workflow_service,
        mock_navigation_service,
        mock_persistence_service,
        secret_filter,
        logger,
        runtime_context,
    ) -> None:
        mock_safety_gate_service.evaluate.return_value = GateStatusRecord(
            session_id="session-01",
            decision=GateDecision.PERMITTED_CLEAR,
            severity=GateSeverity.NONE,
            reason_code=GateReasonCode.NONE,
            action=SafetyGateAction.TOOL_NAVIGATION,
            sequence_number=1,
            subsystem_snapshots=(),
            evaluated_at_utc="2025-01-01T12:00:00Z",
        )
        mock_workflow_service.authorize_tool.return_value = WorkflowToolAuthorizationDecision(
            tool_id="inst-01",
            m07_safety_classification=ToolSafetyClassification.READ_ONLY_INFORMATIVE,
            status=WorkflowToolAuthorizationStatus.BLOCKED_PHASE,
            workflow_phase=WorkflowPhase.SAFETY_TIMEOUT,
            reason="Tool requires NAVIGATION phase",
            epoch_id=1,
            session_id="session-01",
        )

        svc = _make_started_service(
            mock_dispatcher, mock_safety_gate_service, mock_workflow_service,
            mock_navigation_service, mock_persistence_service, secret_filter,
            logger, runtime_context,
        )

        req = make_execution_request()
        object.__setattr__(req, "tool_safety_classification", ToolSafetyClassification.READ_ONLY_INFORMATIVE)
        res = svc.execute_navigation(req)

        assert res.execution_status == ExecutionStatus.BLOCKED_WORKFLOW
        assert res.workflow_status == WorkflowToolAuthorizationStatus.BLOCKED_PHASE


class TestTrajectoryAlignmentDualGate:
    """Tests for TRAJECTORY_ALIGNMENT dual-gate ordering and short-circuiting (Tests E, F, G)."""

    def test_case_e_trajectory_alignment_m18_denied_short_circuits_m10_and_m14(
        self,
        mock_dispatcher,
        mock_safety_gate_service,
        mock_workflow_service,
        mock_navigation_service,
        mock_persistence_service,
        secret_filter,
        logger,
        runtime_context,
    ) -> None:
        """Test E: M18 DENIED -> M10 and M14 skipped."""
        mock_safety_gate_service.evaluate.return_value = GateStatusRecord(
            session_id="session-01",
            decision=GateDecision.DENIED_INTERLOCKED,
            severity=GateSeverity.BLOCKING,
            reason_code=GateReasonCode.CRITICAL_EXCLUSION_ZONE_BREACH,
            action=SafetyGateAction.TRAJECTORY_ALIGNMENT,
            sequence_number=1,
            subsystem_snapshots=(),
            evaluated_at_utc="2025-01-01T12:00:00Z",
        )
        svc = _make_started_service(
            mock_dispatcher, mock_safety_gate_service, mock_workflow_service,
            mock_navigation_service, mock_persistence_service, secret_filter,
            logger, runtime_context,
        )

        mock_workflow_service.authorize_tool.reset_mock()
        mock_navigation_service.bind_trajectory.reset_mock()

        from tests.unit.execution.conftest import make_trajectory_binding_request
        req = make_trajectory_binding_request()
        res = svc.execute_trajectory_binding(req)

        assert res.execution_status == ExecutionStatus.BLOCKED_SAFETY_GATE
        assert res.gate_decision == GateDecision.DENIED_INTERLOCKED
        mock_workflow_service.authorize_tool.assert_not_called()
        mock_navigation_service.bind_trajectory.assert_not_called()

    def test_case_f_trajectory_alignment_m18_permit_m10_denied_short_circuits_m14(
        self,
        mock_dispatcher,
        mock_safety_gate_service,
        mock_workflow_service,
        mock_navigation_service,
        mock_persistence_service,
        secret_filter,
        logger,
        runtime_context,
    ) -> None:
        """Test F: M18 PERMIT + M10 DENY -> M14 bind_trajectory skipped."""
        mock_safety_gate_service.evaluate.return_value = GateStatusRecord(
            session_id="session-01",
            decision=GateDecision.PERMITTED_CLEAR,
            severity=GateSeverity.NONE,
            reason_code=GateReasonCode.NONE,
            action=SafetyGateAction.TRAJECTORY_ALIGNMENT,
            sequence_number=1,
            subsystem_snapshots=(),
            evaluated_at_utc="2025-01-01T12:00:00Z",
        )
        mock_workflow_service.authorize_tool.return_value = WorkflowToolAuthorizationDecision(
            tool_id="traj-01",
            m07_safety_classification=ToolSafetyClassification.VISUALIZATION_ADJUSTMENT,
            status=WorkflowToolAuthorizationStatus.BLOCKED_PHASE,
            workflow_phase=WorkflowPhase.PATIENT_CONTEXT,
            reason="Trajectory alignment not permitted in PATIENT_CONTEXT",
            epoch_id=1,
            session_id="session-01",
        )
        svc = _make_started_service(
            mock_dispatcher, mock_safety_gate_service, mock_workflow_service,
            mock_navigation_service, mock_persistence_service, secret_filter,
            logger, runtime_context,
        )

        mock_navigation_service.bind_trajectory.reset_mock()

        from tests.unit.execution.conftest import make_trajectory_binding_request
        req = make_trajectory_binding_request()
        res = svc.execute_trajectory_binding(req)

        assert res.execution_status == ExecutionStatus.BLOCKED_WORKFLOW
        assert res.workflow_status == WorkflowToolAuthorizationStatus.BLOCKED_PHASE
        mock_navigation_service.bind_trajectory.assert_not_called()

    def test_case_g_trajectory_alignment_m18_permit_m10_permit_executes_m14(
        self,
        mock_dispatcher,
        mock_safety_gate_service,
        mock_workflow_service,
        mock_navigation_service,
        mock_persistence_service,
        secret_filter,
        logger,
        runtime_context,
    ) -> None:
        """Test G: M18 PERMIT + M10 PERMIT -> M14 bind_trajectory executed -> success."""
        mock_safety_gate_service.evaluate.return_value = GateStatusRecord(
            session_id="session-01",
            decision=GateDecision.PERMITTED_CLEAR,
            severity=GateSeverity.NONE,
            reason_code=GateReasonCode.NONE,
            action=SafetyGateAction.TRAJECTORY_ALIGNMENT,
            sequence_number=1,
            subsystem_snapshots=(),
            evaluated_at_utc="2025-01-01T12:00:00Z",
        )
        mock_workflow_service.authorize_tool.return_value = WorkflowToolAuthorizationDecision(
            tool_id="traj-01",
            m07_safety_classification=ToolSafetyClassification.VISUALIZATION_ADJUSTMENT,
            status=WorkflowToolAuthorizationStatus.PERMITTED,
            workflow_phase=WorkflowPhase.NAVIGATION,
            reason="Authorized",
            epoch_id=1,
            session_id="session-01",
        )
        svc = _make_started_service(
            mock_dispatcher, mock_safety_gate_service, mock_workflow_service,
            mock_navigation_service, mock_persistence_service, secret_filter,
            logger, runtime_context,
        )

        mock_navigation_service.bind_trajectory.reset_mock()

        from tests.unit.execution.conftest import make_trajectory_binding_request
        req = make_trajectory_binding_request()
        res = svc.execute_trajectory_binding(req)

        assert res.execution_status == ExecutionStatus.EXECUTED_CLEAR
        assert res.gate_decision == GateDecision.PERMITTED_CLEAR
        mock_navigation_service.bind_trajectory.assert_called_once()


class TestRecoveryReorientationDualGate:
    """Tests for RECOVERY_REORIENTATION dual-gate ordering and execution (Tests H, I, J)."""

    def test_case_h_recovery_m18_denied_short_circuits_m10_and_m17(
        self,
        mock_dispatcher,
        mock_safety_gate_service,
        mock_workflow_service,
        mock_navigation_service,
        mock_persistence_service,
        mock_recovery_service,
        secret_filter,
        logger,
        runtime_context,
    ) -> None:
        """Test H: M18 DENIED -> M10 and M17 skipped."""
        mock_safety_gate_service.evaluate.return_value = GateStatusRecord(
            session_id="session-01",
            decision=GateDecision.DENIED_INTERLOCKED,
            severity=GateSeverity.BLOCKING,
            reason_code=GateReasonCode.RUNTIME_EPOCH_MISMATCH,
            action=SafetyGateAction.RECOVERY_REORIENTATION,
            sequence_number=1,
            subsystem_snapshots=(),
            evaluated_at_utc="2025-01-01T12:00:00Z",
        )
        svc = _make_started_service(
            mock_dispatcher, mock_safety_gate_service, mock_workflow_service,
            mock_navigation_service, mock_persistence_service, secret_filter,
            logger, runtime_context,
            mock_recovery_service=mock_recovery_service,
        )

        mock_workflow_service.authorize_tool.reset_mock()
        mock_recovery_service.get_recovery_status.reset_mock()

        from tests.unit.execution.conftest import make_recovery_request
        req = make_recovery_request()
        res = svc.execute_recovery_reorientation(req)

        assert res.execution_status == ExecutionStatus.BLOCKED_SAFETY_GATE
        assert res.gate_decision == GateDecision.DENIED_INTERLOCKED
        mock_workflow_service.authorize_tool.assert_not_called()
        mock_recovery_service.get_recovery_status.assert_not_called()

    def test_case_i_recovery_m18_permit_m10_denied_short_circuits_m17(
        self,
        mock_dispatcher,
        mock_safety_gate_service,
        mock_workflow_service,
        mock_navigation_service,
        mock_persistence_service,
        mock_recovery_service,
        secret_filter,
        logger,
        runtime_context,
    ) -> None:
        """Test I: M18 PERMIT + M10 DENY -> M17 skipped."""
        mock_safety_gate_service.evaluate.return_value = GateStatusRecord(
            session_id="session-01",
            decision=GateDecision.PERMITTED_CLEAR,
            severity=GateSeverity.NONE,
            reason_code=GateReasonCode.NONE,
            action=SafetyGateAction.RECOVERY_REORIENTATION,
            sequence_number=1,
            subsystem_snapshots=(),
            evaluated_at_utc="2025-01-01T12:00:00Z",
        )
        mock_workflow_service.authorize_tool.return_value = WorkflowToolAuthorizationDecision(
            tool_id="recovery_session-01",
            m07_safety_classification=ToolSafetyClassification.TELEMETRY_RECORDING,
            status=WorkflowToolAuthorizationStatus.BLOCKED_INTERLOCK,
            workflow_phase=WorkflowPhase.RECOVERY_REQUIRED,
            reason="Active blocking interlock prevents recovery tool invocation",
            epoch_id=1,
            session_id="session-01",
        )
        svc = _make_started_service(
            mock_dispatcher, mock_safety_gate_service, mock_workflow_service,
            mock_navigation_service, mock_persistence_service, secret_filter,
            logger, runtime_context,
            mock_recovery_service=mock_recovery_service,
        )

        mock_recovery_service.get_recovery_status.reset_mock()

        from tests.unit.execution.conftest import make_recovery_request
        req = make_recovery_request()
        res = svc.execute_recovery_reorientation(req)

        assert res.execution_status == ExecutionStatus.BLOCKED_WORKFLOW
        assert res.workflow_status == WorkflowToolAuthorizationStatus.BLOCKED_INTERLOCK
        mock_recovery_service.get_recovery_status.assert_not_called()

    def test_case_j_recovery_m18_permit_m10_permit_executes_m17(
        self,
        mock_dispatcher,
        mock_safety_gate_service,
        mock_workflow_service,
        mock_navigation_service,
        mock_persistence_service,
        mock_recovery_service,
        secret_filter,
        logger,
        runtime_context,
    ) -> None:
        """Test J: M18 PERMIT + M10 PERMIT -> M17 recovery operation executed -> success."""
        mock_safety_gate_service.evaluate.return_value = GateStatusRecord(
            session_id="session-01",
            decision=GateDecision.PERMITTED_CLEAR,
            severity=GateSeverity.NONE,
            reason_code=GateReasonCode.NONE,
            action=SafetyGateAction.RECOVERY_REORIENTATION,
            sequence_number=1,
            subsystem_snapshots=(),
            evaluated_at_utc="2025-01-01T12:00:00Z",
        )
        mock_workflow_service.authorize_tool.return_value = WorkflowToolAuthorizationDecision(
            tool_id="recovery_session-01",
            m07_safety_classification=ToolSafetyClassification.TELEMETRY_RECORDING,
            status=WorkflowToolAuthorizationStatus.PERMITTED,
            workflow_phase=WorkflowPhase.RECOVERY_REQUIRED,
            reason="Recovery tool invocation authorized",
            epoch_id=1,
            session_id="session-01",
        )
        svc = _make_started_service(
            mock_dispatcher, mock_safety_gate_service, mock_workflow_service,
            mock_navigation_service, mock_persistence_service, secret_filter,
            logger, runtime_context,
            mock_recovery_service=mock_recovery_service,
        )

        from tests.unit.execution.conftest import make_recovery_request
        req = make_recovery_request()
        res = svc.execute_recovery_reorientation(req)

        assert res.execution_status == ExecutionStatus.EXECUTED_CLEAR
        assert res.gate_decision == GateDecision.PERMITTED_CLEAR
        assert res.recovery_status is not None
        mock_recovery_service.get_recovery_status.assert_called_once()
