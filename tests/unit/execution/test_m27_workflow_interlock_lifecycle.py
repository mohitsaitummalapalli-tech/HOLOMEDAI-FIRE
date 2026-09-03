# -*- coding: utf-8 -*-
"""Hostile Unit & Integration Tests for M27 Workflow Safety Interlock Lifecycle & Session Eviction."""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import MagicMock
import pytest

from holomed.core.dispatcher import MessageDispatcher
from holomed.execution.models import (
    ExecutionStatus,
    SessionTeardownExecutionRequest,
)
from holomed.execution.service import ClinicalExecutionGatewayService
from holomed.navigation.service import NavigationService
from holomed.persistence.service import PersistenceService
from holomed.planning.service import PlanningService
from holomed.platform.service import PlatformService
from holomed.recovery.service import RecoveryService
from holomed.registration.service import RegistrationService
from holomed.safety_gate.models import (
    GateDecision,
    GateReasonCode,
    GateSeverity,
    GateStatusRecord,
    SafetyGateAction,
)
from holomed.safety_gate.service import SafetyGateService
from holomed.workflow.checkpoints import AnatomicalCheckpointValidator
from holomed.workflow.exceptions import (
    WorkflowCapacityError,
    WorkflowLifecycleError,
    WorkflowSafetyInterlockError,
)
from holomed.workflow.interlocks import SafetyInterlockEngine
from holomed.workflow.models import (
    MAX_REGISTERED_CHECKPOINTS,
    AnatomicalCheckpoint,
    ConfirmationResponse,
    InterlockSeverity,
    SafetyInterlock,
    WorkflowPhase,
    WorkflowResumptionRequest,
)
from holomed.workflow.service import WorkflowService


@pytest.fixture
def m27_env(runtime_context):
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
        "gateway": gateway,
        "persistence": persistence,
    }


def _create_dummy_interlock(
    interlock_id: str,
    session_id: str,
    severity: InterlockSeverity = InterlockSeverity.CRITICAL,
    status: bool = False,
) -> SafetyInterlock:
    return SafetyInterlock(
        interlock_id=interlock_id,
        severity=severity,
        condition_name="boundary_breach",
        status=status,
        reason="Exclusion boundary violated",
        source_service="proximity_service",
        epoch_id=1,
        session_id=session_id,
    )


def _create_dummy_checkpoint(checkpoint_id: str) -> AnatomicalCheckpoint:
    return AnatomicalCheckpoint(
        checkpoint_id=checkpoint_id,
        entity_id="carotid_artery",
        expected_relation="outside_exclusion_zone",
        max_tolerance_mm=1.5,
        min_confidence=0.85,
        max_uncertainty=0.15,
    )


def _advance_to_recovery_required(svc: WorkflowService, session_id: str) -> None:
    """Advance session workflow safely to RECOVERY_REQUIRED through legal transitions."""
    svc.transition_phase(session_id, WorkflowPhase.PRE_PROCEDURE_PLANNING, 1)
    svc.transition_phase(session_id, WorkflowPhase.REGISTRATION, 2)
    svc.transition_phase(session_id, WorkflowPhase.SAFETY_TIMEOUT, 3)

    req = svc.request_confirmation(session_id, WorkflowPhase.NAVIGATION, "Checklist verified", 4)
    resp = ConfirmationResponse(
        confirmation_id=req.confirmation_id,
        session_id=session_id,
        epoch_id=1,
        approved=True,
        operator_id="surgeon_01",
        timestamp_utc=datetime.now(timezone.utc).isoformat(),
        sequence_number=4,
    )
    svc.confirm(resp)
    svc.transition_phase(session_id, WorkflowPhase.NAVIGATION, 4)
    svc.transition_phase(session_id, WorkflowPhase.RECOVERY_REQUIRED, 5)


# =========================================================================
# 1. Critical Interlock Isolation (Session A Cannot Abort Session B)
# =========================================================================

def test_m27_session_a_critical_interlock_cannot_abort_session_b(m27_env):
    workflow: WorkflowService = m27_env["workflow"]
    platform: PlatformService = m27_env["platform"]

    session_a = "SESS-M27-A"
    session_b = "SESS-M27-B"

    platform.start_session(session_a)
    platform.start_session(session_b)

    workflow.start_workflow(session_a)
    workflow.start_workflow(session_b)

    # Session A trips a critical safety interlock
    crit_interlock_a = _create_dummy_interlock("crit-a-001", session_a, severity=InterlockSeverity.CRITICAL, status=False)
    workflow.interlock_engine.register_interlock(crit_interlock_a)

    assert workflow.interlock_engine.has_critical_interlock(session_a) is True
    assert workflow.interlock_engine.has_critical_interlock(session_b) is False

    # Session B executes a REAL workflow transition from PATIENT_CONTEXT to PRE_PROCEDURE_PLANNING
    snap_b = workflow.transition_phase(session_b, WorkflowPhase.PRE_PROCEDURE_PLANNING, sequence_number=1)
    assert snap_b.current_phase == WorkflowPhase.PRE_PROCEDURE_PLANNING
    assert snap_b.current_phase != WorkflowPhase.ABORTED

    # Session A executing a transition MUST be aborted by its own critical interlock
    with pytest.raises(WorkflowSafetyInterlockError, match="Critical safety interlock tripped; workflow aborted"):
        workflow.transition_phase(session_a, WorkflowPhase.PRE_PROCEDURE_PLANNING, sequence_number=1)

    assert workflow.get_workflow_state(session_a).current_phase == WorkflowPhase.ABORTED


# =========================================================================
# 2. Blocking Interlock Isolation (Session A Cannot Block Session B)
# =========================================================================

def test_m27_session_a_blocking_interlock_cannot_block_session_b(m27_env):
    workflow: WorkflowService = m27_env["workflow"]
    platform: PlatformService = m27_env["platform"]

    session_a = "SESS-BLOCK-A"
    session_b = "SESS-BLOCK-B"

    platform.start_session(session_a)
    platform.start_session(session_b)

    workflow.start_workflow(session_a)
    workflow.start_workflow(session_b)

    # Session A trips a blocking interlock
    block_interlock_a = _create_dummy_interlock("block-a-001", session_a, severity=InterlockSeverity.BLOCKING, status=False)
    workflow.interlock_engine.register_interlock(block_interlock_a)

    assert workflow.interlock_engine.has_blocking_interlock(session_a) is True
    assert workflow.interlock_engine.has_blocking_interlock(session_b) is False

    # Session B executes a real transition cleanly
    snap_b = workflow.transition_phase(session_b, WorkflowPhase.PRE_PROCEDURE_PLANNING, sequence_number=1)
    assert snap_b.current_phase == WorkflowPhase.PRE_PROCEDURE_PLANNING

    # Session A is blocked from transition
    with pytest.raises(WorkflowSafetyInterlockError, match="Active safety interlock blocks phase transition"):
        workflow.transition_phase(session_a, WorkflowPhase.PRE_PROCEDURE_PLANNING, sequence_number=1)


# =========================================================================
# 3. Target Session Interlock Eviction
# =========================================================================

def test_m27_interlock_eviction_removes_only_target_session(m27_env):
    workflow: WorkflowService = m27_env["workflow"]

    session_a = "SESS-EVICT-A"
    session_b = "SESS-EVICT-B"

    it_a = _create_dummy_interlock("it-a", session_a)
    it_b = _create_dummy_interlock("it-b", session_b)

    workflow.interlock_engine.register_interlock(it_a)
    workflow.interlock_engine.register_interlock(it_b)

    assert workflow.interlock_engine.interlock_count == 2
    assert workflow.interlock_engine.has_critical_interlock(session_a) is True
    assert workflow.interlock_engine.has_critical_interlock(session_b) is True

    # Evict Session A
    assert workflow.interlock_engine.evict_session(session_a) is True
    assert workflow.interlock_engine.has_critical_interlock(session_a) is False
    assert workflow.interlock_engine.has_critical_interlock(session_b) is True
    assert workflow.interlock_engine.interlock_count == 1


# =========================================================================
# 4. Checkpoint Isolation & Eviction
# =========================================================================

def test_m27_checkpoint_isolation_and_eviction(m27_env):
    workflow: WorkflowService = m27_env["workflow"]

    session_a = "SESS-CHK-A"
    session_b = "SESS-CHK-B"

    chk_a = _create_dummy_checkpoint("chk-a")
    chk_b = _create_dummy_checkpoint("chk-b")

    workflow.register_checkpoint(chk_a, session_id=session_a)
    workflow.register_checkpoint(chk_b, session_id=session_b)

    assert workflow.checkpoint_validator.checkpoint_count == 2

    # Evict Session A
    assert workflow.checkpoint_validator.evict_session(session_a) is True
    assert workflow.checkpoint_validator.checkpoint_count == 1
    assert "chk-a" not in workflow.checkpoint_validator._checkpoints
    assert "chk-b" in workflow.checkpoint_validator._checkpoints


# =========================================================================
# 5. Gateway Teardown Production Path (Workflow + Interlocks + Checkpoints)
# =========================================================================

def test_m27_gateway_teardown_cleanses_all_workflow_structures(m27_env):
    gateway: ClinicalExecutionGatewayService = m27_env["gateway"]
    workflow: WorkflowService = m27_env["workflow"]
    platform: PlatformService = m27_env["platform"]

    session_id = "SESS-TEARDOWN-M27"
    platform.start_session(session_id)
    workflow.start_workflow(session_id)

    # Register interlock and checkpoint
    it = _create_dummy_interlock("it-td", session_id)
    workflow.interlock_engine.register_interlock(it)
    chk = _create_dummy_checkpoint("chk-td")
    workflow.register_checkpoint(chk, session_id=session_id)

    assert session_id in workflow._workflows
    assert session_id in workflow._confirmations
    assert workflow.interlock_engine.has_critical_interlock(session_id) is True
    assert "chk-td" in workflow.checkpoint_validator._checkpoints

    # Execute M25/M27 Teardown via Execution Gateway
    req = SessionTeardownExecutionRequest(
        session_id=session_id,
        sequence_number=1,
        now_utc=datetime.now(timezone.utc).isoformat(),
    )
    res = gateway.execute_session_teardown(req)
    assert res.execution_status == ExecutionStatus.EXECUTED_CLEAR
    assert "workflow" in res.subsystems_purged

    # Verify ALL 4 structures are completely purged
    assert session_id not in workflow._workflows
    assert session_id not in workflow._confirmations
    assert workflow.interlock_engine.has_critical_interlock(session_id) is False
    assert "chk-td" not in workflow.checkpoint_validator._checkpoints


# =========================================================================
# 6. Session ID Reuse Is Completely Clean
# =========================================================================

def test_m27_session_id_reuse_has_zero_stale_interlocks(m27_env):
    gateway: ClinicalExecutionGatewayService = m27_env["gateway"]
    workflow: WorkflowService = m27_env["workflow"]
    platform: PlatformService = m27_env["platform"]

    session_id = "SESS-REUSE-CLEAN"
    platform.start_session(session_id)
    workflow.start_workflow(session_id)

    # Session 1 trips critical interlock
    it = _create_dummy_interlock("it-stale", session_id)
    workflow.interlock_engine.register_interlock(it)
    chk = _create_dummy_checkpoint("chk-stale")
    workflow.register_checkpoint(chk, session_id=session_id)

    # Teardown Session 1
    req = SessionTeardownExecutionRequest(
        session_id=session_id,
        sequence_number=1,
        now_utc=datetime.now(timezone.utc).isoformat(),
    )
    res = gateway.execute_session_teardown(req)
    assert res.execution_status == ExecutionStatus.EXECUTED_CLEAR

    # Start Session 2 with the same session_id
    platform.start_session(session_id)
    workflow.start_workflow(session_id)

    # Prove no stale interlocks or checkpoints survive
    assert workflow.interlock_engine.has_critical_interlock(session_id) is False
    assert "chk-stale" not in workflow.checkpoint_validator._checkpoints

    # Real workflow transition MUST succeed without being aborted
    snap = workflow.transition_phase(session_id, WorkflowPhase.PRE_PROCEDURE_PLANNING, sequence_number=1)
    assert snap.current_phase == WorkflowPhase.PRE_PROCEDURE_PLANNING
    assert snap.current_phase != WorkflowPhase.ABORTED


# =========================================================================
# 7. Checkpoint Capacity Reclaimed
# =========================================================================

def test_m27_checkpoint_capacity_reclaimed_after_teardown(m27_env):
    gateway: ClinicalExecutionGatewayService = m27_env["gateway"]
    workflow: WorkflowService = m27_env["workflow"]
    platform: PlatformService = m27_env["platform"]

    session_id = "SESS-CAPACITY"
    platform.start_session(session_id)
    workflow.start_workflow(session_id)

    # Register checkpoints up to MAX_REGISTERED_CHECKPOINTS
    for i in range(MAX_REGISTERED_CHECKPOINTS):
        chk = _create_dummy_checkpoint(f"chk-cap-{i:03d}")
        workflow.register_checkpoint(chk, session_id=session_id)

    assert workflow.checkpoint_validator.checkpoint_count == MAX_REGISTERED_CHECKPOINTS

    # Next checkpoint must fail with capacity error
    chk_extra = _create_dummy_checkpoint("chk-extra")
    with pytest.raises(WorkflowCapacityError, match="Registered checkpoint limit"):
        workflow.register_checkpoint(chk_extra, session_id=session_id)

    # Teardown session
    req = SessionTeardownExecutionRequest(
        session_id=session_id,
        sequence_number=1,
        now_utc=datetime.now(timezone.utc).isoformat(),
    )
    res = gateway.execute_session_teardown(req)
    assert res.execution_status == ExecutionStatus.EXECUTED_CLEAR
    assert workflow.checkpoint_validator.checkpoint_count == 0

    # Start new session and prove capacity is completely reclaimed
    new_session = "SESS-CAPACITY-NEW"
    platform.start_session(new_session)
    workflow.start_workflow(new_session)
    workflow.register_checkpoint(chk_extra, session_id=new_session)
    assert workflow.checkpoint_validator.checkpoint_count == 1


# =========================================================================
# 8. Recovery Resumption Cross-Session Isolation
# =========================================================================

def test_m27_recovery_resumption_unaffected_by_foreign_session_interlock(m27_env):
    workflow: WorkflowService = m27_env["workflow"]
    platform: PlatformService = m27_env["platform"]
    safety_gate: SafetyGateService = m27_env["safety_gate"]

    session_a = "SESS-REC-A"
    session_b = "SESS-REC-B"

    platform.start_session(session_a)
    platform.start_session(session_b)
    workflow.start_workflow(session_a)
    workflow.start_workflow(session_b)

    # Move Session A to RECOVERY_REQUIRED through legal transitions
    _advance_to_recovery_required(workflow, session_a)

    # Register a recovery interlock for Session A
    it_a = SafetyInterlock(
        interlock_id="it-rec-a",
        severity=InterlockSeverity.BLOCKING,
        condition_name="drift",
        status=False,
        reason="Drift on A",
        source_service="drift",
        epoch_id=1,
        session_id=session_a,
    )
    workflow.interlock_engine.register_interlock(it_a)

    # Foreign Session B trips an unrelated critical interlock!
    it_b = _create_dummy_interlock("it-b-crit", session_b, severity=InterlockSeverity.CRITICAL, status=False)
    workflow.interlock_engine.register_interlock(it_b)

    # Session A requests resumption from recovery
    mock_gate = MagicMock()
    mock_gate.evaluate.return_value = GateStatusRecord(
        session_id=session_a,
        decision=GateDecision.PERMITTED_CLEAR,
        severity=GateSeverity.NONE,
        reason_code=GateReasonCode.NONE,
        action=SafetyGateAction.WORKFLOW_RESUMPTION,
        sequence_number=10,
        subsystem_snapshots=(),
        evaluated_at_utc=datetime.now(timezone.utc).isoformat(),
    )

    req = WorkflowResumptionRequest(
        session_id=session_a,
        sequence_number=10,
        now_utc=datetime.now(timezone.utc).isoformat(),
        recovery_revision=1,
        cleared_interlock_ids=("it-rec-a",),
    )

    # Session A resumption MUST succeed and not be blocked by Session B's critical interlock!
    snap = workflow.resume_from_recovery(req, mock_gate)
    assert snap.current_phase == WorkflowPhase.NAVIGATION
    assert workflow.interlock_engine.has_critical_interlock(session_b) is True
    assert workflow.interlock_engine.has_critical_interlock(session_a) is False


# =========================================================================
# 9. Reentrancy Guard Fails Closed
# =========================================================================

def test_m27_workflow_evict_reentrancy_fails_closed(m27_env):
    workflow: WorkflowService = m27_env["workflow"]
    session_id = "SESS-REENTRANT"

    workflow._in_transaction = True
    with pytest.raises(WorkflowLifecycleError, match="Reentrant call to evict_session rejected"):
        workflow.evict_session(session_id)
    workflow._in_transaction = False


# =========================================================================
# 10. Partial Teardown Failure Best-Effort Aggregation
# =========================================================================

def test_m27_partial_workflow_failure_aggregates_and_continues(m27_env):
    gateway: ClinicalExecutionGatewayService = m27_env["gateway"]
    workflow: WorkflowService = m27_env["workflow"]
    platform: PlatformService = m27_env["platform"]

    session_id = "SESS-FAIL-AGG"
    platform.start_session(session_id)
    workflow.start_workflow(session_id)

    # Force workflow eviction to fail
    workflow.evict_session = MagicMock(side_effect=RuntimeError("Workflow disk sync error"))

    req = SessionTeardownExecutionRequest(
        session_id=session_id,
        sequence_number=1,
        now_utc=datetime.now(timezone.utc).isoformat(),
    )
    res = gateway.execute_session_teardown(req)

    assert res.execution_status == ExecutionStatus.FAILED_NAVIGATION_GEOMETRY
    assert any("workflow: Workflow disk sync error" in f for f in res.failures)
    assert "platform" in res.subsystems_purged


# =========================================================================
# 11. Tool Authorization Scoping & Isolation
# =========================================================================

def test_m27_tool_authorization_isolated_between_sessions(m27_env):
    workflow: WorkflowService = m27_env["workflow"]
    platform: PlatformService = m27_env["platform"]
    from holomed.workflow.models import ToolSafetyClassification, WorkflowToolAuthorizationStatus

    session_a = "SESS-TOOL-A"
    session_b = "SESS-TOOL-B"

    platform.start_session(session_a)
    platform.start_session(session_b)
    workflow.start_workflow(session_a)
    workflow.start_workflow(session_b)

    # Transition both to PRE_PROCEDURE_PLANNING
    workflow.transition_phase(session_a, WorkflowPhase.PRE_PROCEDURE_PLANNING, 1)
    workflow.transition_phase(session_b, WorkflowPhase.PRE_PROCEDURE_PLANNING, 1)

    # Session A trips a blocking interlock
    it_a = _create_dummy_interlock("it-tool-a", session_a, severity=InterlockSeverity.BLOCKING, status=False)
    workflow.interlock_engine.register_interlock(it_a)

    # Evaluate tool authorization for Session B (should be PERMITTED)
    dec_b = workflow.authorize_tool(
        session_id=session_b,
        tool_id="planning_tool_01",
        safety_classification=ToolSafetyClassification.READ_ONLY_INFORMATIVE,
    )
    assert dec_b.status == WorkflowToolAuthorizationStatus.PERMITTED

    # Evaluate tool authorization for Session A (should be BLOCKED_INTERLOCK)
    dec_a = workflow.authorize_tool(
        session_id=session_a,
        tool_id="planning_tool_01",
        safety_classification=ToolSafetyClassification.READ_ONLY_INFORMATIVE,
    )
    assert dec_a.status == WorkflowToolAuthorizationStatus.BLOCKED_INTERLOCK


# =========================================================================
# 12. Cross-Session Capability Misuse Fails Closed
# =========================================================================

def test_m27_cross_session_recovery_capability_fails_closed(m27_env):
    workflow: WorkflowService = m27_env["workflow"]
    from holomed.workflow._transaction import _create_recovery_capability
    from holomed.workflow.exceptions import WorkflowAuthorizationError

    session_a = "SESS-CAP-A"
    session_b = "SESS-CAP-B"

    it_b = SafetyInterlock(
        interlock_id="it-b-cap",
        severity=InterlockSeverity.BLOCKING,
        condition_name="drift",
        status=False,
        reason="Drift",
        source_service="drift",
        epoch_id=1,
        session_id=session_b,
    )
    workflow.interlock_engine.register_interlock(it_b)

    # Mint capability for Session A
    cap_a = _create_recovery_capability(id(workflow), session_a, "wf-a", 1, 1)

    # Attempt to clear Session B interlock using Session A capability
    with pytest.raises(WorkflowSafetyInterlockError, match="belongs to session 'SESS-CAP-B', not active session 'SESS-CAP-A'"):
        workflow.interlock_engine.stage_recovery_clearance(cap_a, ("it-b-cap",))


# =========================================================================
# 13. Stale Capability Replay Rejection
# =========================================================================

def test_m27_stale_capability_replay_rejected(m27_env):
    workflow: WorkflowService = m27_env["workflow"]
    from holomed.workflow._transaction import _create_recovery_capability
    from holomed.workflow.exceptions import WorkflowAuthorizationError

    session_a = "SESS-STALE-CAP"
    cap = _create_recovery_capability(id(workflow), session_a, "wf-a", 1, 1)
    cap.invalidate()
    assert cap.is_active is False

    with pytest.raises(WorkflowAuthorizationError, match="Valid active _RecoveryTransactionCapability required"):
        workflow.interlock_engine.stage_recovery_clearance(cap, ("some-it",))


