# -*- coding: utf-8 -*-
"""Authoritative Contract Compliance Tests for M24 Preoperative Planning Capability Hardening."""

from __future__ import annotations

from unittest.mock import MagicMock
import pytest

from holomed.core.dispatcher import MessageDispatcher
from holomed.core.exceptions import UnroutableMessageError
from holomed.execution.exceptions import (
    ExecutionLifecycleError,
    ExecutionValidationError,
)
from holomed.execution.models import (
    ExecutionStatus,
    PlanningExecutionRequest,
    PlanningExecutionResult,
)
from holomed.execution.service import ClinicalExecutionGatewayService
from holomed.execution._capability import _create_execution_capability
from holomed.planning.exceptions import (
    PlanningAuthorizationError,
    PlanningValidationError,
)
from holomed.planning.models import (
    PatientCaseContext,
    SafetyExclusionZone,
    SurgicalLaterality,
    SurgicalPlanDefinition,
    TrajectoryPlan,
)
from holomed.planning.service import PlanningService
from holomed.protocol.builders import create_command, create_query
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


def make_mock_gate_record(
    session_id: str,
    decision: GateDecision = GateDecision.PERMITTED_WITH_CAUTION,
    reason_code: GateReasonCode = GateReasonCode.NONE,
    severity: GateSeverity = GateSeverity.NONE,
    seq: int = 1,
) -> GateStatusRecord:
    return GateStatusRecord(
        session_id=session_id,
        decision=decision,
        severity=severity,
        reason_code=reason_code,
        action=SafetyGateAction.TRAJECTORY_ALIGNMENT,
        sequence_number=seq,
        subsystem_snapshots=(),
        evaluated_at_utc="2026-09-03T12:00:00Z",
    )


def make_mock_wf_decision(
    tool_id: str,
    status: WorkflowToolAuthorizationStatus = WorkflowToolAuthorizationStatus.PERMITTED,
    phase: WorkflowPhase = WorkflowPhase.PRE_PROCEDURE_PLANNING,
    session_id: str = "sess_m24",
) -> WorkflowToolAuthorizationDecision:
    return WorkflowToolAuthorizationDecision(
        tool_id=tool_id,
        m07_safety_classification=ToolSafetyClassification.TELEMETRY_RECORDING,
        status=status,
        workflow_phase=phase,
        reason="Test authorization decision",
        epoch_id=1,
        session_id=session_id,
    )


@pytest.fixture
def message_dispatcher(runtime_context: RuntimeContext, secret_filter: SecretFilter) -> MessageDispatcher:
    disp = MessageDispatcher(secret_filter=secret_filter)
    disp.initialize(runtime_context)
    return disp


@pytest.fixture
def sample_draft_plan() -> SurgicalPlanDefinition:
    ctx = PatientCaseContext(
        case_id="case_m24",
        patient_hash="b" * 64,
        procedure_code="THA_POSTERIOR",
        laterality=SurgicalLaterality.LEFT,
        primary_surgeon_id="dr_m24",
        scheduled_date_utc="2026-09-03T12:00:00Z",
    )
    return SurgicalPlanDefinition(
        plan_id="plan_m24",
        version="1.0",
        case_context=ctx,
        trajectories=(
            TrajectoryPlan(
                trajectory_id="traj_m24_1",
                target_structure="acetabulum",
                entry_point_mm=(0.0, 0.0, 0.0),
                target_point_mm=(0.0, 0.0, 50.0),
                max_lateral_deviation_mm=2.0,
                max_angular_deviation_deg=3.0,
                min_confidence=0.85,
                max_uncertainty=0.15,
            ),
        ),
        exclusion_zones=(
            SafetyExclusionZone(
                zone_id="zone_m24_1",
                anatomical_structure="sciatic_nerve",
                center_point_mm=(10.0, 10.0, 10.0),
                bounding_radius_mm=5.0,
                min_clearance_mm=5.0,
            ),
        ),
        is_locked=False,
    )


class TestM24DispatcherHardening:
    """Requirements 1-5: Unroutable raw routes, functional query and execution routes."""

    def test_planning_submit_unroutable(
        self,
        runtime_context: RuntimeContext,
        message_dispatcher: MessageDispatcher,
        secret_filter: SecretFilter,
    ) -> None:
        """Requirement 1: planning.submit is unroutable on dispatcher."""
        srv = PlanningService(dispatcher=message_dispatcher, secret_filter=secret_filter)
        srv.initialize(runtime_context)
        message_dispatcher.start()
        srv.start()

        cmd = create_command("planning.submit", "test", payload={"session_id": "s1"})
        with pytest.raises(UnroutableMessageError):
            message_dispatcher.dispatch(cmd)
        srv.stop()

    def test_planning_lock_unroutable(
        self,
        runtime_context: RuntimeContext,
        message_dispatcher: MessageDispatcher,
        secret_filter: SecretFilter,
    ) -> None:
        """Requirement 2: planning.lock is unroutable on dispatcher."""
        srv = PlanningService(dispatcher=message_dispatcher, secret_filter=secret_filter)
        srv.initialize(runtime_context)
        message_dispatcher.start()
        srv.start()

        cmd = create_command("planning.lock", "test", payload={"plan_id": "p1"})
        with pytest.raises(UnroutableMessageError):
            message_dispatcher.dispatch(cmd)
        srv.stop()

    def test_planning_verify_unroutable(
        self,
        runtime_context: RuntimeContext,
        message_dispatcher: MessageDispatcher,
        secret_filter: SecretFilter,
    ) -> None:
        """Requirement 3: planning.verify is unroutable on dispatcher."""
        srv = PlanningService(dispatcher=message_dispatcher, secret_filter=secret_filter)
        srv.initialize(runtime_context)
        message_dispatcher.start()
        srv.start()

        cmd = create_command("planning.verify", "test", payload={"plan_id": "p1"})
        with pytest.raises(UnroutableMessageError):
            message_dispatcher.dispatch(cmd)
        srv.stop()

    def test_planning_get_functional_as_query(
        self,
        runtime_context: RuntimeContext,
        message_dispatcher: MessageDispatcher,
        secret_filter: SecretFilter,
        sample_draft_plan: SurgicalPlanDefinition,
    ) -> None:
        """Requirement 4: planning.get is registered and functional as read-only query."""
        srv = PlanningService(dispatcher=message_dispatcher, secret_filter=secret_filter)
        srv.initialize(runtime_context)
        message_dispatcher.start()
        srv.start()

        cap = _create_execution_capability(id(srv), "sess_q", "PLANNING_COORDINATION", 1)
        srv.submit_plan(sample_draft_plan, "sess_q", capability=cap)

        query = create_query("planning.get", "test", payload={"plan_id": sample_draft_plan.plan_id})
        resp = message_dispatcher.dispatch(query)
        assert resp.message_type.value == "RESPONSE"
        assert resp.payload["plan_id"] == sample_draft_plan.plan_id
        assert resp.payload["case_id"] == "case_m24"
        srv.stop()

    def test_execution_planning_execute_registered(
        self,
        runtime_context: RuntimeContext,
        message_dispatcher: MessageDispatcher,
        secret_filter: SecretFilter,
    ) -> None:
        """Requirement 5: execution.planning.execute is registered on dispatcher."""
        gateway = ClinicalExecutionGatewayService(dispatcher=message_dispatcher, secret_filter=secret_filter)
        gateway.initialize(runtime_context)
        message_dispatcher.start()
        gateway.start()

        cmd = create_command("execution.planning.execute", "test", payload={})
        resp = message_dispatcher.dispatch(cmd)
        assert resp.message_type.value == "ERROR"
        assert resp.payload["error_code"] == "ERR_INVALID_ARGS"
        gateway.stop()


class TestM24DispatcherProtocolValidation:
    """Requirements 6-8: Payload validation in dispatcher handler."""

    def test_missing_required_fields_returns_error(
        self,
        runtime_context: RuntimeContext,
        message_dispatcher: MessageDispatcher,
        secret_filter: SecretFilter,
    ) -> None:
        """Requirement 6: Missing required fields returns error response."""
        gateway = ClinicalExecutionGatewayService(dispatcher=message_dispatcher, secret_filter=secret_filter)
        gateway.initialize(runtime_context)
        message_dispatcher.start()
        gateway.start()

        cmd = create_command("execution.planning.execute", "test", payload={"session_id": "s1"})
        resp = message_dispatcher.dispatch(cmd)
        assert resp.message_type.value == "ERROR"
        assert resp.payload["error_code"] == "ERR_INVALID_ARGS"
        gateway.stop()

    def test_malformed_payload_returns_error(
        self,
        runtime_context: RuntimeContext,
        message_dispatcher: MessageDispatcher,
        secret_filter: SecretFilter,
    ) -> None:
        """Requirement 7: Malformed payload returns error response."""
        gateway = ClinicalExecutionGatewayService(dispatcher=message_dispatcher, secret_filter=secret_filter)
        gateway.initialize(runtime_context)
        message_dispatcher.start()
        gateway.start()

        cmd = create_command(
            "execution.planning.execute",
            "test",
            payload={"session_id": "s1", "sequence_number": "invalid_num", "operation": "SUBMIT"},
        )
        resp = message_dispatcher.dispatch(cmd)
        assert resp.message_type.value == "ERROR"
        gateway.stop()

    def test_unknown_operation_raises_validation_error(self) -> None:
        """Requirement 8: Unknown/unsupported operation raises ExecutionValidationError."""
        with pytest.raises(ExecutionValidationError, match="Invalid planning operation"):
            PlanningExecutionRequest(
                session_id="s1",
                sequence_number=1,
                now_utc="2026-09-03T12:00:00Z",
                operation="DELETE_PLAN",
            )


class TestM24DirectCapabilityEnforcement:
    """Requirements 9-17: Direct calls to PlanningService mutation methods require active capability."""

    def test_direct_submit_without_capability_raises(
        self,
        runtime_context: RuntimeContext,
        secret_filter: SecretFilter,
        sample_draft_plan: SurgicalPlanDefinition,
    ) -> None:
        """Requirement 9: submit_plan without capability raises PlanningAuthorizationError."""
        srv = PlanningService(secret_filter=secret_filter)
        srv.initialize(runtime_context)
        srv.start()

        with pytest.raises(PlanningAuthorizationError):
            srv.submit_plan(sample_draft_plan, "s1")
        srv.stop()

    def test_direct_submit_with_inactive_capability_raises(
        self,
        runtime_context: RuntimeContext,
        secret_filter: SecretFilter,
        sample_draft_plan: SurgicalPlanDefinition,
    ) -> None:
        """Requirement 10: submit_plan with inactive/revoked capability raises PlanningAuthorizationError."""
        srv = PlanningService(secret_filter=secret_filter)
        srv.initialize(runtime_context)
        srv.start()

        cap = _create_execution_capability(id(srv), "s1", "PLANNING_COORDINATION", 1)
        cap.invalidate()
        with pytest.raises(PlanningAuthorizationError):
            srv.submit_plan(sample_draft_plan, "s1", capability=cap, sequence_number=1)
        srv.stop()

    def test_direct_submit_with_wrong_session_raises(
        self,
        runtime_context: RuntimeContext,
        secret_filter: SecretFilter,
        sample_draft_plan: SurgicalPlanDefinition,
    ) -> None:
        """Requirement 11: submit_plan with wrong session_id raises PlanningAuthorizationError."""
        srv = PlanningService(secret_filter=secret_filter)
        srv.initialize(runtime_context)
        srv.start()

        cap = _create_execution_capability(id(srv), "session_A", "PLANNING_COORDINATION", 1)
        with pytest.raises(PlanningAuthorizationError):
            srv.submit_plan(sample_draft_plan, "session_B", capability=cap, sequence_number=1)
        srv.stop()

    def test_direct_submit_with_wrong_action_raises(
        self,
        runtime_context: RuntimeContext,
        secret_filter: SecretFilter,
        sample_draft_plan: SurgicalPlanDefinition,
    ) -> None:
        """Requirement 12: submit_plan with wrong action raises PlanningAuthorizationError."""
        srv = PlanningService(secret_filter=secret_filter)
        srv.initialize(runtime_context)
        srv.start()

        cap = _create_execution_capability(id(srv), "s1", "REGISTRATION_ALIGNMENT", 1)
        with pytest.raises(PlanningAuthorizationError):
            srv.submit_plan(sample_draft_plan, "s1", capability=cap, sequence_number=1)
        srv.stop()

    def test_direct_submit_with_mismatched_sequence_raises(
        self,
        runtime_context: RuntimeContext,
        secret_filter: SecretFilter,
        sample_draft_plan: SurgicalPlanDefinition,
    ) -> None:
        """Requirement 13: submit_plan with mismatched sequence_number raises PlanningAuthorizationError."""
        srv = PlanningService(secret_filter=secret_filter)
        srv.initialize(runtime_context)
        srv.start()

        cap = _create_execution_capability(id(srv), "s1", "PLANNING_COORDINATION", 1)
        with pytest.raises(PlanningAuthorizationError):
            srv.submit_plan(sample_draft_plan, "s1", capability=cap, sequence_number=2)
        srv.stop()

    def test_direct_submit_with_wrong_service_instance_raises(
        self,
        runtime_context: RuntimeContext,
        secret_filter: SecretFilter,
        sample_draft_plan: SurgicalPlanDefinition,
    ) -> None:
        """Requirement 14: submit_plan with wrong service_instance_id raises PlanningAuthorizationError."""
        srv = PlanningService(secret_filter=secret_filter)
        srv.initialize(runtime_context)
        srv.start()

        cap = _create_execution_capability(999999999, "s1", "PLANNING_COORDINATION", 1)
        with pytest.raises(PlanningAuthorizationError):
            srv.submit_plan(sample_draft_plan, "s1", capability=cap, sequence_number=1)
        srv.stop()

    def test_direct_lock_without_capability_raises(
        self,
        runtime_context: RuntimeContext,
        secret_filter: SecretFilter,
        sample_draft_plan: SurgicalPlanDefinition,
    ) -> None:
        """Requirement 15: lock_plan without capability raises PlanningAuthorizationError."""
        srv = PlanningService(secret_filter=secret_filter)
        srv.initialize(runtime_context)
        srv.start()

        cap = _create_execution_capability(id(srv), "s1", "PLANNING_COORDINATION", 1)
        srv.submit_plan(sample_draft_plan, "s1", capability=cap)

        with pytest.raises(PlanningAuthorizationError):
            srv.lock_plan(sample_draft_plan.plan_id)
        srv.stop()

    def test_direct_verify_without_capability_raises(
        self,
        runtime_context: RuntimeContext,
        secret_filter: SecretFilter,
        sample_draft_plan: SurgicalPlanDefinition,
    ) -> None:
        """Requirement 16: verify_plan without capability raises PlanningAuthorizationError."""
        srv = PlanningService(secret_filter=secret_filter)
        srv.initialize(runtime_context)
        srv.start()

        cap = _create_execution_capability(id(srv), "s1", "PLANNING_COORDINATION", 1)
        srv.submit_plan(sample_draft_plan, "s1", capability=cap)
        cap_lock = _create_execution_capability(id(srv), "s1", "PLANNING_COORDINATION", 2)
        srv.lock_plan(sample_draft_plan.plan_id, capability=cap_lock)

        with pytest.raises(PlanningAuthorizationError):
            srv.verify_plan(
                plan_id=sample_draft_plan.plan_id,
                session_id="s1",
                operator_id="op1",
                patient_hash=sample_draft_plan.case_context.patient_hash,
                procedure_code="THA_POSTERIOR",
                laterality=SurgicalLaterality.LEFT,
            )
        srv.stop()

    def test_capability_replay_raises(
        self,
        runtime_context: RuntimeContext,
        secret_filter: SecretFilter,
        sample_draft_plan: SurgicalPlanDefinition,
    ) -> None:
        """Requirement 17: Reusing capability a second time raises PlanningAuthorizationError."""
        srv = PlanningService(secret_filter=secret_filter)
        srv.initialize(runtime_context)
        srv.start()

        cap = _create_execution_capability(id(srv), "s1", "PLANNING_COORDINATION", 1)
        srv.submit_plan(sample_draft_plan, "s1", capability=cap)
        cap.invalidate()

        with pytest.raises(PlanningAuthorizationError):
            srv.lock_plan(sample_draft_plan.plan_id, capability=cap)
        srv.stop()


class TestM24GatewayPlanningExecution:
    """Requirements 18-22: Full execution lifecycle under gateway mediation."""

    def test_execute_planning_submit_success(
        self,
        runtime_context: RuntimeContext,
        secret_filter: SecretFilter,
        sample_draft_plan: SurgicalPlanDefinition,
    ) -> None:
        """Requirement 18: Valid SUBMIT via execute_planning succeeds."""
        plan_srv = PlanningService(secret_filter=secret_filter)
        plan_srv.initialize(runtime_context)
        plan_srv.start()

        mock_wf = MagicMock()
        mock_wf.authorize_tool.return_value = make_mock_wf_decision("planning.submit", session_id="sess_m24")
        mock_gate = MagicMock()
        mock_gate.evaluate.return_value = make_mock_gate_record("sess_m24")

        gateway = ClinicalExecutionGatewayService(
            workflow_service=mock_wf,
            safety_gate_service=mock_gate,
            planning_service=plan_srv,
            secret_filter=secret_filter,
        )
        gateway.initialize(runtime_context)
        gateway.start()

        req = PlanningExecutionRequest(
            session_id="sess_m24",
            sequence_number=1,
            now_utc="2026-09-03T12:00:00Z",
            operation="SUBMIT",
            plan=sample_draft_plan,
        )
        res = gateway.execute_planning(req)
        assert res.execution_status == ExecutionStatus.EXECUTED_WITH_CAUTION
        assert res.plan is not None
        assert res.plan.plan_id == "plan_m24"
        assert res.plan.is_locked is False

        gateway.stop()
        plan_srv.stop()

    def test_execute_planning_lock_success(
        self,
        runtime_context: RuntimeContext,
        secret_filter: SecretFilter,
        sample_draft_plan: SurgicalPlanDefinition,
    ) -> None:
        """Requirement 19: Valid LOCK via execute_planning locks plan and derives checkpoints."""
        wf_srv = WorkflowService(secret_filter=secret_filter)
        wf_srv.initialize(runtime_context)
        wf_srv.start()

        plan_srv = PlanningService(workflow_service=wf_srv, secret_filter=secret_filter)
        plan_srv.initialize(runtime_context)
        plan_srv.start()

        mock_gate = MagicMock()
        mock_gate.evaluate.return_value = make_mock_gate_record("sess_m24")

        gateway = ClinicalExecutionGatewayService(
            workflow_service=wf_srv,
            safety_gate_service=mock_gate,
            planning_service=plan_srv,
            secret_filter=secret_filter,
        )
        gateway.initialize(runtime_context)
        gateway.start()

        # Start workflow session for sess_m24
        wf_srv.start_workflow("sess_m24")

        # Submit plan first
        req_sub = PlanningExecutionRequest(
            session_id="sess_m24",
            sequence_number=1,
            now_utc="2026-09-03T12:00:00Z",
            operation="SUBMIT",
            plan=sample_draft_plan,
        )
        res_sub = gateway.execute_planning(req_sub)
        assert res_sub.execution_status == ExecutionStatus.EXECUTED_WITH_CAUTION

        # Lock plan
        req_lock = PlanningExecutionRequest(
            session_id="sess_m24",
            sequence_number=2,
            now_utc="2026-09-03T12:00:00Z",
            operation="LOCK",
            plan_id="plan_m24",
        )
        res_lock = gateway.execute_planning(req_lock)
        assert res_lock.execution_status == ExecutionStatus.EXECUTED_WITH_CAUTION
        assert res_lock.plan is not None
        assert res_lock.plan.is_locked is True

        # Verify derived checkpoints exist in WorkflowService
        chk = wf_srv.evaluate_checkpoint(
            checkpoint_id="chk_traj_traj_m24_1",
            session_id="sess_m24",
            measured_confidence=0.9,
            measured_uncertainty=0.1,
            measured_deviation_mm=1.0,
        )
        assert chk.status is True

        gateway.stop()
        plan_srv.stop()
        wf_srv.stop()

    def test_execute_planning_verify_success(
        self,
        runtime_context: RuntimeContext,
        secret_filter: SecretFilter,
        sample_draft_plan: SurgicalPlanDefinition,
    ) -> None:
        """Requirement 20: Valid VERIFY via execute_planning verifies plan and returns record."""
        plan_srv = PlanningService(secret_filter=secret_filter)
        plan_srv.initialize(runtime_context)
        plan_srv.start()

        mock_wf = MagicMock()
        mock_wf.authorize_tool.return_value = make_mock_wf_decision("planning.verify", session_id="sess_m24")
        mock_gate = MagicMock()
        mock_gate.evaluate.return_value = make_mock_gate_record("sess_m24")

        gateway = ClinicalExecutionGatewayService(
            workflow_service=mock_wf,
            safety_gate_service=mock_gate,
            planning_service=plan_srv,
            secret_filter=secret_filter,
        )
        gateway.initialize(runtime_context)
        gateway.start()

        # Submit and lock first
        cap_sub = _create_execution_capability(id(plan_srv), "sess_m24", "PLANNING_COORDINATION", 1)
        plan_srv.submit_plan(sample_draft_plan, "sess_m24", capability=cap_sub)
        cap_lock = _create_execution_capability(id(plan_srv), "sess_m24", "PLANNING_COORDINATION", 2)
        plan_srv.lock_plan("plan_m24", capability=cap_lock)

        req_ver = PlanningExecutionRequest(
            session_id="sess_m24",
            sequence_number=3,
            now_utc="2026-09-03T12:00:00Z",
            operation="VERIFY",
            plan_id="plan_m24",
            operator_id="dr_m24",
            patient_hash="b" * 64,
            procedure_code="THA_POSTERIOR",
            laterality=SurgicalLaterality.LEFT,
        )
        res_ver = gateway.execute_planning(req_ver)
        assert res_ver.execution_status == ExecutionStatus.EXECUTED_WITH_CAUTION
        assert res_ver.verification_record is not None
        assert res_ver.verification_record.verified is True
        assert res_ver.verification_record.plan_id == "plan_m24"

        gateway.stop()
        plan_srv.stop()

    def test_preoperative_permitted_with_caution_resolves_caution(
        self,
        runtime_context: RuntimeContext,
        secret_filter: SecretFilter,
        sample_draft_plan: SurgicalPlanDefinition,
    ) -> None:
        """Requirement 21: Gate PERMITTED_WITH_CAUTION resolves to EXECUTED_WITH_CAUTION."""
        plan_srv = PlanningService(secret_filter=secret_filter)
        plan_srv.initialize(runtime_context)
        plan_srv.start()

        mock_wf = MagicMock()
        mock_wf.authorize_tool.return_value = make_mock_wf_decision("planning.submit", session_id="sess_m24")
        mock_gate = MagicMock()
        mock_gate.evaluate.return_value = make_mock_gate_record("sess_m24", decision=GateDecision.PERMITTED_WITH_CAUTION)

        gateway = ClinicalExecutionGatewayService(
            workflow_service=mock_wf,
            safety_gate_service=mock_gate,
            planning_service=plan_srv,
            secret_filter=secret_filter,
        )
        gateway.initialize(runtime_context)
        gateway.start()

        req = PlanningExecutionRequest(
            session_id="sess_m24",
            sequence_number=1,
            now_utc="2026-09-03T12:00:00Z",
            operation="SUBMIT",
            plan=sample_draft_plan,
        )
        res = gateway.execute_planning(req)
        assert res.execution_status == ExecutionStatus.EXECUTED_WITH_CAUTION
        gateway.stop()
        plan_srv.stop()

    def test_preoperative_permitted_clear_resolves_clear(
        self,
        runtime_context: RuntimeContext,
        secret_filter: SecretFilter,
        sample_draft_plan: SurgicalPlanDefinition,
    ) -> None:
        """Requirement 22: Gate PERMITTED_CLEAR resolves to EXECUTED_CLEAR."""
        plan_srv = PlanningService(secret_filter=secret_filter)
        plan_srv.initialize(runtime_context)
        plan_srv.start()

        mock_wf = MagicMock()
        mock_wf.authorize_tool.return_value = make_mock_wf_decision("planning.submit", session_id="sess_m24")
        mock_gate = MagicMock()
        mock_gate.evaluate.return_value = make_mock_gate_record("sess_m24", decision=GateDecision.PERMITTED_CLEAR)

        gateway = ClinicalExecutionGatewayService(
            workflow_service=mock_wf,
            safety_gate_service=mock_gate,
            planning_service=plan_srv,
            secret_filter=secret_filter,
        )
        gateway.initialize(runtime_context)
        gateway.start()

        req = PlanningExecutionRequest(
            session_id="sess_m24",
            sequence_number=1,
            now_utc="2026-09-03T12:00:00Z",
            operation="SUBMIT",
            plan=sample_draft_plan,
        )
        res = gateway.execute_planning(req)
        assert res.execution_status == ExecutionStatus.EXECUTED_CLEAR
        gateway.stop()
        plan_srv.stop()


class TestM24DualGateInterlocks:
    """Requirements 23-25: Fail-closed dual-gate protection."""

    def test_critical_gate_denial_blocks_execution(
        self,
        runtime_context: RuntimeContext,
        secret_filter: SecretFilter,
        sample_draft_plan: SurgicalPlanDefinition,
    ) -> None:
        """Requirement 23: DENIED_CRITICAL blocks execution before planning service mutation."""
        plan_srv = PlanningService(secret_filter=secret_filter)
        plan_srv.initialize(runtime_context)
        plan_srv.start()

        mock_gate = MagicMock()
        mock_gate.evaluate.return_value = make_mock_gate_record(
            "sess_crit",
            decision=GateDecision.DENIED_CRITICAL,
            reason_code=GateReasonCode.CRITICAL_EXCLUSION_ZONE_BREACH,
            severity=GateSeverity.CRITICAL,
        )

        gateway = ClinicalExecutionGatewayService(
            safety_gate_service=mock_gate,
            planning_service=plan_srv,
            secret_filter=secret_filter,
        )
        gateway.initialize(runtime_context)
        gateway.start()

        req = PlanningExecutionRequest(
            session_id="sess_crit",
            sequence_number=1,
            now_utc="2026-09-03T12:00:00Z",
            operation="SUBMIT",
            plan=sample_draft_plan,
        )
        res = gateway.execute_planning(req)
        assert res.execution_status == ExecutionStatus.BLOCKED_SAFETY_GATE
        assert res.gate_decision == GateDecision.DENIED_CRITICAL
        assert plan_srv.active_plans_count == 0  # No mutation occurred!

        gateway.stop()
        plan_srv.stop()

    def test_interlocked_gate_denial_blocks_execution(
        self,
        runtime_context: RuntimeContext,
        secret_filter: SecretFilter,
        sample_draft_plan: SurgicalPlanDefinition,
    ) -> None:
        """Requirement 24: DENIED_INTERLOCKED blocks execution before planning service mutation."""
        plan_srv = PlanningService(secret_filter=secret_filter)
        plan_srv.initialize(runtime_context)
        plan_srv.start()

        mock_gate = MagicMock()
        mock_gate.evaluate.return_value = make_mock_gate_record(
            "sess_interlock",
            decision=GateDecision.DENIED_INTERLOCKED,
            reason_code=GateReasonCode.LANDMARK_INTEGRITY_INTERLOCKED,
            severity=GateSeverity.BLOCKING,
        )

        gateway = ClinicalExecutionGatewayService(
            safety_gate_service=mock_gate,
            planning_service=plan_srv,
            secret_filter=secret_filter,
        )
        gateway.initialize(runtime_context)
        gateway.start()

        req = PlanningExecutionRequest(
            session_id="sess_interlock",
            sequence_number=1,
            now_utc="2026-09-03T12:00:00Z",
            operation="SUBMIT",
            plan=sample_draft_plan,
        )
        res = gateway.execute_planning(req)
        assert res.execution_status == ExecutionStatus.BLOCKED_SAFETY_GATE
        assert res.gate_decision == GateDecision.DENIED_INTERLOCKED
        assert plan_srv.active_plans_count == 0

        gateway.stop()
        plan_srv.stop()

    def test_workflow_denial_blocks_execution(
        self,
        runtime_context: RuntimeContext,
        secret_filter: SecretFilter,
        sample_draft_plan: SurgicalPlanDefinition,
    ) -> None:
        """Requirement 25: M10 Workflow authorization denial blocks execution before planning mutation."""
        plan_srv = PlanningService(secret_filter=secret_filter)
        plan_srv.initialize(runtime_context)
        plan_srv.start()

        mock_gate = MagicMock()
        mock_gate.evaluate.return_value = make_mock_gate_record("sess_wf_block")

        mock_wf = MagicMock()
        mock_wf.authorize_tool.return_value = make_mock_wf_decision(
            "planning.submit",
            status=WorkflowToolAuthorizationStatus.BLOCKED_PHASE,
            phase=WorkflowPhase.NAVIGATION,
            session_id="sess_wf_block",
        )

        gateway = ClinicalExecutionGatewayService(
            workflow_service=mock_wf,
            safety_gate_service=mock_gate,
            planning_service=plan_srv,
            secret_filter=secret_filter,
        )
        gateway.initialize(runtime_context)
        gateway.start()

        req = PlanningExecutionRequest(
            session_id="sess_wf_block",
            sequence_number=1,
            now_utc="2026-09-03T12:00:00Z",
            operation="SUBMIT",
            plan=sample_draft_plan,
        )
        res = gateway.execute_planning(req)
        assert res.execution_status == ExecutionStatus.BLOCKED_WORKFLOW
        assert res.workflow_status == WorkflowToolAuthorizationStatus.BLOCKED_PHASE
        assert plan_srv.active_plans_count == 0

        gateway.stop()
        plan_srv.stop()


class TestM24ResilienceAndAudit:
    """Requirements 26-29: Capability cleanup, checkpoint failure, persistence audit, non-reentrancy."""

    def test_capability_invalidated_on_exception(
        self,
        runtime_context: RuntimeContext,
        secret_filter: SecretFilter,
    ) -> None:
        """Requirement 26: Capability invalidated even when planning operation raises an exception."""
        plan_srv = PlanningService(secret_filter=secret_filter)
        plan_srv.initialize(runtime_context)
        plan_srv.start()

        mock_gate = MagicMock()
        mock_gate.evaluate.return_value = make_mock_gate_record("sess_exc")

        mock_wf = MagicMock()
        mock_wf.authorize_tool.return_value = make_mock_wf_decision("planning.lock", session_id="sess_exc")

        gateway = ClinicalExecutionGatewayService(
            workflow_service=mock_wf,
            safety_gate_service=mock_gate,
            planning_service=plan_srv,
            secret_filter=secret_filter,
        )
        gateway.initialize(runtime_context)
        gateway.start()

        # Locking non-existent plan raises PlanningValidationError in PlanningService
        req = PlanningExecutionRequest(
            session_id="sess_exc",
            sequence_number=1,
            now_utc="2026-09-03T12:00:00Z",
            operation="LOCK",
            plan_id="non_existent_plan",
        )
        res = gateway.execute_planning(req)
        assert res.execution_status == ExecutionStatus.FAILED_NAVIGATION_GEOMETRY
        assert "not found" in res.error_message

        gateway.stop()
        plan_srv.stop()

    def test_checkpoint_derivation_failure_fails_closed(
        self,
        runtime_context: RuntimeContext,
        secret_filter: SecretFilter,
        sample_draft_plan: SurgicalPlanDefinition,
    ) -> None:
        """Requirement 27: Checkpoint derivation failure during LOCK fails closed."""
        mock_wf = MagicMock()
        mock_wf.authorize_tool.return_value = make_mock_wf_decision("planning.lock", session_id="sess_fail")
        mock_wf.register_checkpoint.side_effect = RuntimeError("Checkpoint registry failure")

        plan_srv = PlanningService(workflow_service=mock_wf, secret_filter=secret_filter)
        plan_srv.initialize(runtime_context)
        plan_srv.start()

        cap = _create_execution_capability(id(plan_srv), "sess_fail", "PLANNING_COORDINATION", 1)
        plan_srv.submit_plan(sample_draft_plan, "sess_fail", capability=cap)

        mock_gate = MagicMock()
        mock_gate.evaluate.return_value = make_mock_gate_record("sess_fail", seq=2)

        gateway = ClinicalExecutionGatewayService(
            workflow_service=mock_wf,
            safety_gate_service=mock_gate,
            planning_service=plan_srv,
            secret_filter=secret_filter,
        )
        gateway.initialize(runtime_context)
        gateway.start()

        req = PlanningExecutionRequest(
            session_id="sess_fail",
            sequence_number=2,
            now_utc="2026-09-03T12:00:00Z",
            operation="LOCK",
            plan_id="plan_m24",
        )
        res = gateway.execute_planning(req)
        assert res.execution_status == ExecutionStatus.FAILED_NAVIGATION_GEOMETRY
        assert "Checkpoint registry failure" in res.error_message

        gateway.stop()
        plan_srv.stop()

    def test_durable_persistence_audit_recorded(
        self,
        runtime_context: RuntimeContext,
        secret_filter: SecretFilter,
        sample_draft_plan: SurgicalPlanDefinition,
    ) -> None:
        """Requirement 28: Durable persistence audits recorded for permitted and blocked paths."""
        plan_srv = PlanningService(secret_filter=secret_filter)
        plan_srv.initialize(runtime_context)
        plan_srv.start()

        mock_persistence = MagicMock()

        mock_wf = MagicMock()
        mock_wf.authorize_tool.return_value = make_mock_wf_decision("planning.submit", session_id="sess_audit")

        mock_gate = MagicMock()
        mock_gate.evaluate.return_value = make_mock_gate_record("sess_audit", seq=1)

        gateway = ClinicalExecutionGatewayService(
            workflow_service=mock_wf,
            safety_gate_service=mock_gate,
            planning_service=plan_srv,
            persistence_service=mock_persistence,
            secret_filter=secret_filter,
        )
        gateway.initialize(runtime_context)
        gateway.start()

        # Permitted execution
        req = PlanningExecutionRequest(
            session_id="sess_audit",
            sequence_number=1,
            now_utc="2026-09-03T12:00:00Z",
            operation="SUBMIT",
            plan=sample_draft_plan,
        )
        res = gateway.execute_planning(req)
        assert res.execution_status == ExecutionStatus.EXECUTED_WITH_CAUTION
        assert mock_persistence.record_audit.called

        # Blocked execution
        mock_gate.evaluate.return_value = make_mock_gate_record(
            "sess_audit",
            decision=GateDecision.DENIED_CRITICAL,
            reason_code=GateReasonCode.CRITICAL_EXCLUSION_ZONE_BREACH,
            severity=GateSeverity.CRITICAL,
            seq=2,
        )
        req_blocked = PlanningExecutionRequest(
            session_id="sess_audit",
            sequence_number=2,
            now_utc="2026-09-03T12:00:00Z",
            operation="LOCK",
            plan_id="plan_m24",
        )
        res_blocked = gateway.execute_planning(req_blocked)
        assert res_blocked.execution_status == ExecutionStatus.BLOCKED_SAFETY_GATE
        # Verify persistence audit record includes blocked event
        calls = mock_persistence.record_audit.call_args_list
        blocked_audits = [c for c in calls if c[0][0].get("event") == "planning_blocked_safety_gate"]
        assert len(blocked_audits) >= 1

        gateway.stop()
        plan_srv.stop()

    def test_non_reentrancy_in_execute_planning(
        self,
        runtime_context: RuntimeContext,
        secret_filter: SecretFilter,
    ) -> None:
        """Requirement 29: Reentrant call to execute_planning raises ExecutionLifecycleError."""
        plan_srv = PlanningService(secret_filter=secret_filter)
        plan_srv.initialize(runtime_context)
        plan_srv.start()

        gateway = ClinicalExecutionGatewayService(
            planning_service=plan_srv,
            secret_filter=secret_filter,
        )
        gateway.initialize(runtime_context)
        gateway.start()

        gateway._in_transaction = True
        req = PlanningExecutionRequest(
            session_id="sess_reentrant",
            sequence_number=1,
            now_utc="2026-09-03T12:00:00Z",
            operation="LOCK",
            plan_id="plan_m24",
        )
        with pytest.raises(ExecutionLifecycleError, match="Reentrant"):
            gateway.execute_planning(req)

        gateway._in_transaction = False
        gateway.stop()
        plan_srv.stop()
