# -*- coding: utf-8 -*-
"""End-to-End Tests for M19 Navigation Safety Gate Execution Gateway."""

from __future__ import annotations

from unittest.mock import MagicMock
import pytest

from holomed.execution.models import (
    ExecutionStatus,
    TrajectoryBindingExecutionRequest,
)
from holomed.execution.service import NavigationExecutionService
from holomed.runtime.service import ServiceState
from holomed.safety_gate.models import (
    GateDecision,
    GateReasonCode,
    GateSeverity,
    GateStatusRecord,
    SafetyGateAction,
)
from tests.unit.execution.conftest import (
    make_execution_request,
    make_plan_trajectory,
)


class TestExecutionE2E:
    """Dynamic multi-service coordinated surgical navigation scenario."""

    def test_full_coordinated_intraoperative_navigation_lifecycle(
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
        """Scenario:
        1. Initialize & Start NavigationExecutionService
        2. Execute Trajectory Binding (TRAJECTORY_ALIGNMENT)
        3. Execute Tool Navigation (Nominal EXECUTED_CLEAR)
        4. Execute Tool Navigation with Margin Caution (EXECUTED_WITH_CAUTION)
        5. Execute Tool Navigation blocked by Safety Gate (BLOCKED_SAFETY_GATE)
        6. Execute Tool Navigation recovery & resumption (EXECUTED_CLEAR)
        7. Teardown & handle release
        """
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
        assert svc.state == ServiceState.STARTED

        # Step 1: Trajectory Binding
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
        bind_res = svc.execute_trajectory_binding(
            TrajectoryBindingExecutionRequest(
                session_id="session-01",
                trajectory_id="traj-01",
                plan_trajectory=make_plan_trajectory(),
                sequence_number=1,
                now_utc="2025-01-01T12:00:00Z",
            )
        )
        assert bind_res.execution_status == ExecutionStatus.EXECUTED_CLEAR

        # Step 2: Nominal Tool Navigation
        mock_safety_gate_service.evaluate.return_value = GateStatusRecord(
            session_id="session-01",
            decision=GateDecision.PERMITTED_CLEAR,
            severity=GateSeverity.NONE,
            reason_code=GateReasonCode.NONE,
            action=SafetyGateAction.TOOL_NAVIGATION,
            sequence_number=2,
            subsystem_snapshots=(),
            evaluated_at_utc="2025-01-01T12:00:01Z",
        )
        r2 = svc.execute_navigation(make_execution_request(sequence_number=2, now_utc="2025-01-01T12:00:01Z"))
        assert r2.execution_status == ExecutionStatus.EXECUTED_CLEAR

        # Step 3: Margin Caution
        mock_safety_gate_service.evaluate.return_value = GateStatusRecord(
            session_id="session-01",
            decision=GateDecision.PERMITTED_WITH_CAUTION,
            severity=GateSeverity.WARNING,
            reason_code=GateReasonCode.APPROACHING_MARGIN,
            action=SafetyGateAction.TOOL_NAVIGATION,
            sequence_number=3,
            subsystem_snapshots=(),
            evaluated_at_utc="2025-01-01T12:00:02Z",
        )
        r3 = svc.execute_navigation(make_execution_request(sequence_number=3, now_utc="2025-01-01T12:00:02Z"))
        assert r3.execution_status == ExecutionStatus.EXECUTED_WITH_CAUTION
        assert r3.gate_reason_code == GateReasonCode.APPROACHING_MARGIN

        # Step 4: Landmark Drift Exceeded -> Blocked by M18
        mock_safety_gate_service.evaluate.return_value = GateStatusRecord(
            session_id="session-01",
            decision=GateDecision.DENIED_INTERLOCKED,
            severity=GateSeverity.BLOCKING,
            reason_code=GateReasonCode.LANDMARK_DRIFT_EXCEEDED,
            action=SafetyGateAction.TOOL_NAVIGATION,
            sequence_number=4,
            subsystem_snapshots=(),
            evaluated_at_utc="2025-01-01T12:00:03Z",
        )
        r4 = svc.execute_navigation(make_execution_request(sequence_number=4, now_utc="2025-01-01T12:00:03Z"))
        assert r4.execution_status == ExecutionStatus.BLOCKED_SAFETY_GATE

        # Step 5: Resumption after recovery -> Clear
        mock_safety_gate_service.evaluate.return_value = GateStatusRecord(
            session_id="session-01",
            decision=GateDecision.PERMITTED_CLEAR,
            severity=GateSeverity.NONE,
            reason_code=GateReasonCode.NONE,
            action=SafetyGateAction.TOOL_NAVIGATION,
            sequence_number=5,
            subsystem_snapshots=(),
            evaluated_at_utc="2025-01-01T12:00:04Z",
        )
        r5 = svc.execute_navigation(make_execution_request(sequence_number=5, now_utc="2025-01-01T12:00:04Z"))
        assert r5.execution_status == ExecutionStatus.EXECUTED_CLEAR

        # Teardown
        svc.stop()
        assert svc.state == ServiceState.STOPPED
        assert svc.resources.is_empty
