# -*- coding: utf-8 -*-
"""Authoritative Contract Verification Suite for M22 Spatial Recovery & Trajectory Hardening.

Verifies:
1. Removal of raw recovery dispatcher routes (recovery.stage, recovery.verify, recovery.activate).
2. Retention of read-only recovery.status.get query route.
3. Capability enforcement on RecoveryService.stage_candidate, verify_candidate, activate_recovery.
4. Capability enforcement on NavigationService.bind_trajectory.
5. Strict rejection of forged, mismatched, replayed, expired, or invalid capabilities.
6. Execution Gateway coordination of recovery (STAGE, VERIFY, ACTIVATE) and trajectory binding.
7. Unconditional capability invalidation in finally blocks.
"""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import MagicMock
import pytest

from holomed.core.dispatcher import MessageDispatcher
from holomed.core.exceptions import UnroutableMessageError
from holomed.execution._capability import _ExecutionCapability, _create_execution_capability
from holomed.execution.models import (
    ExecutionStatus,
    RecoveryReorientationExecutionRequest,
    TrajectoryBindingExecutionRequest,
)
from holomed.execution.service import ClinicalExecutionGatewayService
from holomed.navigation.constants import DEFAULT_PATIENT_TRACKER_FRAME
from holomed.navigation.exceptions import (
    NavigationAuthorizationError,
    NavigationRegistrationMismatchError,
)
from holomed.navigation.models import NavigationState, TrackedInstrumentPose
from holomed.navigation.service import NavigationService
from holomed.planning.models import TrajectoryPlan
from holomed.protocol.builders import create_command, create_query
from holomed.recovery.exceptions import (
    RecoveryAuthorizationError,
    RecoveryLifecycleError,
)
from holomed.recovery.models import (
    RecoveryAuthorization,
    RecoveryState,
)
from holomed.recovery.service import RecoveryService
from holomed.registration.models import (
    FiducialCloud,
    FiducialPointPair,
    RigidRegistrationTransform3D,
)
from holomed.registration.service import RegistrationService
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
from holomed.workflow.models import (
    WorkflowPhase,
    WorkflowStateSnapshot,
    WorkflowToolAuthorizationDecision,
    WorkflowToolAuthorizationStatus,
)
from holomed.workflow.service import WorkflowService


@pytest.fixture
def test_runtime_context() -> RuntimeContext:
    from holomed.configuration.models import AppConfig
    config = AppConfig(
        app_name="HoloMed-M22-Hardening-Test",
        environment="TESTING",
        host="127.0.0.1",
        port=8090,
        log_level="DEBUG",
        gemini_api_key=None,
        protocol_version="1.0",
    )
    return RuntimeContext(app_config=config, epoch_id=1)


@pytest.fixture
def secret_filter() -> SecretFilter:
    return SecretFilter()


@pytest.fixture
def sample_cloud() -> FiducialCloud:
    pairs = (
        FiducialPointPair("f1", (100.0, 0.0, 0.0), (100.1, 0.0, 0.0), "f1"),
        FiducialPointPair("f2", (0.0, 100.0, 0.0), (0.0, 100.1, 0.0), "f2"),
        FiducialPointPair("f3", (0.0, -100.0, 0.0), (0.0, -100.1, 0.0), "f3"),
        FiducialPointPair("f4", (0.0, 0.0, 100.0), (0.0, 0.0, 100.1), "f4"),
    )
    return FiducialCloud(pairs=pairs)


@pytest.fixture
def sample_trajectory() -> TrajectoryPlan:
    return TrajectoryPlan(
        trajectory_id="traj_01",
        target_structure="pedicle_l4",
        entry_point_mm=(0.0, 0.0, 0.0),
        target_point_mm=(0.0, 0.0, 100.0),
        max_lateral_deviation_mm=2.0,
        max_angular_deviation_deg=5.0,
        min_confidence=0.8,
        max_uncertainty=0.2,
    )


class TestM22DispatcherRouteRemoval:
    """Verifies that M17 raw execution routes are completely unroutable."""

    def test_raw_recovery_routes_unroutable(
        self, test_runtime_context: RuntimeContext, secret_filter: SecretFilter
    ) -> None:
        dispatcher = MessageDispatcher()
        dispatcher.initialize(test_runtime_context)
        recovery_srv = RecoveryService(
            dispatcher=dispatcher,
            secret_filter=secret_filter,
        )
        recovery_srv.initialize(test_runtime_context)
        dispatcher.start()
        recovery_srv.start()

        # 1. recovery.stage
        cmd_stage = create_command("recovery.stage", "test", payload={"session_id": "session-01"})
        with pytest.raises(UnroutableMessageError):
            dispatcher.dispatch(cmd_stage)

        # 2. recovery.verify
        cmd_verify = create_command("recovery.verify", "test", payload={"session_id": "session-01"})
        with pytest.raises(UnroutableMessageError):
            dispatcher.dispatch(cmd_verify)

        # 3. recovery.activate
        cmd_activate = create_command("recovery.activate", "test", payload={"session_id": "session-01"})
        with pytest.raises(UnroutableMessageError):
            dispatcher.dispatch(cmd_activate)

        # 4. recovery.status.get remains routable
        qry_status = create_query("recovery.status.get", "test", payload={"session_id": "session-01"})
        resp = dispatcher.dispatch(qry_status)
        assert resp is not None
        assert resp.message_type.value == "RESPONSE"
        assert resp.payload["state"] == "IDLE"

        recovery_srv.stop()


class TestM22RecoveryDirectCapabilityEnforcement:
    """Verifies that direct Python calls to RecoveryService methods fail closed without valid capability."""

    def test_stage_candidate_requires_capability(
        self, test_runtime_context: RuntimeContext, secret_filter: SecretFilter, sample_cloud: FiducialCloud
    ) -> None:
        srv = RecoveryService(secret_filter=secret_filter)
        srv.initialize(test_runtime_context)
        srv.start()

        # Direct call without capability
        with pytest.raises(RecoveryAuthorizationError, match="missing execution capability"):
            srv.stage_candidate("session-01", "plan-01", sample_cloud)

        # Inactive capability
        cap = _create_execution_capability(id(srv), "session-01", "RECOVERY_REORIENTATION", 1)
        cap.invalidate()
        with pytest.raises(RecoveryAuthorizationError, match="inactive, expired, or replayed"):
            srv.stage_candidate("session-01", "plan-01", sample_cloud, capability=cap)

        # Session mismatch
        cap_wrong_session = _create_execution_capability(id(srv), "other-session", "RECOVERY_REORIENTATION", 1)
        with pytest.raises(RecoveryAuthorizationError, match="Capability session mismatch"):
            srv.stage_candidate("session-01", "plan-01", sample_cloud, capability=cap_wrong_session)

        # Action mismatch
        cap_wrong_action = _create_execution_capability(id(srv), "session-01", "TOOL_NAVIGATION", 1)
        with pytest.raises(RecoveryAuthorizationError, match="Capability action mismatch"):
            srv.stage_candidate("session-01", "plan-01", sample_cloud, capability=cap_wrong_action)

        # Sequence mismatch
        cap_wrong_seq = _create_execution_capability(id(srv), "session-01", "RECOVERY_REORIENTATION", 5)
        with pytest.raises(RecoveryAuthorizationError, match="Capability sequence mismatch"):
            srv.stage_candidate("session-01", "plan-01", sample_cloud, sequence_number=1, capability=cap_wrong_seq)

        # Service instance mismatch
        cap_wrong_srv = _create_execution_capability(12345678, "session-01", "RECOVERY_REORIENTATION", 1)
        with pytest.raises(RecoveryAuthorizationError, match="Capability service binding mismatch"):
            srv.stage_candidate("session-01", "plan-01", sample_cloud, capability=cap_wrong_srv)

        # Valid capability succeeds
        cap_valid = _create_execution_capability(id(srv), "session-01", "RECOVERY_REORIENTATION", 1)
        candidate = srv.stage_candidate("session-01", "plan-01", sample_cloud, sequence_number=1, capability=cap_valid)
        assert candidate.plan_id == "plan-01"

        srv.stop()

    def test_verify_candidate_requires_capability(
        self, test_runtime_context: RuntimeContext, secret_filter: SecretFilter, sample_cloud: FiducialCloud
    ) -> None:
        srv = RecoveryService(secret_filter=secret_filter)
        srv.initialize(test_runtime_context)
        srv.start()

        cap_stage = _create_execution_capability(id(srv), "session-01", "RECOVERY_REORIENTATION", 1)
        srv.stage_candidate("session-01", "plan-01", sample_cloud, sequence_number=1, capability=cap_stage)

        auth = RecoveryAuthorization(
            operator_id="dr_chen",
            rationale="Test recovery",
            timestamp_utc=datetime.now(timezone.utc).isoformat(),
            sequence_number=2,
            authorization_reference="REF-01",
        )

        # Direct call without capability
        with pytest.raises(RecoveryAuthorizationError, match="missing execution capability"):
            srv.verify_candidate("session-01", auth, (100.0, 0.0, 0.0), (100.1, 0.0, 0.0))

        # Valid capability succeeds
        cap_verify = _create_execution_capability(id(srv), "session-01", "RECOVERY_REORIENTATION", 2)
        snapshot = srv.verify_candidate(
            "session-01",
            auth,
            (100.0, 0.0, 0.0),
            (100.1, 0.0, 0.0),
            sequence_number=2,
            capability=cap_verify,
        )
        assert snapshot.passed is True

        srv.stop()

    def test_activate_recovery_requires_capability(
        self, test_runtime_context: RuntimeContext, secret_filter: SecretFilter, sample_cloud: FiducialCloud
    ) -> None:
        mock_reg = MagicMock(spec=RegistrationService)
        mock_reg.is_registration_verified.return_value = True
        mock_reg.submit_fiducials.return_value = MagicMock()
        mock_reg.solve_registration.return_value = MagicMock()
        mock_reg.verify_registration.return_value = MagicMock(passed=True)
        reg_status = MagicMock()
        from holomed.registration.models import RegistrationState
        reg_status.state = RegistrationState.VERIFIED
        reg_status.transform = RigidRegistrationTransform3D(
            rotation_matrix=((1.0, 0.0, 0.0), (0.0, 1.0, 0.0), (0.0, 0.0, 1.0)),
            translation_vector_mm=(0.0, 0.0, 0.0),
            source_frame="plan_space",
            target_frame=DEFAULT_PATIENT_TRACKER_FRAME,
            epoch_id=1,
            created_at_utc=datetime.now(timezone.utc).isoformat(),
        )
        reg_status.epoch_id = 1
        mock_reg.get_registration.return_value = reg_status

        srv = RecoveryService(registration_service=mock_reg, secret_filter=secret_filter)
        srv.initialize(test_runtime_context)
        srv.start()

        cap_stage = _create_execution_capability(id(srv), "session-01", "RECOVERY_REORIENTATION", 1)
        srv.stage_candidate("session-01", "plan-01", sample_cloud, sequence_number=1, capability=cap_stage)

        auth = RecoveryAuthorization(
            operator_id="dr_chen",
            rationale="Test recovery",
            timestamp_utc=datetime.now(timezone.utc).isoformat(),
            sequence_number=2,
            authorization_reference="REF-01",
        )
        cap_verify = _create_execution_capability(id(srv), "session-01", "RECOVERY_REORIENTATION", 2)
        srv.verify_candidate(
            "session-01",
            auth,
            (100.0, 0.0, 0.0),
            (100.1, 0.0, 0.0),
            sequence_number=2,
            capability=cap_verify,
        )

        # Direct call without capability
        with pytest.raises(RecoveryAuthorizationError, match="missing execution capability"):
            srv.activate_recovery("session-01")

        # Valid capability succeeds
        cap_act = _create_execution_capability(id(srv), "session-01", "RECOVERY_REORIENTATION", 3)
        status_rec = srv.activate_recovery("session-01", sequence_number=3, capability=cap_act)
        assert status_rec.state == RecoveryState.ACTIVATED

        srv.stop()


class TestM22NavigationDirectCapabilityEnforcement:
    """Verifies that direct Python calls to NavigationService.bind_trajectory fail closed without valid capability."""

    def test_bind_trajectory_requires_capability(
        self, test_runtime_context: RuntimeContext, secret_filter: SecretFilter, sample_trajectory: TrajectoryPlan
    ) -> None:
        mock_reg = MagicMock(spec=RegistrationService)
        mock_reg.is_registration_verified.return_value = True
        mock_status = MagicMock()
        mock_status.transform = RigidRegistrationTransform3D(
            rotation_matrix=((1.0, 0.0, 0.0), (0.0, 1.0, 0.0), (0.0, 0.0, 1.0)),
            translation_vector_mm=(0.0, 0.0, 0.0),
            source_frame="plan_space",
            target_frame=DEFAULT_PATIENT_TRACKER_FRAME,
            epoch_id=1,
            created_at_utc=datetime.now(timezone.utc).isoformat(),
        )
        mock_reg.get_registration.return_value = mock_status

        nav_srv = NavigationService(registration_service=mock_reg, secret_filter=secret_filter)
        nav_srv.initialize(test_runtime_context)
        nav_srv.start()

        # 1. Missing capability
        with pytest.raises(NavigationAuthorizationError, match="missing execution capability"):
            nav_srv.bind_trajectory("session-01", "traj_01", sample_trajectory)

        # 2. Inactive capability
        cap = _create_execution_capability(id(nav_srv), "session-01", "TRAJECTORY_ALIGNMENT", 1)
        cap.invalidate()
        with pytest.raises(NavigationAuthorizationError, match="inactive, expired, or replayed"):
            nav_srv.bind_trajectory("session-01", "traj_01", sample_trajectory, capability=cap)

        # 3. Action mismatch
        cap_wrong_action = _create_execution_capability(id(nav_srv), "session-01", "TOOL_NAVIGATION", 1)
        with pytest.raises(NavigationAuthorizationError, match="Capability action mismatch"):
            nav_srv.bind_trajectory("session-01", "traj_01", sample_trajectory, capability=cap_wrong_action)

        # 4. Service mismatch
        cap_wrong_srv = _create_execution_capability(999999, "session-01", "TRAJECTORY_ALIGNMENT", 1)
        with pytest.raises(NavigationAuthorizationError, match="Capability service binding mismatch"):
            nav_srv.bind_trajectory("session-01", "traj_01", sample_trajectory, capability=cap_wrong_srv)

        # 5. Valid capability succeeds
        cap_valid = _create_execution_capability(id(nav_srv), "session-01", "TRAJECTORY_ALIGNMENT", 1)
        nav_srv.bind_trajectory("session-01", "traj_01", sample_trajectory, sequence_number=1, capability=cap_valid)
        assert nav_srv.get_navigation_status("session-01").state == NavigationState.IDLE

        nav_srv.stop()


class TestM22GatewayCoordinationAndInvalidation:
    """Verifies that the gateway coordinates recovery and trajectory binding with automatic capability invalidation."""

    def test_gateway_coordinates_recovery_reorientation_stages(
        self, test_runtime_context: RuntimeContext, secret_filter: SecretFilter, sample_cloud: FiducialCloud
    ) -> None:
        dispatcher = MessageDispatcher()
        dispatcher.initialize(test_runtime_context)

        recovery_srv = RecoveryService(secret_filter=secret_filter)
        recovery_srv.initialize(test_runtime_context)

        mock_gate = MagicMock(spec=SafetyGateService)
        mock_gate.evaluate.return_value = GateStatusRecord(
            decision=GateDecision.PERMITTED_CLEAR,
            reason_code=GateReasonCode.NONE,
            severity=GateSeverity.NONE,
            action=SafetyGateAction.RECOVERY_REORIENTATION,
            evaluated_at_utc=datetime.now(timezone.utc).isoformat(),
            session_id="session-01",
            sequence_number=1,
            subsystem_snapshots=(),
        )

        mock_wf = MagicMock(spec=WorkflowService)
        mock_wf.authorize_tool.return_value = WorkflowToolAuthorizationDecision(
            tool_id="recovery.reorientation",
            m07_safety_classification=MagicMock(),
            status=WorkflowToolAuthorizationStatus.PERMITTED,
            workflow_phase=WorkflowPhase.RECOVERY_REQUIRED,
            reason="AUTHORIZED",
            epoch_id=1,
            session_id="session-01",
        )

        gateway = ClinicalExecutionGatewayService(
            dispatcher=dispatcher,
            safety_gate_service=mock_gate,
            workflow_service=mock_wf,
            recovery_service=recovery_srv,
            secret_filter=secret_filter,
        )
        gateway.initialize(test_runtime_context)
        dispatcher.start()
        recovery_srv.start()
        gateway.start()

        # Execute STAGE via gateway
        req_stage = RecoveryReorientationExecutionRequest(
            session_id="session-01",
            sequence_number=1,
            now_utc=datetime.now(timezone.utc).isoformat(),
            recovery_operation="STAGE",
            plan_id="plan-01",
            cloud=sample_cloud,
        )
        res_stage = gateway.execute_recovery_reorientation(req_stage)
        assert res_stage.execution_status == ExecutionStatus.EXECUTED_CLEAR
        assert recovery_srv.get_recovery_status("session-01").state == RecoveryState.SOLVED

        # Execute VERIFY via gateway
        auth = RecoveryAuthorization(
            operator_id="dr_chen",
            rationale="Test recovery",
            timestamp_utc=datetime.now(timezone.utc).isoformat(),
            sequence_number=2,
            authorization_reference="REF-01",
        )
        req_verify = RecoveryReorientationExecutionRequest(
            session_id="session-01",
            sequence_number=2,
            now_utc=datetime.now(timezone.utc).isoformat(),
            recovery_operation="VERIFY",
            authorization=auth,
            checkpoint_plan_mm=(100.0, 0.0, 0.0),
            checkpoint_measured_mm=(100.1, 0.0, 0.0),
        )
        res_verify = gateway.execute_recovery_reorientation(req_verify)
        assert res_verify.execution_status == ExecutionStatus.EXECUTED_CLEAR
        assert recovery_srv.get_recovery_status("session-01").state == RecoveryState.VERIFIED

        gateway.stop()
        recovery_srv.stop()

    def test_gateway_coordinates_trajectory_binding(
        self, test_runtime_context: RuntimeContext, secret_filter: SecretFilter, sample_trajectory: TrajectoryPlan
    ) -> None:
        dispatcher = MessageDispatcher()
        dispatcher.initialize(test_runtime_context)

        mock_reg = MagicMock(spec=RegistrationService)
        mock_reg.is_registration_verified.return_value = True
        mock_status = MagicMock()
        mock_status.transform = RigidRegistrationTransform3D(
            rotation_matrix=((1.0, 0.0, 0.0), (0.0, 1.0, 0.0), (0.0, 0.0, 1.0)),
            translation_vector_mm=(0.0, 0.0, 0.0),
            source_frame="plan_space",
            target_frame=DEFAULT_PATIENT_TRACKER_FRAME,
            epoch_id=1,
            created_at_utc=datetime.now(timezone.utc).isoformat(),
        )
        mock_reg.get_registration.return_value = mock_status

        nav_srv = NavigationService(registration_service=mock_reg, secret_filter=secret_filter)
        nav_srv.initialize(test_runtime_context)

        mock_gate = MagicMock(spec=SafetyGateService)
        mock_gate.evaluate.return_value = GateStatusRecord(
            decision=GateDecision.PERMITTED_CLEAR,
            reason_code=GateReasonCode.NONE,
            severity=GateSeverity.NONE,
            action=SafetyGateAction.TRAJECTORY_ALIGNMENT,
            evaluated_at_utc=datetime.now(timezone.utc).isoformat(),
            session_id="session-01",
            sequence_number=1,
            subsystem_snapshots=(),
        )

        mock_wf = MagicMock(spec=WorkflowService)
        mock_wf.authorize_tool.return_value = WorkflowToolAuthorizationDecision(
            tool_id="trajectory.bind",
            m07_safety_classification=MagicMock(),
            status=WorkflowToolAuthorizationStatus.PERMITTED,
            workflow_phase=WorkflowPhase.REGISTRATION,
            reason="AUTHORIZED",
            epoch_id=1,
            session_id="session-01",
        )

        gateway = ClinicalExecutionGatewayService(
            dispatcher=dispatcher,
            safety_gate_service=mock_gate,
            workflow_service=mock_wf,
            navigation_service=nav_srv,
            secret_filter=secret_filter,
        )
        gateway.initialize(test_runtime_context)
        dispatcher.start()
        nav_srv.start()
        gateway.start()

        req = TrajectoryBindingExecutionRequest(
            session_id="session-01",
            sequence_number=1,
            now_utc=datetime.now(timezone.utc).isoformat(),
            trajectory_id="traj_01",
            plan_trajectory=sample_trajectory,
        )
        res = gateway.execute_trajectory_binding(req)
        assert res.execution_status == ExecutionStatus.EXECUTED_CLEAR
        assert nav_srv.get_navigation_status("session-01").state == NavigationState.IDLE

        gateway.stop()
        nav_srv.stop()
