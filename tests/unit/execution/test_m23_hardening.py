# -*- coding: utf-8 -*-
"""Authoritative Contract Compliance Tests for M23 Registration Capability Hardening."""

from __future__ import annotations

from unittest.mock import MagicMock
import pytest

from holomed.core.dispatcher import MessageDispatcher
from holomed.core.exceptions import UnroutableMessageError
from holomed.execution.models import (
    ExecutionStatus,
    RegistrationExecutionRequest,
    RegistrationExecutionResult,
)
from holomed.execution.service import ClinicalExecutionGatewayService
from holomed.execution._capability import _create_execution_capability
from holomed.planning.models import PatientCaseContext, SurgicalLaterality, SurgicalPlanDefinition, TrajectoryPlan
from holomed.planning.service import PlanningService
from holomed.protocol.builders import create_command, create_query
from holomed.recovery.models import RecoveryAuthorization
from holomed.recovery.service import RecoveryService
from holomed.registration.exceptions import (
    RegistrationAccuracyError,
    RegistrationAuthorizationError,
    RegistrationVerificationError,
)
from holomed.registration.models import (
    FiducialCloud,
    FiducialPointPair,
    RegistrationState,
)
from holomed.registration.service import RegistrationService
from holomed.runtime.context import RuntimeContext
from holomed.runtime.logging import SecretFilter
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
def message_dispatcher(runtime_context: RuntimeContext, secret_filter: SecretFilter) -> MessageDispatcher:
    disp = MessageDispatcher(secret_filter=secret_filter)
    disp.initialize(runtime_context)
    return disp


@pytest.fixture
def planning_service(runtime_context: RuntimeContext, secret_filter: SecretFilter) -> PlanningService:
    srv = PlanningService(secret_filter=secret_filter)
    srv.initialize(runtime_context)
    srv.start()
    return srv


@pytest.fixture
def sample_cloud() -> FiducialCloud:
    return FiducialCloud(
        pairs=(
            FiducialPointPair("f1", (0.0, 0.0, 0.0), (10.0, 20.0, 30.0), "landmark_1"),
            FiducialPointPair("f2", (50.0, 0.0, 0.0), (60.0, 20.0, 30.0), "landmark_2"),
            FiducialPointPair("f3", (25.0, 50.0, 0.0), (35.0, 70.0, 30.0), "landmark_3"),
        )
    )


@pytest.fixture
def sample_locked_plan(planning_service: PlanningService) -> SurgicalPlanDefinition:
    ctx = PatientCaseContext(
        case_id="case_m23",
        patient_hash="a" * 64,
        procedure_code="BIOPSY",
        laterality=SurgicalLaterality.RIGHT,
        primary_surgeon_id="surgeon_m23",
        scheduled_date_utc="2026-09-01T00:00:00Z",
    )
    plan = SurgicalPlanDefinition(
        plan_id="plan_m23",
        version="1.0",
        case_context=ctx,
        trajectories=(
            TrajectoryPlan("traj_m23", "target_m23", (10.0, 10.0, 10.0), (10.0, 10.0, 50.0), 3.0, 3.0, 0.85, 0.15),
        ),
        exclusion_zones=(),
        is_locked=True,
    )
    cap_sub = _create_execution_capability(id(planning_service), "sess_m23", "PLANNING_COORDINATION", 1)
    planning_service.submit_plan(plan, "sess_m23", capability=cap_sub)
    cap_lock = _create_execution_capability(id(planning_service), "sess_m23", "PLANNING_COORDINATION", 2)
    planning_service.lock_plan("plan_m23", capability=cap_lock)
    return plan


class TestM23DispatcherRouteRemoval:
    """Clauses 1-5: Old registration mutation routes unroutable, read-only query and execution route functional."""

    def test_raw_registration_routes_unroutable(
        self,
        runtime_context: RuntimeContext,
        message_dispatcher: MessageDispatcher,
        secret_filter: SecretFilter,
    ) -> None:
        reg_srv = RegistrationService(dispatcher=message_dispatcher, secret_filter=secret_filter)
        reg_srv.initialize(runtime_context)
        message_dispatcher.start()
        reg_srv.start()

        # registration.submit -> Unroutable
        with pytest.raises(UnroutableMessageError):
            message_dispatcher.dispatch(create_command("registration.submit", "console", payload={"session_id": "s1"}))

        # registration.solve -> Unroutable
        with pytest.raises(UnroutableMessageError):
            message_dispatcher.dispatch(create_command("registration.solve", "console", payload={"session_id": "s1"}))

        # registration.verify -> Unroutable
        with pytest.raises(UnroutableMessageError):
            message_dispatcher.dispatch(create_command("registration.verify", "console", payload={"session_id": "s1"}))

        # registration.get -> Routable
        q = create_query("registration.get", "console", payload={"session_id": "s1"})
        resp = message_dispatcher.dispatch(q)
        assert resp.message_type.value == "ERROR"
        assert resp.payload["error_code"] == "ERR_REGISTRATION_NOT_FOUND"

        reg_srv.stop()


class TestM23DirectCapabilityEnforcement:
    """Clauses 9-18: Direct invocation without valid capability fails closed."""

    def test_direct_submit_solve_verify_require_capability(
        self,
        runtime_context: RuntimeContext,
        secret_filter: SecretFilter,
        planning_service: PlanningService,
        sample_locked_plan: SurgicalPlanDefinition,
        sample_cloud: FiducialCloud,
    ) -> None:
        reg_srv = RegistrationService(planning_service=planning_service, secret_filter=secret_filter)
        reg_srv.initialize(runtime_context)
        reg_srv.start()

        # 1. Missing capability
        with pytest.raises(RegistrationAuthorizationError):
            reg_srv.submit_fiducials("sess_cap", sample_locked_plan.plan_id, sample_cloud)
        with pytest.raises(RegistrationAuthorizationError):
            reg_srv.solve_registration("sess_cap", sample_locked_plan.plan_id)
        with pytest.raises(RegistrationAuthorizationError):
            reg_srv.verify_registration("sess_cap", "op", (0.0, 0.0, 0.0), (10.0, 20.0, 30.0))

        # 2. Inactive capability
        cap = _create_execution_capability(
            service_instance_id=id(reg_srv),
            session_id="sess_cap",
            action="REGISTRATION_ALIGNMENT",
            sequence_number=1,
        )
        cap.invalidate()
        with pytest.raises(RegistrationAuthorizationError):
            reg_srv.submit_fiducials("sess_cap", sample_locked_plan.plan_id, sample_cloud, capability=cap, sequence_number=1)

        # 3. Wrong action
        cap_wrong_action = _create_execution_capability(
            service_instance_id=id(reg_srv),
            session_id="sess_cap",
            action="TOOL_NAVIGATION",
            sequence_number=1,
        )
        with pytest.raises(RegistrationAuthorizationError):
            reg_srv.submit_fiducials("sess_cap", sample_locked_plan.plan_id, sample_cloud, capability=cap_wrong_action, sequence_number=1)

        # 4. Wrong session
        cap_wrong_sess = _create_execution_capability(
            service_instance_id=id(reg_srv),
            session_id="other_sess",
            action="REGISTRATION_ALIGNMENT",
            sequence_number=1,
        )
        with pytest.raises(RegistrationAuthorizationError):
            reg_srv.submit_fiducials("sess_cap", sample_locked_plan.plan_id, sample_cloud, capability=cap_wrong_sess, sequence_number=1)

        # 5. Wrong sequence
        cap_seq = _create_execution_capability(
            service_instance_id=id(reg_srv),
            session_id="sess_cap",
            action="REGISTRATION_ALIGNMENT",
            sequence_number=1,
        )
        with pytest.raises(RegistrationAuthorizationError):
            reg_srv.submit_fiducials("sess_cap", sample_locked_plan.plan_id, sample_cloud, capability=cap_seq, sequence_number=99)

        # 6. Wrong service instance
        class FakeSrv:
            pass
        cap_fake = _create_execution_capability(
            service_instance_id=id(FakeSrv()),
            session_id="sess_cap",
            action="REGISTRATION_ALIGNMENT",
            sequence_number=1,
        )
        with pytest.raises(RegistrationAuthorizationError):
            reg_srv.submit_fiducials("sess_cap", sample_locked_plan.plan_id, sample_cloud, capability=cap_fake, sequence_number=1)

        reg_srv.stop()


class TestM23GatewayRegistrationCoordination:
    """Clauses 6-8, 10-12, 19-23: Execution through ClinicalExecutionGatewayService."""

    def test_gateway_full_registration_lifecycle_e2e(
        self,
        runtime_context: RuntimeContext,
        message_dispatcher: MessageDispatcher,
        secret_filter: SecretFilter,
        planning_service: PlanningService,
        sample_locked_plan: SurgicalPlanDefinition,
        sample_cloud: FiducialCloud,
    ) -> None:
        reg_srv = RegistrationService(planning_service=planning_service, secret_filter=secret_filter)
        reg_srv.initialize(runtime_context)
        reg_srv.start()

        # Mock SafetyGateService: Permit
        mock_gate = MagicMock(spec=SafetyGateService)
        mock_gate.evaluate.return_value = GateStatusRecord(
            session_id="sess_gw",
            decision=GateDecision.PERMITTED_CLEAR,
            severity=GateSeverity.NONE,
            reason_code=GateReasonCode.NONE,
            action=SafetyGateAction.TRAJECTORY_ALIGNMENT,
            sequence_number=1,
            subsystem_snapshots=(),
            evaluated_at_utc="2026-09-01T12:00:00Z",
        )

        # Mock WorkflowService: Authorize
        mock_wf = MagicMock(spec=WorkflowService)
        mock_wf.authorize_tool.return_value = WorkflowToolAuthorizationDecision(
            tool_id="registration.submit",
            m07_safety_classification=ToolSafetyClassification.READ_ONLY_INFORMATIVE,
            status=WorkflowToolAuthorizationStatus.PERMITTED,
            workflow_phase=WorkflowPhase.REGISTRATION,
            reason="Permitted in REGISTRATION phase",
            epoch_id=1,
            session_id="sess_gw",
        )

        gw = ClinicalExecutionGatewayService(
            dispatcher=message_dispatcher,
            safety_gate_service=mock_gate,
            workflow_service=mock_wf,
            registration_service=reg_srv,
            secret_filter=secret_filter,
        )
        gw.initialize(runtime_context)
        message_dispatcher.start()
        gw.start()

        # 1. SUBMIT via Gateway
        req_sub = RegistrationExecutionRequest(
            session_id="sess_gw",
            sequence_number=1,
            now_utc="2026-09-01T12:00:00Z",
            operation="SUBMIT",
            plan_id=sample_locked_plan.plan_id,
            cloud=sample_cloud,
        )
        res_sub = gw.execute_registration(req_sub)
        assert res_sub.execution_status == ExecutionStatus.EXECUTED_CLEAR
        assert res_sub.registration_record is not None
        assert res_sub.registration_record.state == RegistrationState.DRAFT

        # 2. SOLVE via Gateway
        mock_wf.authorize_tool.return_value = WorkflowToolAuthorizationDecision(
            tool_id="registration.solve",
            m07_safety_classification=ToolSafetyClassification.READ_ONLY_INFORMATIVE,
            status=WorkflowToolAuthorizationStatus.PERMITTED,
            workflow_phase=WorkflowPhase.REGISTRATION,
            reason="Permitted in REGISTRATION phase",
            epoch_id=1,
            session_id="sess_gw",
        )
        req_solve = RegistrationExecutionRequest(
            session_id="sess_gw",
            sequence_number=2,
            now_utc="2026-09-01T12:00:01Z",
            operation="SOLVE",
            plan_id=sample_locked_plan.plan_id,
        )
        res_solve = gw.execute_registration(req_solve)
        assert res_solve.execution_status == ExecutionStatus.EXECUTED_CLEAR
        assert res_solve.registration_record is not None
        assert res_solve.registration_record.state == RegistrationState.SOLVED

        # 3. VERIFY via Gateway
        mock_wf.authorize_tool.return_value = WorkflowToolAuthorizationDecision(
            tool_id="registration.verify",
            m07_safety_classification=ToolSafetyClassification.READ_ONLY_INFORMATIVE,
            status=WorkflowToolAuthorizationStatus.PERMITTED,
            workflow_phase=WorkflowPhase.SAFETY_TIMEOUT,
            reason="Permitted in SAFETY_TIMEOUT phase",
            epoch_id=1,
            session_id="sess_gw",
        )
        req_ver = RegistrationExecutionRequest(
            session_id="sess_gw",
            sequence_number=3,
            now_utc="2026-09-01T12:00:02Z",
            operation="VERIFY",
            operator_id="surgeon_01",
            checkpoint_plan_mm=(0.0, 0.0, 0.0),
            checkpoint_measured_mm=(10.0, 20.0, 30.0),
        )
        res_ver = gw.execute_registration(req_ver)
        assert res_ver.execution_status == ExecutionStatus.EXECUTED_CLEAR
        assert res_ver.verification_snapshot is not None
        assert res_ver.verification_snapshot.passed is True
        assert reg_srv.is_registration_verified("sess_gw") is True

        # 4. Also verify dispatcher command execution.registration.execute
        cmd = create_command(
            "execution.registration.execute",
            "surgeon_console",
            payload={
                "session_id": "sess_gw",
                "sequence_number": 4,
                "now_utc": "2026-09-01T12:00:03Z",
                "operation": "SOLVE",
                "plan_id": sample_locked_plan.plan_id,
            },
        )
        resp = message_dispatcher.dispatch(cmd)
        assert resp.message_type.value == "RESPONSE"
        assert resp.payload["execution_status"] == "EXECUTED_CLEAR"

        gw.stop()
        reg_srv.stop()

    def test_gateway_blocks_when_m18_safety_denies(
        self,
        runtime_context: RuntimeContext,
        secret_filter: SecretFilter,
        planning_service: PlanningService,
        sample_locked_plan: SurgicalPlanDefinition,
        sample_cloud: FiducialCloud,
    ) -> None:
        reg_srv = RegistrationService(planning_service=planning_service, secret_filter=secret_filter)
        reg_srv.initialize(runtime_context)
        reg_srv.start()

        mock_gate = MagicMock(spec=SafetyGateService)
        mock_gate.evaluate.return_value = GateStatusRecord(
            session_id="sess_block_m18",
            decision=GateDecision.DENIED_INTERLOCKED,
            severity=GateSeverity.CRITICAL,
            reason_code=GateReasonCode.CRITICAL_EXCLUSION_ZONE_BREACH,
            action=SafetyGateAction.TRAJECTORY_ALIGNMENT,
            sequence_number=1,
            subsystem_snapshots=(),
            evaluated_at_utc="2026-09-01T12:00:00Z",
        )

        gw = ClinicalExecutionGatewayService(
            safety_gate_service=mock_gate,
            registration_service=reg_srv,
            secret_filter=secret_filter,
        )
        gw.initialize(runtime_context)
        gw.start()

        req = RegistrationExecutionRequest(
            session_id="sess_block_m18",
            sequence_number=1,
            now_utc="2026-09-01T12:00:00Z",
            operation="SUBMIT",
            plan_id=sample_locked_plan.plan_id,
            cloud=sample_cloud,
        )
        res = gw.execute_registration(req)
        assert res.execution_status == ExecutionStatus.BLOCKED_SAFETY_GATE
        assert res.gate_decision == GateDecision.DENIED_INTERLOCKED
        # Registration service was NOT touched
        assert reg_srv.get_registration("sess_block_m18") is None

        gw.stop()
        reg_srv.stop()

    def test_gateway_blocks_when_m10_workflow_denies(
        self,
        runtime_context: RuntimeContext,
        secret_filter: SecretFilter,
        planning_service: PlanningService,
        sample_locked_plan: SurgicalPlanDefinition,
        sample_cloud: FiducialCloud,
    ) -> None:
        reg_srv = RegistrationService(planning_service=planning_service, secret_filter=secret_filter)
        reg_srv.initialize(runtime_context)
        reg_srv.start()

        mock_gate = MagicMock(spec=SafetyGateService)
        mock_gate.evaluate.return_value = GateStatusRecord(
            session_id="sess_block_m10",
            decision=GateDecision.PERMITTED_CLEAR,
            severity=GateSeverity.NONE,
            reason_code=GateReasonCode.NONE,
            action=SafetyGateAction.TRAJECTORY_ALIGNMENT,
            sequence_number=1,
            subsystem_snapshots=(),
            evaluated_at_utc="2026-09-01T12:00:00Z",
        )

        mock_wf = MagicMock(spec=WorkflowService)
        mock_wf.authorize_tool.return_value = WorkflowToolAuthorizationDecision(
            tool_id="registration.submit",
            m07_safety_classification=ToolSafetyClassification.READ_ONLY_INFORMATIVE,
            status=WorkflowToolAuthorizationStatus.BLOCKED_PHASE,
            workflow_phase=WorkflowPhase.INTERVENTION,
            reason="Blocked in INTERVENTION phase",
            epoch_id=1,
            session_id="sess_block_m10",
        )

        gw = ClinicalExecutionGatewayService(
            safety_gate_service=mock_gate,
            workflow_service=mock_wf,
            registration_service=reg_srv,
            secret_filter=secret_filter,
        )
        gw.initialize(runtime_context)
        gw.start()

        req = RegistrationExecutionRequest(
            session_id="sess_block_m10",
            sequence_number=1,
            now_utc="2026-09-01T12:00:00Z",
            operation="SUBMIT",
            plan_id=sample_locked_plan.plan_id,
            cloud=sample_cloud,
        )
        res = gw.execute_registration(req)
        assert res.execution_status == ExecutionStatus.BLOCKED_WORKFLOW
        assert res.workflow_status == WorkflowToolAuthorizationStatus.BLOCKED_PHASE
        assert reg_srv.get_registration("sess_block_m10") is None

        gw.stop()
        reg_srv.stop()

    def test_gateway_handles_solver_fre_failure(
        self,
        runtime_context: RuntimeContext,
        secret_filter: SecretFilter,
        planning_service: PlanningService,
        sample_locked_plan: SurgicalPlanDefinition,
    ) -> None:
        reg_srv = RegistrationService(planning_service=planning_service, secret_filter=secret_filter)
        reg_srv.initialize(runtime_context)
        reg_srv.start()

        noisy_cloud = FiducialCloud(
            pairs=(
                FiducialPointPair("f1", (0.0, 0.0, 0.0), (0.0, 0.0, 0.0), "p1"),
                FiducialPointPair("f2", (50.0, 0.0, 0.0), (50.0, 0.0, 0.0), "p2"),
                FiducialPointPair("f3", (0.0, 50.0, 0.0), (0.0, 50.0, 0.0), "p3"),
                FiducialPointPair("f4", (50.0, 50.0, 0.0), (50.0, 50.0, 10.0), "p4"),
            )
        )

        mock_gate = MagicMock(spec=SafetyGateService)
        mock_gate.evaluate.return_value = GateStatusRecord(
            session_id="sess_fre_fail",
            decision=GateDecision.PERMITTED_CLEAR,
            severity=GateSeverity.NONE,
            reason_code=GateReasonCode.NONE,
            action=SafetyGateAction.TRAJECTORY_ALIGNMENT,
            sequence_number=1,
            subsystem_snapshots=(),
            evaluated_at_utc="2026-09-01T12:00:00Z",
        )

        mock_wf = MagicMock(spec=WorkflowService)
        mock_wf.authorize_tool.return_value = WorkflowToolAuthorizationDecision(
            tool_id="registration.solve",
            m07_safety_classification=ToolSafetyClassification.READ_ONLY_INFORMATIVE,
            status=WorkflowToolAuthorizationStatus.PERMITTED,
            workflow_phase=WorkflowPhase.REGISTRATION,
            reason="Permitted",
            epoch_id=1,
            session_id="sess_fre_fail",
        )

        gw = ClinicalExecutionGatewayService(
            safety_gate_service=mock_gate,
            workflow_service=mock_wf,
            registration_service=reg_srv,
            secret_filter=secret_filter,
        )
        gw.initialize(runtime_context)
        gw.start()

        # Ingest noisy cloud first
        cap = _create_execution_capability(
            service_instance_id=id(reg_srv),
            session_id="sess_fre_fail",
            action="REGISTRATION_ALIGNMENT",
            sequence_number=1,
        )
        reg_srv.submit_fiducials("sess_fre_fail", sample_locked_plan.plan_id, noisy_cloud, capability=cap, sequence_number=1)
        cap.invalidate()

        req_solve = RegistrationExecutionRequest(
            session_id="sess_fre_fail",
            sequence_number=2,
            now_utc="2026-09-01T12:00:01Z",
            operation="SOLVE",
            plan_id=sample_locked_plan.plan_id,
        )
        res_solve = gw.execute_registration(req_solve)
        assert res_solve.execution_status == ExecutionStatus.FAILED_NAVIGATION_GEOMETRY
        assert "exceeds limit" in (res_solve.error_message or "")
        reg = reg_srv.get_registration("sess_fre_fail")
        assert reg is not None
        assert reg.state == RegistrationState.FAILED

        gw.stop()
        reg_srv.stop()
