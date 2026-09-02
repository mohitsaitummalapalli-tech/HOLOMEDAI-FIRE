# -*- coding: utf-8 -*-
"""Unit Tests for M19 Execution Models, Validation, and Immutability."""

from __future__ import annotations

from dataclasses import FrozenInstanceError
import pytest

from holomed.execution.exceptions import ExecutionValidationError
from holomed.execution.models import (
    ExecutionStatus,
    NavigationExecutionRequest,
    NavigationExecutionResult,
    TrajectoryBindingExecutionRequest,
    TrajectoryBindingExecutionResult,
)
from holomed.safety_gate.models import (
    GateDecision,
    GateReasonCode,
    SafetyGateAction,
)
from tests.unit.execution.conftest import (
    make_execution_request,
    make_plan_trajectory,
    make_tracked_pose,
)


class TestExecutionModelValidation:
    """Tests for model parameter validation."""

    def test_valid_request(self) -> None:
        req = make_execution_request()
        assert req.session_id == "session-01"
        assert req.sequence_number == 1
        assert req.action == SafetyGateAction.TOOL_NAVIGATION

    def test_invalid_session_id(self) -> None:
        valid_pose = make_tracked_pose()
        with pytest.raises(ExecutionValidationError, match="Invalid session_id"):
            NavigationExecutionRequest(
                session_id="invalid session!@#",
                sequence_number=1,
                now_utc="2025-01-01T12:00:00Z",
                instrument_id="inst-01",
                target_trajectory_id="traj-01",
                pose=valid_pose,
            )

    def test_invalid_sequence_number(self) -> None:
        valid_pose = make_tracked_pose()
        with pytest.raises(ExecutionValidationError, match="sequence_number"):
            NavigationExecutionRequest(
                session_id="session-01",
                sequence_number=0,
                now_utc="2025-01-01T12:00:00Z",
                instrument_id="inst-01",
                target_trajectory_id="traj-01",
                pose=valid_pose,
            )

    def test_invalid_instrument_id(self) -> None:
        valid_pose = make_tracked_pose()
        with pytest.raises(ExecutionValidationError, match="Invalid instrument_id"):
            NavigationExecutionRequest(
                session_id="session-01",
                sequence_number=1,
                now_utc="2025-01-01T12:00:00Z",
                instrument_id="inst with spaces!",
                target_trajectory_id="traj-01",
                pose=valid_pose,
            )

    def test_invalid_trajectory_id(self) -> None:
        with pytest.raises(ExecutionValidationError, match="Invalid target_trajectory_id"):
            make_execution_request(target_trajectory_id="traj with spaces!")

    def test_invalid_trajectory_binding_request(self) -> None:
        with pytest.raises(ExecutionValidationError, match="Invalid session_id"):
            TrajectoryBindingExecutionRequest(
                session_id="bad session!",
                trajectory_id="traj-01",
                plan_trajectory=make_plan_trajectory(),
                sequence_number=1,
                now_utc="2025-01-01T12:00:00Z",
            )

    def test_invalid_recovery_reorientation_request(self) -> None:
        from holomed.execution.models import RecoveryReorientationExecutionRequest
        with pytest.raises(ExecutionValidationError, match="Invalid session_id"):
            RecoveryReorientationExecutionRequest(
                session_id="bad session!",
                sequence_number=1,
                now_utc="2025-01-01T12:00:00Z",
            )

        with pytest.raises(ExecutionValidationError, match="sequence_number"):
            RecoveryReorientationExecutionRequest(
                session_id="session-01",
                sequence_number=0,
                now_utc="2025-01-01T12:00:00Z",
            )


class TestExecutionModelImmutability:
    """Tests for frozen dataclass immutability."""

    def test_request_is_immutable(self) -> None:
        req = make_execution_request()
        with pytest.raises(FrozenInstanceError):
            req.session_id = "other"  # type: ignore

    def test_result_is_immutable(self) -> None:
        res = NavigationExecutionResult(
            session_id="session-01",
            execution_status=ExecutionStatus.EXECUTED_CLEAR,
            gate_decision=GateDecision.PERMITTED_CLEAR,
            gate_reason_code=GateReasonCode.NONE,
            action=SafetyGateAction.TOOL_NAVIGATION,
            sequence_number=1,
            instrument_id="inst-01",
            target_trajectory_id="traj-01",
            executed_at_utc="2025-01-01T12:00:00Z",
        )
        with pytest.raises(FrozenInstanceError):
            res.execution_status = ExecutionStatus.BLOCKED_SAFETY_GATE  # type: ignore

    def test_recovery_models_are_immutable(self) -> None:
        from holomed.execution.models import (
            RecoveryReorientationExecutionRequest,
            RecoveryReorientationExecutionResult,
        )
        req = RecoveryReorientationExecutionRequest(
            session_id="session-01",
            sequence_number=1,
            now_utc="2025-01-01T12:00:00Z",
        )
        with pytest.raises(FrozenInstanceError):
            req.session_id = "other"  # type: ignore

        res = RecoveryReorientationExecutionResult(
            session_id="session-01",
            execution_status=ExecutionStatus.EXECUTED_CLEAR,
            gate_decision=GateDecision.PERMITTED_CLEAR,
            gate_reason_code=GateReasonCode.NONE,
            action=SafetyGateAction.RECOVERY_REORIENTATION,
            sequence_number=1,
            executed_at_utc="2025-01-01T12:00:00Z",
        )
        with pytest.raises(FrozenInstanceError):
            res.execution_status = ExecutionStatus.BLOCKED_SAFETY_GATE  # type: ignore


class TestExecutionEnumCompleteness:
    """Tests confirming all ExecutionStatus enum values."""

    def test_execution_status_values(self) -> None:
        expected = {
            "EXECUTED_CLEAR",
            "EXECUTED_WITH_CAUTION",
            "BLOCKED_SAFETY_GATE",
            "BLOCKED_WORKFLOW",
            "FAILED_NAVIGATION_GEOMETRY",
        }
        assert {s.value for s in ExecutionStatus} == expected
