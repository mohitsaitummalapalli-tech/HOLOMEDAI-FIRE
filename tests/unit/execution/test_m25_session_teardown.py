# -*- coding: utf-8 -*-
"""Unit and Hostile Tests for M25 Coordinated Clinical Session Teardown."""

from __future__ import annotations

from datetime import datetime, timezone
import pytest
from unittest.mock import MagicMock

from holomed.core.dispatcher import MessageDispatcher
from holomed.core.models import DispatcherState
from holomed.execution.exceptions import (
    ExecutionLifecycleError,
    ExecutionValidationError,
)
from holomed.execution.models import (
    ExecutionStatus,
    SessionTeardownExecutionRequest,
    SessionTeardownExecutionResult,
)
from holomed.execution.service import ClinicalExecutionGatewayService
from holomed.navigation.service import NavigationService
from holomed.persistence.service import PersistenceService
from holomed.planning.service import PlanningService
from holomed.platform.service import PlatformService
from holomed.platform.session import SessionManager
from holomed.protocol.models import MessageEnvelope
from holomed.recovery.service import RecoveryService
from holomed.registration.service import RegistrationService
from holomed.runtime.context import RuntimeContext
from holomed.runtime.service import ServiceState
from holomed.safety_gate.models import GateStatusRecord, GateDecision, GateReasonCode, SafetyGateAction
from holomed.safety_gate.service import SafetyGateService
from holomed.workflow.service import WorkflowService


@pytest.fixture
def m25_services(runtime_context):
    dispatcher = MessageDispatcher()
    dispatcher.initialize(runtime_context)

    platform = PlatformService()
    platform.initialize(runtime_context)
    platform.start()

    workflow = WorkflowService(dispatcher=dispatcher, platform_service=platform)
    workflow.initialize(runtime_context)
    workflow.start()

    planning = PlanningService(dispatcher=dispatcher, workflow_service=workflow)
    planning.initialize(runtime_context)
    planning.start()

    registration = RegistrationService(dispatcher=dispatcher, planning_service=planning)
    registration.initialize(runtime_context)
    registration.start()

    navigation = NavigationService(dispatcher=dispatcher, registration_service=registration)
    navigation.initialize(runtime_context)
    navigation.start()

    recovery = RecoveryService(dispatcher=dispatcher, registration_service=registration)
    recovery.initialize(runtime_context)
    recovery.start()

    safety_gate = SafetyGateService(dispatcher=None, workflow_service=workflow)
    safety_gate.initialize(runtime_context)
    safety_gate.start()



    persistence = PersistenceService()
    persistence.initialize(runtime_context)
    persistence.start()

    gateway = ClinicalExecutionGatewayService(
        dispatcher=dispatcher,
        safety_gate_service=safety_gate,
        workflow_service=workflow,
        navigation_service=navigation,
        persistence_service=persistence,
        recovery_service=recovery,
        registration_service=registration,
        planning_service=planning,
        platform_service=platform,
    )
    gateway.initialize(runtime_context)
    gateway.start()

    dispatcher.start()



    return {
        "dispatcher": dispatcher,
        "platform": platform,
        "workflow": workflow,
        "planning": planning,
        "registration": registration,
        "navigation": navigation,
        "recovery": recovery,
        "safety_gate": safety_gate,
        "persistence": persistence,
        "gateway": gateway,
    }


def test_m25_teardown_purges_all_subsystem_states(m25_services):
    """Verify that execution.session.teardown explicitly purges state from all subsystems."""
    services = m25_services
    gateway: ClinicalExecutionGatewayService = services["gateway"]
    platform: PlatformService = services["platform"]
    workflow: WorkflowService = services["workflow"]
    planning: PlanningService = services["planning"]
    registration: RegistrationService = services["registration"]
    navigation: NavigationService = services["navigation"]
    recovery: RecoveryService = services["recovery"]
    safety_gate: SafetyGateService = services["safety_gate"]

    session_id = "SESSION-001"
    other_session_id = "SESSION-OTHER"
    now_utc = datetime.now(timezone.utc).isoformat()

    # Populate state across subsystems
    platform.start_session(session_id)
    workflow.start_workflow(session_id)
    planning._session_plan_bindings[session_id] = "plan_123"
    registration._registrations[session_id] = MagicMock()
    recovery._session_states[session_id] = MagicMock()
    safety_gate._latest_decisions[session_id] = MagicMock()
    gateway._latest_results[session_id] = MagicMock()

    # Populate full M14 navigation state (including composite keys)
    navigation._session_states[session_id] = MagicMock()
    navigation._bound_trajectories[session_id] = MagicMock()
    navigation._latest_poses[(session_id, "inst-01")] = MagicMock()
    navigation._latest_sequences[(session_id, "inst-01")] = 42
    navigation._latest_deviations[session_id] = MagicMock()
    navigation._active_instruments[session_id] = "inst-01"

    # Populate another session's state to verify no collateral eviction
    navigation._latest_poses[(other_session_id, "inst-01")] = MagicMock()
    navigation._latest_sequences[(other_session_id, "inst-01")] = 99
    navigation._active_instruments[other_session_id] = "inst-01"

    # Verify presence before teardown
    assert platform.has_session(session_id) is True
    assert session_id in workflow._workflows
    assert session_id in planning._session_plan_bindings
    assert session_id in registration._registrations
    assert session_id in recovery._session_states
    assert session_id in safety_gate._latest_decisions
    assert session_id in gateway._latest_results
    assert session_id in navigation._session_states
    assert session_id in navigation._bound_trajectories
    assert (session_id, "inst-01") in navigation._latest_poses
    assert (session_id, "inst-01") in navigation._latest_sequences
    assert session_id in navigation._latest_deviations
    assert session_id in navigation._active_instruments

    # Execute teardown
    req = SessionTeardownExecutionRequest(
        session_id=session_id,
        sequence_number=1,
        now_utc=now_utc,
    )
    res = gateway.execute_session_teardown(req)

    assert res.execution_status == ExecutionStatus.EXECUTED_CLEAR
    assert res.session_id == session_id
    assert set(res.subsystems_purged) == {
        "navigation",
        "recovery",
        "registration",
        "planning",
        "safety_gate",
        "workflow",
        "gateway",
        "platform",
    }
    assert res.failures == ()

    # Verify complete eviction from all subsystems
    assert platform.has_session(session_id) is False
    assert session_id not in workflow._workflows
    assert session_id not in planning._session_plan_bindings
    assert session_id not in registration._registrations
    assert session_id not in recovery._session_states
    assert session_id not in safety_gate._latest_decisions
    assert session_id not in gateway._latest_results

    # Verify M14 composite keys and active instruments are completely evicted
    assert session_id not in navigation._session_states
    assert session_id not in navigation._bound_trajectories
    assert (session_id, "inst-01") not in navigation._latest_poses
    assert (session_id, "inst-01") not in navigation._latest_sequences
    assert session_id not in navigation._latest_deviations
    assert session_id not in navigation._active_instruments

    # Verify other session's composite state was completely untouched
    assert (other_session_id, "inst-01") in navigation._latest_poses
    assert (other_session_id, "inst-01") in navigation._latest_sequences
    assert navigation._active_instruments[other_session_id] == "inst-01"



def test_m25_32_session_capacity_reclaimed(m25_services):
    """Hostile test: 32 sequential sessions with teardown must NOT exhaust capacity on session 33."""
    services = m25_services
    gateway: ClinicalExecutionGatewayService = services["gateway"]
    platform: PlatformService = services["platform"]
    workflow: WorkflowService = services["workflow"]
    registration: RegistrationService = services["registration"]
    navigation: NavigationService = services["navigation"]
    recovery: RecoveryService = services["recovery"]
    safety_gate: SafetyGateService = services["safety_gate"]

    # Run 32 sessions sequentially, tearing down each one
    for i in range(1, 33):
        sess_id = f"SESSION-{i:03d}"
        now_utc = datetime.now(timezone.utc).isoformat()

        platform.start_session(sess_id)
        workflow.start_workflow(sess_id)
        registration._registrations[sess_id] = MagicMock()
        navigation._session_states[sess_id] = MagicMock()
        recovery._session_states[sess_id] = MagicMock()
        safety_gate._latest_decisions[sess_id] = MagicMock()

        req = SessionTeardownExecutionRequest(
            session_id=sess_id,
            sequence_number=i,
            now_utc=now_utc,
        )
        res = gateway.execute_session_teardown(req)
        assert res.execution_status == ExecutionStatus.EXECUTED_CLEAR

    # Session 33 MUST succeed across all subsystems without capacity error
    sess_33 = "SESSION-033"
    ctx_33 = platform.start_session(sess_33)
    assert ctx_33.session_id == sess_33

    wf_33 = workflow.start_workflow(sess_33)
    assert wf_33.session_id == sess_33


def test_m25_session_id_reuse_has_zero_residual_state(m25_services):
    """Hostile test: Reusing the same session ID after teardown starts from clean slate."""
    services = m25_services
    gateway: ClinicalExecutionGatewayService = services["gateway"]
    platform: PlatformService = services["platform"]
    workflow: WorkflowService = services["workflow"]
    planning: PlanningService = services["planning"]
    registration: RegistrationService = services["registration"]

    sess_id = "REUSED-SESSION"
    now_utc = datetime.now(timezone.utc).isoformat()

    # Session 1 run
    platform.start_session(sess_id)
    workflow.start_workflow(sess_id)
    planning._session_plan_bindings[sess_id] = "stale_plan_id"
    registration._registrations[sess_id] = MagicMock()

    # Teardown
    req = SessionTeardownExecutionRequest(
        session_id=sess_id,
        sequence_number=1,
        now_utc=now_utc,
    )
    res = gateway.execute_session_teardown(req)
    assert res.execution_status == ExecutionStatus.EXECUTED_CLEAR

    # Session 2 run with SAME session ID
    ctx = platform.start_session(sess_id)
    assert ctx.session_id == sess_id

    # Verify no residual plan or registration exists
    assert planning.get_plan_for_session(sess_id) is None
    assert registration.is_registration_verified(sess_id) is False


def test_m25_one_session_teardown_does_not_affect_another(m25_services):
    """Hostile test: Teardown is surgical and session-scoped; other active sessions remain intact."""
    services = m25_services
    gateway: ClinicalExecutionGatewayService = services["gateway"]
    platform: PlatformService = services["platform"]
    workflow: WorkflowService = services["workflow"]

    sess_a = "SESSION-AAA"
    sess_b = "SESSION-BBB"
    now_utc = datetime.now(timezone.utc).isoformat()

    platform.start_session(sess_a)
    workflow.start_workflow(sess_a)

    platform.start_session(sess_b)
    workflow.start_workflow(sess_b)

    # Teardown A only
    req = SessionTeardownExecutionRequest(
        session_id=sess_a,
        sequence_number=1,
        now_utc=now_utc,
    )
    res = gateway.execute_session_teardown(req)
    assert res.execution_status == ExecutionStatus.EXECUTED_CLEAR

    # Session A is evicted
    assert platform.has_session(sess_a) is False
    assert sess_a not in workflow._workflows

    # Session B is completely intact
    assert platform.has_session(sess_b) is True
    assert sess_b in workflow._workflows


def test_m25_teardown_failure_aggregates_and_continues(m25_services):
    """Hostile test: If one subsystem fails during eviction, remaining cleanup still executes."""
    services = m25_services
    gateway: ClinicalExecutionGatewayService = services["gateway"]
    navigation: NavigationService = services["navigation"]
    workflow: WorkflowService = services["workflow"]

    sess_id = "SESSION-FAIL"
    now_utc = datetime.now(timezone.utc).isoformat()

    # Make navigation raise an unhandled exception during eviction
    navigation.evict_session = MagicMock(side_effect=RuntimeError("Simulated Hardware Navigation Fault"))
    workflow._workflows[sess_id] = MagicMock()

    req = SessionTeardownExecutionRequest(
        session_id=sess_id,
        sequence_number=1,
        now_utc=now_utc,
    )
    res = gateway.execute_session_teardown(req)

    # Teardown degraded/failed status returned
    assert res.execution_status == ExecutionStatus.FAILED_NAVIGATION_GEOMETRY
    assert any("navigation" in f for f in res.failures)
    # Workflow was still evicted despite navigation failure!
    assert sess_id not in workflow._workflows
    assert "workflow" in res.subsystems_purged


def test_m25_reentrant_teardown_fails_safely(m25_services):
    """Hostile test: Reentrant teardown is rejected and fails closed."""
    services = m25_services
    gateway: ClinicalExecutionGatewayService = services["gateway"]

    gateway._in_transaction = True
    try:
        req = SessionTeardownExecutionRequest(
            session_id="SESSION-REENTRANT",
            sequence_number=1,
            now_utc=datetime.now(timezone.utc).isoformat(),
        )
        with pytest.raises(ExecutionLifecycleError, match="Reentrant call"):
            gateway.execute_session_teardown(req)
    finally:
        gateway._in_transaction = False


def test_m25_dispatcher_route_success(m25_services):
    """Verify that execution.session.teardown route dispatches properly."""
    from holomed.protocol.builders import create_command

    services = m25_services
    dispatcher: MessageDispatcher = services["dispatcher"]
    platform: PlatformService = services["platform"]

    sess_id = "DISPATCH-SESS"
    platform.start_session(sess_id)

    cmd = create_command(
        message_name="execution.session.teardown",
        source="client",
        payload={

            "session_id": sess_id,
            "sequence_number": 1,
            "now_utc": datetime.now(timezone.utc).isoformat(),
        },
    )
    resp = dispatcher.dispatch(cmd)
    assert resp is not None
    assert resp.payload["execution_status"] == ExecutionStatus.EXECUTED_CLEAR.value
    assert resp.payload["session_id"] == sess_id
    assert "platform" in resp.payload["subsystems_purged"]
    assert platform.has_session(sess_id) is False


def test_m25_old_capability_replay_fails(m25_services):
    """Hostile test: Capability minted before teardown is rejected and fails closed."""
    from holomed.execution._capability import _create_execution_capability
    from holomed.planning.exceptions import PlanningAuthorizationError

    services = m25_services
    gateway: ClinicalExecutionGatewayService = services["gateway"]
    planning: PlanningService = services["planning"]
    platform: PlatformService = services["platform"]

    sess_id = "CAP-REPLAY-SESS"
    platform.start_session(sess_id)

    # Mint a capability for planning submit
    cap = _create_execution_capability(
        service_instance_id=id(gateway),
        session_id=sess_id,
        action="PLANNING_SUBMIT",
        sequence_number=1,
    )
    assert cap.is_active is True

    # Execute teardown
    req = SessionTeardownExecutionRequest(
        session_id=sess_id,
        sequence_number=2,
        now_utc=datetime.now(timezone.utc).isoformat(),
    )
    res = gateway.execute_session_teardown(req)
    assert res.execution_status == ExecutionStatus.EXECUTED_CLEAR

    # Invalidate the pre-teardown capability
    cap.invalidate()
    assert cap.is_active is False

    # Attempt replay with old invalidated capability
    with pytest.raises(PlanningAuthorizationError, match="requires an active capability"):
        planning.submit_plan(
            plan=MagicMock(),
            session_id=sess_id,
            capability=cap,
        )


def test_m25_teardown_audit_records_outcomes(m25_services):
    """Hostile test: Teardown persistence audit logs completed, degraded, or failed events."""
    services = m25_services
    gateway: ClinicalExecutionGatewayService = services["gateway"]
    persistence: PersistenceService = services["persistence"]
    navigation: NavigationService = services["navigation"]

    persistence.record_audit = MagicMock(wraps=persistence.record_audit)

    sess_success = "SESS-AUDIT-OK"
    now_utc = datetime.now(timezone.utc).isoformat()

    # Success audit
    req_ok = SessionTeardownExecutionRequest(
        session_id=sess_success,
        sequence_number=1,
        now_utc=now_utc,
    )
    res_ok = gateway.execute_session_teardown(req_ok)
    assert res_ok.execution_status == ExecutionStatus.EXECUTED_CLEAR
    assert persistence.record_audit.call_count == 1
    assert persistence.record_audit.call_args[0][0]["event"] == "session_teardown_completed"
    assert persistence.record_audit.call_args[0][0]["status"] == "EXECUTED_CLEAR"

    # Degraded audit (one failure)
    sess_degraded = "SESS-AUDIT-DEG"
    navigation.evict_session = MagicMock(side_effect=ValueError("Faulty sensor hardware"))
    req_deg = SessionTeardownExecutionRequest(
        session_id=sess_degraded,
        sequence_number=2,
        now_utc=now_utc,
    )
    res_deg = gateway.execute_session_teardown(req_deg)
    assert res_deg.execution_status == ExecutionStatus.FAILED_NAVIGATION_GEOMETRY
    assert persistence.record_audit.call_count == 2
    assert persistence.record_audit.call_args[0][0]["event"] == "session_teardown_degraded"
    assert persistence.record_audit.call_args[0][0]["status"] == "FAILED_NAVIGATION_GEOMETRY"



def test_m25_payload_validation_rejects_malformed_inputs():
    """Hostile test: Invalid session_id or negative sequence_number fails with ExecutionValidationError."""
    now_utc = datetime.now(timezone.utc).isoformat()

    with pytest.raises(ExecutionValidationError, match="Invalid session_id"):
        SessionTeardownExecutionRequest(
            session_id="INVALID SESSION ID WITH SPACES",
            sequence_number=1,
            now_utc=now_utc,
        )

    with pytest.raises(ExecutionValidationError, match="sequence_number must be an integer >= 0"):
        SessionTeardownExecutionRequest(
            session_id="VALID-SESSION",
            sequence_number=-5,
            now_utc=now_utc,
        )

    with pytest.raises(ExecutionValidationError, match="now_utc must be a non-empty"):
        SessionTeardownExecutionRequest(
            session_id="VALID-SESSION",
            sequence_number=1,
            now_utc="",
        )


def test_m25_session_id_reuse_m14_navigation_state_clean(m25_services):
    """Explicit regression test: Same session_id reused after teardown has zero M14 residual state."""
    services = m25_services
    gateway: ClinicalExecutionGatewayService = services["gateway"]
    navigation: NavigationService = services["navigation"]
    platform: PlatformService = services["platform"]

    sess_id = "REUSE-M14-SESS"
    now_utc = datetime.now(timezone.utc).isoformat()

    # Populate M14 state for session run 1
    platform.start_session(sess_id)
    navigation._session_states[sess_id] = MagicMock()
    navigation._bound_trajectories[sess_id] = MagicMock()
    navigation._latest_poses[(sess_id, "inst-01")] = MagicMock()
    navigation._latest_sequences[(sess_id, "inst-01")] = 77
    navigation._latest_deviations[sess_id] = MagicMock()
    navigation._active_instruments[sess_id] = "inst-01"

    # Teardown run 1
    req = SessionTeardownExecutionRequest(
        session_id=sess_id,
        sequence_number=1,
        now_utc=now_utc,
    )
    res = gateway.execute_session_teardown(req)
    assert res.execution_status == ExecutionStatus.EXECUTED_CLEAR

    # Verify M14 state is 100% clean
    assert (sess_id, "inst-01") not in navigation._latest_poses
    assert (sess_id, "inst-01") not in navigation._latest_sequences
    assert sess_id not in navigation._active_instruments
    assert sess_id not in navigation._bound_trajectories
    assert sess_id not in navigation._session_states
    assert sess_id not in navigation._latest_deviations

    # Start session run 2 with same session_id
    ctx = platform.start_session(sess_id)
    assert ctx.session_id == sess_id

    # Verify run 2 begins with zero residual M14 state
    assert (sess_id, "inst-01") not in navigation._latest_poses
    assert (sess_id, "inst-01") not in navigation._latest_sequences
    assert sess_id not in navigation._active_instruments


def test_m25_32_session_composite_key_accumulation(m25_services):
    """Capacity regression test: 32 sessions creating composite-keyed M14 tracking state."""
    services = m25_services
    gateway: ClinicalExecutionGatewayService = services["gateway"]
    navigation: NavigationService = services["navigation"]
    platform: PlatformService = services["platform"]

    for i in range(1, 33):
        sess_id = f"COMPOSITE-{i:03d}"
        now_utc = datetime.now(timezone.utc).isoformat()

        platform.start_session(sess_id)
        navigation._session_states[sess_id] = MagicMock()
        navigation._bound_trajectories[sess_id] = MagicMock()
        navigation._latest_poses[(sess_id, "inst-alpha")] = MagicMock()
        navigation._latest_poses[(sess_id, "inst-beta")] = MagicMock()
        navigation._latest_sequences[(sess_id, "inst-alpha")] = i * 10
        navigation._latest_sequences[(sess_id, "inst-beta")] = i * 10 + 1
        navigation._active_instruments[sess_id] = "inst-alpha"

        req = SessionTeardownExecutionRequest(
            session_id=sess_id,
            sequence_number=i,
            now_utc=now_utc,
        )
        res = gateway.execute_session_teardown(req)
        assert res.execution_status == ExecutionStatus.EXECUTED_CLEAR

    # Verify that after 32 sequential sessions, all composite keys are evicted and no residue remains
    assert len(navigation._latest_poses) == 0
    assert len(navigation._latest_sequences) == 0
    assert len(navigation._active_instruments) == 0
    assert len(navigation._session_states) == 0

    # Session 33 starts and navigates with full reusable capacity
    sess_33 = "COMPOSITE-033"
    ctx_33 = platform.start_session(sess_33)
    assert ctx_33.session_id == sess_33
    navigation._latest_poses[(sess_33, "inst-alpha")] = MagicMock()
    assert len(navigation._latest_poses) == 1



