# -*- coding: utf-8 -*-
"""Shared Pytest Fixtures and Mock Factories for M19 Execution Gateway Tests."""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import MagicMock

import pytest

from holomed.configuration.models import AppConfig
from holomed.core.dispatcher import MessageDispatcher
from holomed.core.models import DispatcherState
from holomed.execution.models import NavigationExecutionRequest
from holomed.navigation.models import (
    NavigationState,
    PositionClass,
    TrackedInstrumentPose,
    TrajectoryDeviationRecord,
)
from holomed.navigation.service import NavigationService
from holomed.persistence.service import PersistenceService
from holomed.planning.models import TrajectoryPlan
from holomed.runtime.context import RuntimeContext
from holomed.runtime.logging import SecretFilter, StructuredLogger
from holomed.safety_gate.models import (
    GateDecision,
    GateReasonCode,
    GateSeverity,
    GateStatusRecord,
    SafetyGateAction,
)
from holomed.safety_gate.service import SafetyGateService
from holomed.tools.models import ToolSafetyClassification
from holomed.workflow.models import (
    WorkflowPhase,
    WorkflowToolAuthorizationDecision,
    WorkflowToolAuthorizationStatus,
)
from holomed.workflow.service import WorkflowService


@pytest.fixture
def secret_filter() -> SecretFilter:
    return SecretFilter()


@pytest.fixture
def logger(secret_filter: SecretFilter) -> StructuredLogger:
    return StructuredLogger("test_execution_logger", secret_filter=secret_filter)


@pytest.fixture
def runtime_context() -> RuntimeContext:
    config = AppConfig(
        app_name="HoloMed-Execution-Test",
        environment="TESTING",
        host="127.0.0.1",
        port=8090,
        log_level="DEBUG",
        gemini_api_key=None,
        protocol_version="1.0",
    )
    return RuntimeContext(app_config=config, epoch_id=1, trace_context=None)


@pytest.fixture
def mock_dispatcher() -> MagicMock:
    dispatcher = MagicMock(spec=MessageDispatcher)
    dispatcher.state = DispatcherState.STARTED
    return dispatcher


def make_tracked_pose(
    instrument_id: str = "inst-01",
    session_id: str = "session-01",
    sequence_number: int = 1,
    tip_position_mm: tuple[float, float, float] = (10.0, 20.0, 30.0),
    orientation_quaternion: tuple[float, float, float, float] = (0.0, 0.0, 0.0, 1.0),
    now_utc: str = "",
) -> TrackedInstrumentPose:
    ts = now_utc or datetime.now(timezone.utc).isoformat()
    return TrackedInstrumentPose(
        instrument_id=instrument_id,
        session_id=session_id,
        epoch_id=1,
        sequence_number=sequence_number,
        tip_position_mm=tip_position_mm,
        orientation_quaternion=orientation_quaternion,
        coordinate_frame="PATIENT_TRACKER",
        confidence=1.0,
        uncertainty=0.1,
        timestamp_utc=ts,
    )


def make_execution_request(
    session_id: str = "session-01",
    sequence_number: int = 1,
    instrument_id: str = "inst-01",
    target_trajectory_id: str = "traj-01",
    action: SafetyGateAction = SafetyGateAction.TOOL_NAVIGATION,
    now_utc: str = "",
    pose: TrackedInstrumentPose | None = None,
) -> NavigationExecutionRequest:
    ts = now_utc or datetime.now(timezone.utc).isoformat()
    actual_pose = pose or make_tracked_pose(
        instrument_id=instrument_id,
        session_id=session_id,
        sequence_number=sequence_number,
        now_utc=ts,
    )
    return NavigationExecutionRequest(
        session_id=session_id,
        sequence_number=sequence_number,
        now_utc=ts,
        instrument_id=instrument_id,
        target_trajectory_id=target_trajectory_id,
        pose=actual_pose,
        action=action,
    )


def make_plan_trajectory(trajectory_id: str = "traj-01") -> TrajectoryPlan:
    return TrajectoryPlan(
        trajectory_id=trajectory_id,
        target_structure="TUMOR",
        entry_point_mm=(10.0, 20.0, 30.0),
        target_point_mm=(10.0, 20.0, 80.0),
        max_lateral_deviation_mm=2.0,
        max_angular_deviation_deg=5.0,
        min_confidence=0.9,
        max_uncertainty=0.1,
    )


@pytest.fixture
def mock_safety_gate_service() -> MagicMock:
    gate_svc = MagicMock(spec=SafetyGateService)
    rec = GateStatusRecord(
        session_id="session-01",
        decision=GateDecision.PERMITTED_CLEAR,
        severity=GateSeverity.NONE,
        reason_code=GateReasonCode.NONE,
        action=SafetyGateAction.TOOL_NAVIGATION,
        sequence_number=1,
        subsystem_snapshots=(),
        evaluated_at_utc="2025-01-01T12:00:00Z",
    )
    gate_svc.evaluate.return_value = rec
    return gate_svc


@pytest.fixture
def mock_workflow_service() -> MagicMock:
    wf_svc = MagicMock(spec=WorkflowService)
    wf_snapshot = MagicMock()
    wf_snapshot.current_phase.value = "NAVIGATION"
    wf_snapshot.is_terminal = False
    wf_svc.get_workflow_state.return_value = wf_snapshot

    wf_dec = WorkflowToolAuthorizationDecision(
        tool_id="inst-01",
        m07_safety_classification=ToolSafetyClassification.READ_ONLY_INFORMATIVE,
        status=WorkflowToolAuthorizationStatus.PERMITTED,
        workflow_phase=WorkflowPhase.NAVIGATION,
        reason="Nominal",
        epoch_id=1,
        session_id="session-01",
    )
    wf_svc.authorize_tool.return_value = wf_dec
    return wf_svc


@pytest.fixture
def mock_navigation_service() -> MagicMock:
    nav_svc = MagicMock(spec=NavigationService)
    dev_rec = TrajectoryDeviationRecord(
        record_id="dev-01",
        session_id="session-01",
        trajectory_id="traj-01",
        instrument_id="inst-01",
        lateral_deviation_mm=0.5,
        angular_deviation_deg=1.2,
        axial_distance_mm=25.0,
        position_class=PositionClass.IN_TRAJECTORY,
        pose_age_ms=10.0,
        state=NavigationState.ALIGNED,
        is_aligned=True,
        is_safe=True,
        safety_interlock=None,
        evaluated_at_utc="2025-01-01T12:00:00Z",
    )
    nav_svc.evaluate.return_value = dev_rec
    return nav_svc


from holomed.recovery.models import (
    RecoveryAuthorization,
    RecoveryState,
    RecoveryStatusRecord,
    RecoveryVerificationSnapshot,
    StagedRegistrationCandidate,
)
from holomed.recovery.service import RecoveryService
from holomed.execution.models import (
    NavigationExecutionRequest,
    RecoveryReorientationExecutionRequest,
    TrajectoryBindingExecutionRequest,
)


def make_trajectory_binding_request(
    session_id: str = "session-01",
    trajectory_id: str = "traj-01",
    sequence_number: int = 1,
    now_utc: str = "",
    action: SafetyGateAction = SafetyGateAction.TRAJECTORY_ALIGNMENT,
    tool_safety_classification: ToolSafetyClassification = ToolSafetyClassification.VISUALIZATION_ADJUSTMENT,
    plan_trajectory: TrajectoryPlan | None = None,
) -> TrajectoryBindingExecutionRequest:
    ts = now_utc or datetime.now(timezone.utc).isoformat()
    return TrajectoryBindingExecutionRequest(
        session_id=session_id,
        trajectory_id=trajectory_id,
        plan_trajectory=plan_trajectory or make_plan_trajectory(trajectory_id),
        sequence_number=sequence_number,
        now_utc=ts,
        action=action,
        tool_safety_classification=tool_safety_classification,
    )


def make_recovery_request(
    session_id: str = "session-01",
    sequence_number: int = 1,
    now_utc: str = "",
    action: SafetyGateAction = SafetyGateAction.RECOVERY_REORIENTATION,
    tool_safety_classification: ToolSafetyClassification = ToolSafetyClassification.TELEMETRY_RECORDING,
    recovery_operation: str = "STATUS",
) -> RecoveryReorientationExecutionRequest:
    ts = now_utc or datetime.now(timezone.utc).isoformat()
    return RecoveryReorientationExecutionRequest(
        session_id=session_id,
        sequence_number=sequence_number,
        now_utc=ts,
        action=action,
        tool_safety_classification=tool_safety_classification,
        recovery_operation=recovery_operation,
    )


@pytest.fixture
def mock_recovery_service() -> MagicMock:
    rec_svc = MagicMock(spec=RecoveryService)
    rec_status = RecoveryStatusRecord(
        recovery_id="rec-01",
        session_id="session-01",
        state=RecoveryState.ACTIVATED,
        registration_revision=1,
        prior_registration_state=None,
        active_transform=None,
        last_quality_report=None,
        last_verification=None,
        updated_at_utc="2025-01-01T12:00:00Z",
    )
    rec_svc.get_recovery_status.return_value = rec_status
    rec_svc.activate_recovery.return_value = rec_status
    return rec_svc


@pytest.fixture
def mock_persistence_service() -> MagicMock:
    return MagicMock(spec=PersistenceService)
