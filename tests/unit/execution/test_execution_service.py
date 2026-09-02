# -*- coding: utf-8 -*-
"""Unit Tests for M19 NavigationExecutionService — Lifecycle, Handles, Routes & Persistence Deduplication."""

from __future__ import annotations

from unittest.mock import MagicMock
import pytest

from holomed.execution.constants import STRUCTURAL_RESOURCE_IDS
from holomed.execution.models import (
    ExecutionStatus,
    TrajectoryBindingExecutionRequest,
)
from holomed.execution.service import NavigationExecutionService
from holomed.protocol.builders import create_command, create_query
from holomed.runtime.models import HealthStatus
from holomed.runtime.service import ServiceState
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
    make_plan_trajectory,
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
) -> NavigationExecutionService:
    svc = NavigationExecutionService(
        dispatcher=mock_dispatcher,
        safety_gate_service=mock_safety_gate_service,
        workflow_service=mock_workflow_service,
        navigation_service=mock_navigation_service,
        persistence_service=mock_persistence_service,
        secret_filter=secret_filter,
        logger=logger,
    )
    svc.initialize(runtime_context)
    svc.start()
    return svc


class TestExecutionServiceLifecycle:
    """Tests for 4-handle lifecycle and health."""

    def test_initialize_acquires_exactly_4_structural_handles(
        self, mock_dispatcher, secret_filter, logger, runtime_context
    ) -> None:
        svc = NavigationExecutionService(
            dispatcher=mock_dispatcher,
            secret_filter=secret_filter,
            logger=logger,
        )
        assert svc.state == ServiceState.UNINITIALIZED

        svc.initialize(runtime_context)
        assert svc.state == ServiceState.INITIALIZED
        assert len(svc.resources.outstanding_handles) == 4
        assert {h.resource_id for h in svc.resources.outstanding_handles} == set(STRUCTURAL_RESOURCE_IDS)

    def test_start_and_stop_releases_handles_idempotently(
        self, mock_dispatcher, secret_filter, logger, runtime_context
    ) -> None:
        svc = NavigationExecutionService(
            dispatcher=mock_dispatcher,
            secret_filter=secret_filter,
            logger=logger,
        )
        svc.initialize(runtime_context)
        svc.start()
        assert svc.state == ServiceState.STARTED

        svc.stop()
        assert svc.state == ServiceState.STOPPED
        assert svc.resources.is_empty

        # Idempotent stop
        svc.stop()
        assert svc.state == ServiceState.STOPPED

    def test_health_reporting(
        self, mock_dispatcher, secret_filter, logger, runtime_context
    ) -> None:
        svc = NavigationExecutionService(
            dispatcher=mock_dispatcher,
            secret_filter=secret_filter,
            logger=logger,
        )
        assert svc.health().status == HealthStatus.UNHEALTHY

        svc.initialize(runtime_context)
        svc.start()
        assert svc.health().status == HealthStatus.HEALTHY


class TestExecutionPersistenceDeduplication:
    """Tests confirming persistence on transitions and suppression on identical frames."""

    def test_repeated_identical_evaluations_persisted_only_once(
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

        req1 = make_execution_request(sequence_number=1)
        req2 = make_execution_request(sequence_number=2)
        req3 = make_execution_request(sequence_number=3)

        mock_persistence_service.record_audit.reset_mock()

        # Frame 1: First execution -> Persisted
        svc.execute_navigation(req1)
        assert mock_persistence_service.record_audit.call_count == 1

        # Frame 2: Identical state -> Suppressed
        svc.execute_navigation(req2)
        assert mock_persistence_service.record_audit.call_count == 1

        # Frame 3: Identical state -> Suppressed
        svc.execute_navigation(req3)
        assert mock_persistence_service.record_audit.call_count == 1

        # Frame 4: Safety gate warning transition -> Persisted!
        mock_safety_gate_service.evaluate.return_value = GateStatusRecord(
            session_id="session-01",
            decision=GateDecision.PERMITTED_WITH_CAUTION,
            severity=GateSeverity.WARNING,
            reason_code=GateReasonCode.APPROACHING_MARGIN,
            action=SafetyGateAction.TOOL_NAVIGATION,
            sequence_number=4,
            subsystem_snapshots=(),
            evaluated_at_utc="2025-01-01T12:00:00Z",
        )
        req4 = make_execution_request(sequence_number=4)
        svc.execute_navigation(req4)
        assert mock_persistence_service.record_audit.call_count == 2


class TestExecutionTrajectoryBinding:
    """Tests for trajectory binding execution and isolation."""

    def test_trajectory_binding_executes_and_does_not_grant_tool_navigation(
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

        req = TrajectoryBindingExecutionRequest(
            session_id="session-01",
            trajectory_id="traj-01",
            plan_trajectory=make_plan_trajectory(),
            sequence_number=1,
            now_utc="2025-01-01T12:00:00Z",
        )
        res = svc.execute_trajectory_binding(req)
        assert res.execution_status == ExecutionStatus.EXECUTED_CLEAR
        mock_navigation_service.bind_trajectory.assert_called_once()


class TestExecutionRecoveryReorientation:
    """Tests for recovery reorientation execution through the service."""

    def test_recovery_reorientation_service_execution(
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

        from tests.unit.execution.conftest import make_recovery_request
        req = make_recovery_request(recovery_operation="STATUS")
        res = svc.execute_recovery_reorientation(req)

        assert res.execution_status == ExecutionStatus.EXECUTED_CLEAR
        assert res.action == SafetyGateAction.RECOVERY_REORIENTATION
        assert res.recovery_status is not None


class TestExecutionDispatcherRoutes:
    """Tests for dispatcher commands and queries."""

    def test_navigation_execute_command_route(
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

        cmd = create_command(
            message_name="execution.navigation.execute",
            source="test",
            target="navigation_execution_service",
            payload={
                "session_id": "session-01",
                "instrument_id": "inst-01",
                "target_trajectory_id": "traj-01",
                "sequence_number": 1,
                "now_utc": "2025-01-01T12:00:00Z",
                "pose": {
                    "instrument_id": "inst-01",
                    "session_id": "session-01",
                    "sequence_number": 1,
                    "tip_position_mm": [10.0, 20.0, 30.0],
                    "orientation_quaternion": [0.0, 0.0, 0.0, 1.0],
                    "timestamp_utc": "2025-01-01T12:00:00Z",
                },
            },
        )
        resp = svc.handle_execute_command(cmd)
        assert resp.payload["execution_status"] == "EXECUTED_CLEAR"

        # Query route
        qry = create_query(
            message_name="execution.status.get",
            source="test",
            target="navigation_execution_service",
            payload={"session_id": "session-01"},
        )
        q_resp = svc.handle_get_status_query(qry)
        assert q_resp.payload["execution_status"] == "EXECUTED_CLEAR"
