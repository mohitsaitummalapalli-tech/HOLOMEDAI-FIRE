# -*- coding: utf-8 -*-
"""Adversarial Tests for M19 Execution Gateway — Anti-Replay, Identity Binding & Bypass Limitation."""

from __future__ import annotations

from unittest.mock import MagicMock
import pytest

from holomed.execution.constants import MAX_ACTIVE_EXECUTION_SESSIONS
from holomed.execution.exceptions import (
    ExecutionCapacityError,
    ExecutionValidationError,
)
from holomed.execution.models import ExecutionStatus
from holomed.execution.service import NavigationExecutionService
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
from tests.unit.execution.conftest import (
    make_execution_request,
    make_tracked_pose,
)


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


class TestExecutionAntiReplayAndIdentityBinding:
    """Tests strictly verifying request/pose identity binding and anti-replay."""

    def test_session_mismatch_rejected(
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
        svc = _make_started_service(
            mock_dispatcher, mock_safety_gate_service, mock_workflow_service,
            mock_navigation_service, mock_persistence_service, secret_filter,
            logger, runtime_context,
        )

        mismatched_pose = make_tracked_pose(session_id="session-DIFFERENT")
        req = make_execution_request(session_id="session-01", pose=mismatched_pose)

        with pytest.raises(ExecutionValidationError, match="Pose session_id"):
            svc.execute_navigation(req)

    def test_instrument_mismatch_rejected(
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
        svc = _make_started_service(
            mock_dispatcher, mock_safety_gate_service, mock_workflow_service,
            mock_navigation_service, mock_persistence_service, secret_filter,
            logger, runtime_context,
        )

        mismatched_pose = make_tracked_pose(instrument_id="inst-02")
        req = make_execution_request(instrument_id="inst-01", pose=mismatched_pose)

        with pytest.raises(ExecutionValidationError, match="Pose instrument_id"):
            svc.execute_navigation(req)

    def test_sequence_mismatch_rejected(
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
        svc = _make_started_service(
            mock_dispatcher, mock_safety_gate_service, mock_workflow_service,
            mock_navigation_service, mock_persistence_service, secret_filter,
            logger, runtime_context,
        )

        mismatched_pose = make_tracked_pose(sequence_number=99)
        req = make_execution_request(sequence_number=1, pose=mismatched_pose)

        with pytest.raises(ExecutionValidationError, match="Pose sequence_number"):
            svc.execute_navigation(req)

    def test_action_mismatch_rejected(
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
        svc = _make_started_service(
            mock_dispatcher, mock_safety_gate_service, mock_workflow_service,
            mock_navigation_service, mock_persistence_service, secret_filter,
            logger, runtime_context,
        )

        req = make_execution_request()
        object.__setattr__(req, "action", SafetyGateAction.RECOVERY_REORIENTATION)
        with pytest.raises(ExecutionValidationError, match="Action mismatch"):
            svc.execute_navigation(req)

    def test_trajectory_mismatch_rejected(
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
        """Test R: Mismatched trajectory ID format is rejected."""
        valid_pose = make_tracked_pose()
        with pytest.raises(ExecutionValidationError, match="target_trajectory_id"):
            from holomed.execution.models import NavigationExecutionRequest
            NavigationExecutionRequest(
                session_id="session-01",
                sequence_number=1,
                now_utc="2025-01-01T12:00:00Z",
                instrument_id="inst-01",
                target_trajectory_id="invalid traj!@#",
                pose=valid_pose,
            )

    def test_timestamp_binding_validation(
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
        """Test S: Empty or invalid timestamp in request is rejected."""
        valid_pose = make_tracked_pose()
        with pytest.raises(ExecutionValidationError, match="now_utc"):
            from holomed.execution.models import NavigationExecutionRequest
            NavigationExecutionRequest(
                session_id="session-01",
                sequence_number=1,
                now_utc="",
                instrument_id="inst-01",
                target_trajectory_id="traj-01",
                pose=valid_pose,
            )


class TestActionIsolation:
    """Tests strictly verifying action isolation across TOOL_NAVIGATION, TRAJECTORY_ALIGNMENT, and RECOVERY_REORIENTATION."""

    def test_case_k_alignment_permission_cannot_authorize_navigation(
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
        """Test K: TRAJECTORY_ALIGNMENT success never authorizes TOOL_NAVIGATION."""
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

        from tests.unit.execution.conftest import make_trajectory_binding_request
        bind_res = svc.execute_trajectory_binding(make_trajectory_binding_request())
        assert bind_res.execution_status == ExecutionStatus.EXECUTED_CLEAR
        assert bind_res.action == SafetyGateAction.TRAJECTORY_ALIGNMENT

        # Navigation status query proves navigation remains un-executed
        status = svc.get_execution_status("session-01")
        assert status is None

    def test_case_l_recovery_permission_cannot_authorize_navigation(
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
        """Test L: RECOVERY_REORIENTATION permission never authorizes TOOL_NAVIGATION."""
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
            reason="Authorized",
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
        rec_res = svc.execute_recovery_reorientation(make_recovery_request())
        assert rec_res.execution_status == ExecutionStatus.EXECUTED_CLEAR
        assert rec_res.action == SafetyGateAction.RECOVERY_REORIENTATION

        # Navigation status query proves navigation remains un-executed
        status = svc.get_execution_status("session-01")
        assert status is None

    def test_case_m_navigation_permission_cannot_authorize_recovery(
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
        """Test M: TOOL_NAVIGATION execution does not satisfy RECOVERY_REORIENTATION gate."""
        svc = _make_started_service(
            mock_dispatcher, mock_safety_gate_service, mock_workflow_service,
            mock_navigation_service, mock_persistence_service, secret_filter,
            logger, runtime_context,
            mock_recovery_service=mock_recovery_service,
        )

        # 1. Nominal Navigation succeeds
        nav_res = svc.execute_navigation(make_execution_request())
        assert nav_res.execution_status == ExecutionStatus.EXECUTED_CLEAR

        # 2. Recovery request must be independently evaluated through M18 & M10
        mock_safety_gate_service.evaluate.return_value = GateStatusRecord(
            session_id="session-01",
            decision=GateDecision.DENIED_INTERLOCKED,
            severity=GateSeverity.BLOCKING,
            reason_code=GateReasonCode.RUNTIME_EPOCH_MISMATCH,
            action=SafetyGateAction.RECOVERY_REORIENTATION,
            sequence_number=2,
            subsystem_snapshots=(),
            evaluated_at_utc="2025-01-01T12:00:00Z",
        )

        from tests.unit.execution.conftest import make_recovery_request
        rec_res = svc.execute_recovery_reorientation(make_recovery_request(sequence_number=2))
        assert rec_res.execution_status == ExecutionStatus.BLOCKED_SAFETY_GATE


class TestExecutionDirectBypassLimitation:
    """Explicitly verify and document architectural bypass limitation under frozen M00-M18."""

    def test_m19_blocks_while_direct_m14_caller_operates_independently(
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
        """Architectural Proof:
        When M18 is DENIED, M19 returns BLOCKED_SAFETY_GATE and never invokes M14.
        However, if an external caller directly invokes frozen M14 evaluate(),
        M14 executes independently because M14 has no M19 dependency.
        """
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

        # 1. Coordinated M19 Gateway correctly blocks execution
        res = svc.execute_navigation(make_execution_request())
        assert res.execution_status == ExecutionStatus.BLOCKED_SAFETY_GATE

        # 2. Direct frozen M14 primitive can still be called independently
        direct_dev = mock_navigation_service.evaluate("session-01")
        assert direct_dev.is_safe is True


class TestExecutionCapacity:
    """Tests for active session capacity limits."""

    def test_session_capacity_exceeded(
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
        svc = _make_started_service(
            mock_dispatcher, mock_safety_gate_service, mock_workflow_service,
            mock_navigation_service, mock_persistence_service, secret_filter,
            logger, runtime_context,
        )

        for i in range(MAX_ACTIVE_EXECUTION_SESSIONS):
            svc.execute_navigation(make_execution_request(session_id=f"sess-{i:03d}"))

        with pytest.raises(ExecutionCapacityError, match="Max active execution sessions"):
            svc.execute_navigation(make_execution_request(session_id="sess-overflow"))
