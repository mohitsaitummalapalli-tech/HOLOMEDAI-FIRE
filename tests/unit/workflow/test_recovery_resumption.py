# -*- coding: utf-8 -*-
"""M20 Unit Tests for Clinical Workflow Recovery Re-entry & Safety Gate Resumption.

Verifies the locked invariants:
1. ONLY public path for RECOVERY_REQUIRED -> NAVIGATION is WorkflowService.resume_from_recovery()
2. Generic transition_phase() remains blocked during RECOVERY_REQUIRED
3. Recovery interlock clearing is explicitly scoped (no None, no clear all, session and source checked)
4. M18 WORKFLOW_RESUMPTION evaluation is inline and fresh (denial leaves state untouched)
5. M10-local transactional consistency: rollback on any failure
6. Sequence monotonicity and anti-replay protection
7. Audit durability and event emissions
8. End-to-end recovery re-entry lifecycle with M18 and M19 integration
"""

from __future__ import annotations

from unittest.mock import MagicMock
import pytest

from holomed.configuration.models import AppConfig
from holomed.core.dispatcher import MessageDispatcher
from holomed.runtime.context import RuntimeContext
from holomed.runtime.logging import SecretFilter, StructuredLogger
from holomed.runtime.service import ServiceState
from holomed.safety_gate.models import (
    GateDecision,
    GateReasonCode,
    GateSeverity,
    GateStatusRecord,
    SafetyGateAction,
)
from holomed.workflow.exceptions import (
    WorkflowLifecycleError,
    WorkflowSafetyInterlockError,
    WorkflowSequenceError,
    WorkflowSessionError,
    WorkflowTransitionError,
    WorkflowValidationError,
)
from holomed.workflow.models import (
    InterlockSeverity,
    SafetyInterlock,
    WorkflowPhase,
    WorkflowResumptionRequest,
)
from holomed.core.models import DispatcherState
from holomed.workflow.service import WorkflowService


def _setup_started_workflow_service(
    runtime_context: RuntimeContext,
    message_dispatcher: MessageDispatcher,
    secret_filter: SecretFilter,
    mock_persistence: MagicMock | None = None,
) -> WorkflowService:
    svc = WorkflowService(
        dispatcher=message_dispatcher,
        secret_filter=secret_filter,
        persistence_service=mock_persistence,
    )
    svc.initialize(runtime_context)
    if message_dispatcher.state != DispatcherState.STARTED:
        message_dispatcher.start()
    svc.start()
    return svc


def _advance_to_recovery_required(svc: WorkflowService, session_id: str) -> None:
    """Helper advancing workflow up to RECOVERY_REQUIRED."""
    from holomed.workflow.models import ConfirmationResponse

    svc.start_workflow(session_id)
    svc.transition_phase(session_id, WorkflowPhase.PRE_PROCEDURE_PLANNING, 1)
    svc.transition_phase(session_id, WorkflowPhase.REGISTRATION, 2)
    svc.transition_phase(session_id, WorkflowPhase.SAFETY_TIMEOUT, 3)

    # Human confirmation required before NAVIGATION
    req = svc.request_confirmation(session_id, WorkflowPhase.NAVIGATION, "Checklist verified", 4)
    resp = ConfirmationResponse(
        confirmation_id=req.confirmation_id,
        session_id=session_id,
        epoch_id=1,
        approved=True,
        operator_id="surgeon_01",
        timestamp_utc="2026-09-02T12:00:00+00:00",
        sequence_number=4,
    )
    svc.confirm(resp)
    svc.transition_phase(session_id, WorkflowPhase.NAVIGATION, 4)
    svc.transition_phase(session_id, WorkflowPhase.RECOVERY_REQUIRED, 5)


class TestWorkflowRecoveryResumption:
    """Test suite for WorkflowService.resume_from_recovery()."""

    def test_15_nominal_resume_from_recovery_success(
        self,
        runtime_context: RuntimeContext,
        message_dispatcher: MessageDispatcher,
        secret_filter: SecretFilter,
    ) -> None:
        mock_persistence = MagicMock()
        svc = _setup_started_workflow_service(runtime_context, message_dispatcher, secret_filter, mock_persistence)
        session_id = "session-m20-01"
        _advance_to_recovery_required(svc, session_id)

        # Register an explicit drift interlock for this session
        interlock = SafetyInterlock(
            interlock_id="interlock-drift-01",
            severity=InterlockSeverity.BLOCKING,
            condition_name="landmark_drift_exceeded",
            status=False,  # tripped
            reason="Drift exceeded threshold",
            source_service="drift_service",
            epoch_id=1,
            session_id=session_id,
        )
        svc._interlock_engine.register_interlock(interlock)
        assert svc._interlock_engine.has_blocking_interlock() is True

        # Mock M18 SafetyGateService returning PERMITTED_CLEAR
        mock_gate = MagicMock()
        mock_gate.evaluate.return_value = GateStatusRecord(
            session_id=session_id,
            decision=GateDecision.PERMITTED_CLEAR,
            severity=GateSeverity.NONE,
            reason_code=GateReasonCode.NONE,
            action=SafetyGateAction.WORKFLOW_RESUMPTION,
            sequence_number=6,
            subsystem_snapshots=(),
            evaluated_at_utc="2026-09-02T12:00:02.000000+00:00",
        )

        req = WorkflowResumptionRequest(
            session_id=session_id,
            sequence_number=6,
            now_utc="2026-09-02T12:00:02.000000+00:00",
            recovery_revision=1,
            cleared_interlock_ids=("interlock-drift-01",),
            reason="Drift corrected and verified by spatial recovery",
        )

        snapshot = svc.resume_from_recovery(req, mock_gate)

        # Invariant checks:
        # 1. State machine transitioned to NAVIGATION
        assert snapshot.current_phase == WorkflowPhase.NAVIGATION
        assert snapshot.previous_phase == WorkflowPhase.RECOVERY_REQUIRED
        assert snapshot.sequence_number == 6
        assert svc.get_workflow_state(session_id).current_phase == WorkflowPhase.NAVIGATION

        # 2. Interlock cleared
        assert svc._interlock_engine.has_blocking_interlock() is False
        assert "interlock-drift-01" not in svc._interlock_engine._interlocks

        # 3. M18 gate was evaluated inline
        mock_gate.evaluate.assert_called_once()
        gate_call_req = mock_gate.evaluate.call_args[0][0]
        assert gate_call_req.action == SafetyGateAction.WORKFLOW_RESUMPTION
        assert gate_call_req.session_id == session_id
        assert gate_call_req.recovery_revision == 1

        # 4. Audit was durably recorded
        mock_persistence.record_audit.assert_called_once()
        audit_call = mock_persistence.record_audit.call_args[1]
        assert audit_call["session_id"] == session_id
        assert audit_call["audit_report"]["event"] == "workflow.recovery.resumed"
        assert audit_call["audit_report"]["cleared_interlock_ids"] == ["interlock-drift-01"]

    def test_16_generic_transition_blocked_during_recovery_required(
        self,
        runtime_context: RuntimeContext,
        message_dispatcher: MessageDispatcher,
        secret_filter: SecretFilter,
    ) -> None:
        svc = _setup_started_workflow_service(runtime_context, message_dispatcher, secret_filter)
        session_id = "session-m20-02"
        _advance_to_recovery_required(svc, session_id)

        # Attempting generic transition_phase to NAVIGATION MUST raise WorkflowTransitionError
        with pytest.raises(WorkflowTransitionError) as excinfo:
            svc.transition_phase(session_id, WorkflowPhase.NAVIGATION, sequence_number=6)
        assert "Illegal transition from RECOVERY_REQUIRED to NAVIGATION" in str(excinfo.value)
        assert svc.get_workflow_state(session_id).current_phase == WorkflowPhase.RECOVERY_REQUIRED

    def test_17_resume_from_recovery_when_not_in_recovery_required_fails(
        self,
        runtime_context: RuntimeContext,
        message_dispatcher: MessageDispatcher,
        secret_filter: SecretFilter,
    ) -> None:
        svc = _setup_started_workflow_service(runtime_context, message_dispatcher, secret_filter)
        session_id = "session-m20-03"
        svc.start_workflow(session_id)
        # Session is in PATIENT_CONTEXT, not RECOVERY_REQUIRED
        req = WorkflowResumptionRequest(
            session_id=session_id,
            sequence_number=2,
            now_utc="2026-09-02T12:00:02.000000+00:00",
            recovery_revision=1,
            cleared_interlock_ids=("dummy-id",),
        )
        with pytest.raises(WorkflowTransitionError) as excinfo:
            svc.resume_from_recovery(req, MagicMock())
        assert "expected RECOVERY_REQUIRED" in str(excinfo.value)

    def test_18_non_monotonic_sequence_number_fails(
        self,
        runtime_context: RuntimeContext,
        message_dispatcher: MessageDispatcher,
        secret_filter: SecretFilter,
    ) -> None:
        svc = _setup_started_workflow_service(runtime_context, message_dispatcher, secret_filter)
        session_id = "session-m20-04"
        _advance_to_recovery_required(svc, session_id)  # sequence is 5

        mock_gate = MagicMock()
        mock_gate.evaluate.return_value = GateStatusRecord(
            session_id=session_id,
            decision=GateDecision.PERMITTED_CLEAR,
            severity=GateSeverity.NONE,
            reason_code=GateReasonCode.NONE,
            action=SafetyGateAction.WORKFLOW_RESUMPTION,
            sequence_number=5,
            subsystem_snapshots=(),
            evaluated_at_utc="2026-09-02T12:00:02.000000+00:00",
        )

        # Sequence 5 is <= last_sequence 5 -> fails
        req = WorkflowResumptionRequest(
            session_id=session_id,
            sequence_number=5,
            now_utc="2026-09-02T12:00:02.000000+00:00",
            recovery_revision=1,
            cleared_interlock_ids=("dummy-id",),
        )
        with pytest.raises(WorkflowSequenceError) as excinfo:
            svc.resume_from_recovery(req, mock_gate)
        assert "must exceed last sequence" in str(excinfo.value)
        assert svc.get_workflow_state(session_id).current_phase == WorkflowPhase.RECOVERY_REQUIRED

    def test_19_m18_gate_denial_leaves_state_and_interlocks_untouched(
        self,
        runtime_context: RuntimeContext,
        message_dispatcher: MessageDispatcher,
        secret_filter: SecretFilter,
    ) -> None:
        svc = _setup_started_workflow_service(runtime_context, message_dispatcher, secret_filter)
        session_id = "session-m20-05"
        _advance_to_recovery_required(svc, session_id)

        interlock = SafetyInterlock(
            interlock_id="interlock-prox-01",
            severity=InterlockSeverity.BLOCKING,
            condition_name="proximity_breach",
            status=False,
            reason="Proximity zone breached",
            source_service="proximity",
            epoch_id=1,
            session_id=session_id,
        )
        svc._interlock_engine.register_interlock(interlock)

        # M18 denies resumption
        mock_gate = MagicMock()
        mock_gate.evaluate.return_value = GateStatusRecord(
            session_id=session_id,
            decision=GateDecision.DENIED_INTERLOCKED,
            severity=GateSeverity.BLOCKING,
            reason_code=GateReasonCode.LANDMARK_DRIFT_EXCEEDED,
            action=SafetyGateAction.WORKFLOW_RESUMPTION,
            sequence_number=6,
            subsystem_snapshots=(),
            evaluated_at_utc="2026-09-02T12:00:02.000000+00:00",
        )

        req = WorkflowResumptionRequest(
            session_id=session_id,
            sequence_number=6,
            now_utc="2026-09-02T12:00:02.000000+00:00",
            recovery_revision=1,
            cleared_interlock_ids=("interlock-prox-01",),
        )

        with pytest.raises(WorkflowSafetyInterlockError) as excinfo:
            svc.resume_from_recovery(req, mock_gate)
        assert "M18 Safety Gate refused workflow resumption" in str(excinfo.value)

        # Invariant: state machine remains RECOVERY_REQUIRED, interlock remains present
        assert svc.get_workflow_state(session_id).current_phase == WorkflowPhase.RECOVERY_REQUIRED
        assert "interlock-prox-01" in svc._interlock_engine._interlocks

    def test_20_interlock_not_found_causes_transactional_rollback(
        self,
        runtime_context: RuntimeContext,
        message_dispatcher: MessageDispatcher,
        secret_filter: SecretFilter,
    ) -> None:
        svc = _setup_started_workflow_service(runtime_context, message_dispatcher, secret_filter)
        session_id = "session-m20-06"
        _advance_to_recovery_required(svc, session_id)

        existing_interlock = SafetyInterlock(
            interlock_id="interlock-valid-01",
            severity=InterlockSeverity.BLOCKING,
            condition_name="drift",
            status=False,
            reason="Drift",
            source_service="drift",
            epoch_id=1,
            session_id=session_id,
        )
        svc._interlock_engine.register_interlock(existing_interlock)

        mock_gate = MagicMock()
        mock_gate.evaluate.return_value = GateStatusRecord(
            session_id=session_id,
            decision=GateDecision.PERMITTED_CLEAR,
            severity=GateSeverity.NONE,
            reason_code=GateReasonCode.NONE,
            action=SafetyGateAction.WORKFLOW_RESUMPTION,
            sequence_number=6,
            subsystem_snapshots=(),
            evaluated_at_utc="2026-09-02T12:00:02.000000+00:00",
        )

        # Request attempts to clear non-existent interlock
        req = WorkflowResumptionRequest(
            session_id=session_id,
            sequence_number=6,
            now_utc="2026-09-02T12:00:02.000000+00:00",
            recovery_revision=1,
            cleared_interlock_ids=("non-existent-interlock",),
        )

        with pytest.raises(WorkflowSafetyInterlockError) as excinfo:
            svc.resume_from_recovery(req, mock_gate)
        assert "not found in interlock engine" in str(excinfo.value)

        # Prior state and interlocks preserved intact
        assert svc.get_workflow_state(session_id).current_phase == WorkflowPhase.RECOVERY_REQUIRED
        assert "interlock-valid-01" in svc._interlock_engine._interlocks

    def test_21_interlock_session_mismatch_rejected(
        self,
        runtime_context: RuntimeContext,
        message_dispatcher: MessageDispatcher,
        secret_filter: SecretFilter,
    ) -> None:
        svc = _setup_started_workflow_service(runtime_context, message_dispatcher, secret_filter)
        session_id = "session-m20-07"
        _advance_to_recovery_required(svc, session_id)

        alien_interlock = SafetyInterlock(
            interlock_id="interlock-alien-01",
            severity=InterlockSeverity.BLOCKING,
            condition_name="drift",
            status=False,
            reason="Drift on another session",
            source_service="drift",
            epoch_id=1,
            session_id="alien-session",  # Different session
        )
        svc._interlock_engine.register_interlock(alien_interlock)

        mock_gate = MagicMock()
        mock_gate.evaluate.return_value = GateStatusRecord(
            session_id=session_id,
            decision=GateDecision.PERMITTED_CLEAR,
            severity=GateSeverity.NONE,
            reason_code=GateReasonCode.NONE,
            action=SafetyGateAction.WORKFLOW_RESUMPTION,
            sequence_number=6,
            subsystem_snapshots=(),
            evaluated_at_utc="2026-09-02T12:00:02.000000+00:00",
        )

        req = WorkflowResumptionRequest(
            session_id=session_id,
            sequence_number=6,
            now_utc="2026-09-02T12:00:02.000000+00:00",
            recovery_revision=1,
            cleared_interlock_ids=("interlock-alien-01",),
        )

        with pytest.raises(WorkflowSafetyInterlockError) as excinfo:
            svc.resume_from_recovery(req, mock_gate)
        assert "belongs to session 'alien-session'" in str(excinfo.value)
        assert svc.get_workflow_state(session_id).current_phase == WorkflowPhase.RECOVERY_REQUIRED

    def test_22_interlock_non_recovery_source_rejected(
        self,
        runtime_context: RuntimeContext,
        message_dispatcher: MessageDispatcher,
        secret_filter: SecretFilter,
    ) -> None:
        svc = _setup_started_workflow_service(runtime_context, message_dispatcher, secret_filter)
        session_id = "session-m20-08"
        _advance_to_recovery_required(svc, session_id)

        unrelated_interlock = SafetyInterlock(
            interlock_id="interlock-unrelated-01",
            severity=InterlockSeverity.BLOCKING,
            condition_name="network_loss",
            status=False,
            reason="Network loss",
            source_service="telemetry_network",  # Not recovery-eligible
            epoch_id=1,
            session_id=session_id,
        )
        svc._interlock_engine.register_interlock(unrelated_interlock)

        mock_gate = MagicMock()
        mock_gate.evaluate.return_value = GateStatusRecord(
            session_id=session_id,
            decision=GateDecision.PERMITTED_CLEAR,
            severity=GateSeverity.NONE,
            reason_code=GateReasonCode.NONE,
            action=SafetyGateAction.WORKFLOW_RESUMPTION,
            sequence_number=6,
            subsystem_snapshots=(),
            evaluated_at_utc="2026-09-02T12:00:02.000000+00:00",
        )

        req = WorkflowResumptionRequest(
            session_id=session_id,
            sequence_number=6,
            now_utc="2026-09-02T12:00:02.000000+00:00",
            recovery_revision=1,
            cleared_interlock_ids=("interlock-unrelated-01",),
        )

        with pytest.raises(WorkflowSafetyInterlockError) as excinfo:
            svc.resume_from_recovery(req, mock_gate)
        assert "is not eligible for recovery clearance" in str(excinfo.value)
        assert svc.get_workflow_state(session_id).current_phase == WorkflowPhase.RECOVERY_REQUIRED

    def test_23_remaining_blocking_interlocks_prevent_resumption(
        self,
        runtime_context: RuntimeContext,
        message_dispatcher: MessageDispatcher,
        secret_filter: SecretFilter,
    ) -> None:
        svc = _setup_started_workflow_service(runtime_context, message_dispatcher, secret_filter)
        session_id = "session-m20-09"
        _advance_to_recovery_required(svc, session_id)

        # 2 interlocks: one cleared, one still tripped!
        it1 = SafetyInterlock(
            interlock_id="it-01",
            severity=InterlockSeverity.BLOCKING,
            condition_name="drift",
            status=False,
            reason="Drift",
            source_service="drift",
            epoch_id=1,
            session_id=session_id,
        )
        it2 = SafetyInterlock(
            interlock_id="it-02",
            severity=InterlockSeverity.BLOCKING,
            condition_name="proximity",
            status=False,
            reason="Proximity",
            source_service="proximity",
            epoch_id=1,
            session_id=session_id,
        )
        svc._interlock_engine.register_interlock(it1)
        svc._interlock_engine.register_interlock(it2)

        mock_gate = MagicMock()
        mock_gate.evaluate.return_value = GateStatusRecord(
            session_id=session_id,
            decision=GateDecision.PERMITTED_CLEAR,
            severity=GateSeverity.NONE,
            reason_code=GateReasonCode.NONE,
            action=SafetyGateAction.WORKFLOW_RESUMPTION,
            sequence_number=6,
            subsystem_snapshots=(),
            evaluated_at_utc="2026-09-02T12:00:02.000000+00:00",
        )

        # Only clear it-01, leaving it-02 tripped
        req = WorkflowResumptionRequest(
            session_id=session_id,
            sequence_number=6,
            now_utc="2026-09-02T12:00:02.000000+00:00",
            recovery_revision=1,
            cleared_interlock_ids=("it-01",),
        )

        with pytest.raises(WorkflowSafetyInterlockError) as excinfo:
            svc.resume_from_recovery(req, mock_gate)
        assert "Active blocking or critical safety interlocks remain" in str(excinfo.value)

        # Transaction rollback restored it-01 and preserved RECOVERY_REQUIRED!
        assert "it-01" in svc._interlock_engine._interlocks
        assert "it-02" in svc._interlock_engine._interlocks
        assert svc.get_workflow_state(session_id).current_phase == WorkflowPhase.RECOVERY_REQUIRED

    def test_24_anti_replay_repeated_resumption_fails(
        self,
        runtime_context: RuntimeContext,
        message_dispatcher: MessageDispatcher,
        secret_filter: SecretFilter,
    ) -> None:
        svc = _setup_started_workflow_service(runtime_context, message_dispatcher, secret_filter)
        session_id = "session-m20-10"
        _advance_to_recovery_required(svc, session_id)

        it = SafetyInterlock(
            interlock_id="it-drift",
            severity=InterlockSeverity.BLOCKING,
            condition_name="drift",
            status=False,
            reason="Drift",
            source_service="drift",
            epoch_id=1,
            session_id=session_id,
        )
        svc._interlock_engine.register_interlock(it)

        mock_gate = MagicMock()
        mock_gate.evaluate.return_value = GateStatusRecord(
            session_id=session_id,
            decision=GateDecision.PERMITTED_CLEAR,
            severity=GateSeverity.NONE,
            reason_code=GateReasonCode.NONE,
            action=SafetyGateAction.WORKFLOW_RESUMPTION,
            sequence_number=6,
            subsystem_snapshots=(),
            evaluated_at_utc="2026-09-02T12:00:02.000000+00:00",
        )

        req = WorkflowResumptionRequest(
            session_id=session_id,
            sequence_number=6,
            now_utc="2026-09-02T12:00:02.000000+00:00",
            recovery_revision=1,
            cleared_interlock_ids=("it-drift",),
        )

        # First resumption succeeds
        svc.resume_from_recovery(req, mock_gate)
        assert svc.get_workflow_state(session_id).current_phase == WorkflowPhase.NAVIGATION

        # Replaying identical request fails with WorkflowTransitionError (phase is now NAVIGATION)
        with pytest.raises(WorkflowTransitionError):
            svc.resume_from_recovery(req, mock_gate)

    def test_25_no_standalone_public_recovery_clear_bypass(self) -> None:
        """Verify that WorkflowService provides NO public standalone interlock clearing bypass."""
        assert not hasattr(WorkflowService, "clear_session_interlocks"), (
            "WorkflowService must not expose clear_session_interlocks"
        )
        assert not hasattr(WorkflowService, "clear_recovery_interlocks"), (
            "WorkflowService must not expose clear_recovery_interlocks"
        )
        assert not hasattr(WorkflowService, "clear_interlocks"), (
            "WorkflowService must not expose clear_interlocks"
        )

    def test_27_owner_level_state_machine_staged_transaction_and_rollback(self) -> None:
        """Verify WorkflowStateMachine requires an internal transaction capability for recovery resumption."""
        from holomed.workflow._transaction import _create_recovery_capability
        from holomed.workflow.exceptions import WorkflowAuthorizationError
        from holomed.workflow.procedure import STANDARD_SURGICAL_GUIDANCE_V1
        from holomed.workflow.state_machine import WorkflowStateMachine

        sm = WorkflowStateMachine(
            workflow_id="wf-cp-01",
            session_id="session-cp-01",
            epoch_id=1,
            procedure=STANDARD_SURGICAL_GUIDANCE_V1,
            initial_phase=WorkflowPhase.PATIENT_CONTEXT,
        )
        sm.transition_to(WorkflowPhase.PRE_PROCEDURE_PLANNING, sequence_number=1)
        sm.transition_to(WorkflowPhase.RECOVERY_REQUIRED, sequence_number=2)

        # Invariant: No public resume_from_recovery or checkpoint methods
        assert not hasattr(sm, "resume_from_recovery"), "WorkflowStateMachine must not expose public resume_from_recovery"
        assert not hasattr(sm, "create_checkpoint"), "WorkflowStateMachine must not expose create_checkpoint"
        assert not hasattr(sm, "restore_checkpoint"), "WorkflowStateMachine must not expose restore_checkpoint"

        # Attempting resumption without capability fails with WorkflowAuthorizationError
        with pytest.raises(WorkflowAuthorizationError):
            sm.begin_recovery_resumption(capability=None)  # type: ignore

        # Create valid internal capability
        cap = _create_recovery_capability(
            service_instance_id=1,
            session_id="session-cp-01",
            workflow_id="wf-cp-01",
            recovery_revision=1,
            sequence_number=3,
        )

        # Stage resumption
        sm.begin_recovery_resumption(capability=cap)
        assert sm.current_phase == WorkflowPhase.NAVIGATION
        assert sm.last_sequence_number == 3

        # Abort resumption (rollback)
        sm.abort_recovery_resumption(capability=cap)
        assert sm.current_phase == WorkflowPhase.RECOVERY_REQUIRED
        assert sm.last_sequence_number == 2
        assert sm.snapshot.current_phase == WorkflowPhase.RECOVERY_REQUIRED
        assert sm.snapshot.sequence_number == 2

        # Commit resumption
        sm.begin_recovery_resumption(capability=cap)
        snap = sm.commit_recovery_resumption(capability=cap)
        assert snap.current_phase == WorkflowPhase.NAVIGATION
        assert snap.sequence_number == 3
        assert sm.current_phase == WorkflowPhase.NAVIGATION

    def test_28_owner_level_interlock_engine_staged_clearance_and_rollback(self) -> None:
        """Verify SafetyInterlockEngine requires an internal transaction capability for recovery clearance."""
        from holomed.workflow._transaction import _create_recovery_capability
        from holomed.workflow.exceptions import WorkflowAuthorizationError
        from holomed.workflow.interlocks import SafetyInterlockEngine

        engine = SafetyInterlockEngine()
        it = SafetyInterlock(
            interlock_id="it-cp-01",
            severity=InterlockSeverity.BLOCKING,
            condition_name="recovery",
            status=False,
            reason="Recovery test interlock",
            source_service="recovery",
            epoch_id=1,
            session_id="session-cp-02",
        )
        engine.register_interlock(it)
        assert engine.has_blocking_interlock() is True

        # Invariant: No public clearance or checkpoint methods
        assert not hasattr(engine, "clear_explicit_recovery_interlocks"), (
            "SafetyInterlockEngine must not expose clear_explicit_recovery_interlocks"
        )
        assert not hasattr(engine, "create_checkpoint"), "SafetyInterlockEngine must not expose create_checkpoint"
        assert not hasattr(engine, "restore_checkpoint"), "SafetyInterlockEngine must not expose restore_checkpoint"

        # Attempting clearance without capability fails with WorkflowAuthorizationError
        with pytest.raises(WorkflowAuthorizationError):
            engine.stage_recovery_clearance(capability=None, interlock_ids=("it-cp-01",))  # type: ignore

        # Create valid capability
        cap = _create_recovery_capability(
            service_instance_id=1,
            session_id="session-cp-02",
            workflow_id="wf-02",
            recovery_revision=1,
            sequence_number=5,
        )

        # Stage clearance
        cleared = engine.stage_recovery_clearance(capability=cap, interlock_ids=("it-cp-01",))
        assert cleared == ("it-cp-01",)
        assert engine.has_blocking_interlock() is False
        assert engine.interlock_count == 0

        # Abort clearance (rollback)
        engine.abort_recovery_clearance(capability=cap)
        assert engine.has_blocking_interlock() is True
        assert engine.interlock_count == 1
        assert engine.all_interlocks[0].interlock_id == "it-cp-01"

        # Commit clearance
        engine.stage_recovery_clearance(capability=cap, interlock_ids=("it-cp-01",))
        engine.commit_recovery_clearance(capability=cap)
        assert engine.interlock_count == 0
        assert engine.has_blocking_interlock() is False

    def test_29_workflow_service_has_zero_cross_object_private_access(self) -> None:
        """Verify via AST that WorkflowService.resume_from_recovery contains zero cross-object private accesses."""
        import ast
        from pathlib import Path

        svc_path = Path("python/holomed/workflow/service.py")
        tree = ast.parse(svc_path.read_text(encoding="utf-8"), filename=str(svc_path))

        # Find resume_from_recovery method
        resume_func = None
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef) and node.name == "resume_from_recovery":
                resume_func = node
                break
        assert resume_func is not None, "resume_from_recovery method not found in service.py"

        violations = []
        for node in ast.walk(resume_func):
            if isinstance(node, ast.Attribute):
                # Check for sm._*
                if isinstance(node.value, ast.Name) and node.value.id == "sm" and node.attr.startswith("_"):
                    violations.append(f"sm.{node.attr} at line {node.lineno}")
                # Check for interlock engine private access e.g. self._interlock_engine._*
                if isinstance(node.value, ast.Attribute) and node.value.attr == "_interlock_engine" and node.attr.startswith("_"):
                    violations.append(f"self._interlock_engine.{node.attr} at line {node.lineno}")

        assert len(violations) == 0, f"Found prohibited cross-object private accesses: {violations}"

    def test_30_object_setattr_is_absent_in_workflow_models(self) -> None:
        """Verify that WorkflowResumptionRequest contains zero object.__setattr__ and rejects non-tuple."""
        import ast
        from pathlib import Path

        models_path = Path("python/holomed/workflow/models.py")
        tree = ast.parse(models_path.read_text(encoding="utf-8"), filename=str(models_path))

        resump_class = None
        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef) and node.name == "WorkflowResumptionRequest":
                resump_class = node
                break
        assert resump_class is not None, "WorkflowResumptionRequest not found"

        for node in ast.walk(resump_class):
            if isinstance(node, ast.Attribute) and node.attr == "__setattr__":
                pytest.fail(f"Prohibited __setattr__ found in WorkflowResumptionRequest at line {node.lineno}")

        # Verify passing a list instead of tuple raises WorkflowValidationError
        with pytest.raises(WorkflowValidationError) as excinfo:
            WorkflowResumptionRequest(
                session_id="session-tuple-test",
                sequence_number=1,
                now_utc="2026-09-02T12:00:00.000000+00:00",
                recovery_revision=1,
                cleared_interlock_ids=["not-a-tuple"],  # type: ignore # list must be rejected
            )
        assert "must be a non-empty tuple" in str(excinfo.value)

    def test_31_audit_failure_restores_original_state(
        self,
        runtime_context: RuntimeContext,
        message_dispatcher: MessageDispatcher,
        secret_filter: SecretFilter,
    ) -> None:
        """Verify that persistence audit failure cleanly rolls back the workflow to RECOVERY_REQUIRED."""
        mock_persistence = MagicMock()
        mock_persistence.record_audit.side_effect = RuntimeError("Persistence audit disk error")
        svc = _setup_started_workflow_service(runtime_context, message_dispatcher, secret_filter, mock_persistence)
        session_id = "session-m20-audit-fail"
        _advance_to_recovery_required(svc, session_id)

        it = SafetyInterlock(
            interlock_id="it-audit-fail",
            severity=InterlockSeverity.BLOCKING,
            condition_name="recovery",
            status=False,
            reason="Recovery interlock",
            source_service="recovery",
            epoch_id=1,
            session_id=session_id,
        )
        svc._interlock_engine.register_interlock(it)

        mock_gate = MagicMock()
        mock_gate.evaluate.return_value = GateStatusRecord(
            session_id=session_id,
            decision=GateDecision.PERMITTED_CLEAR,
            severity=GateSeverity.NONE,
            reason_code=GateReasonCode.NONE,
            action=SafetyGateAction.WORKFLOW_RESUMPTION,
            sequence_number=6,
            subsystem_snapshots=(),
            evaluated_at_utc="2026-09-02T12:00:02.000000+00:00",
        )

        req = WorkflowResumptionRequest(
            session_id=session_id,
            sequence_number=6,
            now_utc="2026-09-02T12:00:02.000000+00:00",
            recovery_revision=1,
            cleared_interlock_ids=("it-audit-fail",),
        )

        # Persistence failure must re-raise RuntimeError
        with pytest.raises(RuntimeError) as excinfo:
            svc.resume_from_recovery(req, mock_gate)
        assert "Persistence audit disk error" in str(excinfo.value)

        # Invariant verified: State rolled back to RECOVERY_REQUIRED, sequence 5, interlock restored
        state = svc.get_workflow_state(session_id)
        assert state.current_phase == WorkflowPhase.RECOVERY_REQUIRED
        assert state.sequence_number == 5
        assert svc._interlock_engine.has_blocking_interlock() is True
        assert any(x.interlock_id == "it-audit-fail" for x in svc._interlock_engine.all_interlocks)

    def test_32_attack_a_cannot_restore_navigation_from_recovery_required(self) -> None:
        """Attack A: External code cannot restore NAVIGATION from RECOVERY_REQUIRED without resume_from_recovery."""
        from holomed.workflow.procedure import STANDARD_SURGICAL_GUIDANCE_V1
        from holomed.workflow.state_machine import WorkflowStateMachine

        sm = WorkflowStateMachine(
            workflow_id="wf-atk-a",
            session_id="session-atk-a",
            epoch_id=1,
            procedure=STANDARD_SURGICAL_GUIDANCE_V1,
            initial_phase=WorkflowPhase.PATIENT_CONTEXT,
        )
        sm.transition_to(WorkflowPhase.PRE_PROCEDURE_PLANNING, sequence_number=1)
        sm.transition_to(WorkflowPhase.RECOVERY_REQUIRED, sequence_number=2)

        # 1. No restore_checkpoint method exists
        assert not hasattr(sm, "restore_checkpoint")
        assert not hasattr(WorkflowService, "restore_checkpoint")

        # 2. transition_to(NAVIGATION) is blocked by state machine transition graph
        with pytest.raises(WorkflowTransitionError) as excinfo:
            sm.transition_to(WorkflowPhase.NAVIGATION, sequence_number=3)
        assert "Illegal transition from RECOVERY_REQUIRED to NAVIGATION" in str(excinfo.value)

    def test_33_attack_b_cannot_forge_a_checkpoint(self) -> None:
        """Attack B: External code cannot forge a checkpoint because no checkpoint classes or APIs exist."""
        import holomed.workflow as wf_pkg

        # Checkpoint classes are NOT in holomed.workflow exports
        assert not hasattr(wf_pkg, "StateMachineCheckpoint")
        assert not hasattr(wf_pkg, "InterlockEngineCheckpoint")
        assert "StateMachineCheckpoint" not in wf_pkg.__all__
        assert "InterlockEngineCheckpoint" not in wf_pkg.__all__

        # State machine has no checkpoint ingestion method
        from holomed.workflow.procedure import STANDARD_SURGICAL_GUIDANCE_V1
        from holomed.workflow.state_machine import WorkflowStateMachine
        sm = WorkflowStateMachine("wf-b", "s-b", 1, STANDARD_SURGICAL_GUIDANCE_V1)
        assert not hasattr(sm, "restore_checkpoint")
        assert not hasattr(sm, "create_checkpoint")

    def test_34_attack_c_and_d_no_checkpoint_replay_or_cross_session_restoration(self) -> None:
        """Attacks C & D: Checkpoint replay and cross-session restoration are impossible (no checkpoint APIs)."""
        from holomed.workflow.interlocks import SafetyInterlockEngine
        from holomed.workflow.state_machine import WorkflowStateMachine

        assert not hasattr(WorkflowStateMachine, "create_checkpoint")
        assert not hasattr(WorkflowStateMachine, "restore_checkpoint")
        assert not hasattr(SafetyInterlockEngine, "create_checkpoint")
        assert not hasattr(SafetyInterlockEngine, "restore_checkpoint")

    def test_35_attack_e_cannot_wipe_interlocks_arbitrarily(self) -> None:
        """Attack E: External code cannot wipe interlocks arbitrarily without valid internal capability."""
        from holomed.workflow._transaction import _create_recovery_capability
        from holomed.workflow.exceptions import WorkflowAuthorizationError
        from holomed.workflow.interlocks import SafetyInterlockEngine

        engine = SafetyInterlockEngine()
        it = SafetyInterlock(
            interlock_id="it-crit-01",
            severity=InterlockSeverity.CRITICAL,
            condition_name="hardware_fault",
            status=False,
            reason="Critical hardware fault",
            source_service="hardware",
            epoch_id=1,
            session_id="session-atk-e",
        )
        engine.register_interlock(it)

        # 1. No public clearance or restore methods exist on SafetyInterlockEngine
        assert not hasattr(engine, "clear_explicit_recovery_interlocks")
        assert not hasattr(engine, "restore_checkpoint")

        # 2. Invocations without capability are rejected
        with pytest.raises(WorkflowAuthorizationError):
            engine.stage_recovery_clearance(None, ("it-crit-01",))  # type: ignore

        # 3. Even with capability, non-recovery interlocks cannot be cleared
        cap = _create_recovery_capability(
            service_instance_id=1,
            session_id="session-atk-e",
            workflow_id="wf-atk-e",
            recovery_revision=1,
            sequence_number=1,
        )
        with pytest.raises(WorkflowSafetyInterlockError) as excinfo:
            engine.stage_recovery_clearance(cap, ("it-crit-01",))
        assert "is not eligible for recovery clearance" in str(excinfo.value)
        assert engine.has_critical_interlock() is True

        # 4. Empty IDs are rejected
        with pytest.raises(WorkflowValidationError):
            engine.stage_recovery_clearance(cap, ())

    def test_36_attack_f_and_g_cannot_replace_history_or_manipulate_sequence(self) -> None:
        """Attacks F & G: External code cannot replace workflow history or manipulate sequence numbers."""
        from holomed.workflow._transaction import _create_recovery_capability
        from holomed.workflow.exceptions import WorkflowAuthorizationError
        from holomed.workflow.procedure import STANDARD_SURGICAL_GUIDANCE_V1
        from holomed.workflow.state_machine import WorkflowStateMachine

        sm = WorkflowStateMachine("wf-fg", "s-fg", 1, STANDARD_SURGICAL_GUIDANCE_V1)
        sm.transition_to(WorkflowPhase.PRE_PROCEDURE_PLANNING, sequence_number=5)
        sm.transition_to(WorkflowPhase.RECOVERY_REQUIRED, sequence_number=10)

        # Invariant: No public resume_from_recovery or restore methods
        assert not hasattr(sm, "resume_from_recovery")
        assert not hasattr(sm, "restore_checkpoint")

        # Invocations without capability are rejected
        with pytest.raises(WorkflowAuthorizationError):
            sm.begin_recovery_resumption(None)  # type: ignore

        # Non-monotonic sequence in capability is rejected
        stale_cap = _create_recovery_capability(
            service_instance_id=1,
            session_id="s-fg",
            workflow_id="wf-fg",
            recovery_revision=1,
            sequence_number=10,
        )
        with pytest.raises(WorkflowSequenceError) as excinfo:
            sm.begin_recovery_resumption(stale_cap)
        assert "must exceed last sequence 10" in str(excinfo.value)

        backwards_cap = _create_recovery_capability(
            service_instance_id=1,
            session_id="s-fg",
            workflow_id="wf-fg",
            recovery_revision=1,
            sequence_number=8,
        )
        with pytest.raises(WorkflowSequenceError) as excinfo:
            sm.begin_recovery_resumption(backwards_cap)
        assert "must exceed last sequence 10" in str(excinfo.value)

        # History is immutable tuple in snapshot property
        snap = sm.snapshot
        assert snap.sequence_number == 10
        assert snap.current_phase == WorkflowPhase.RECOVERY_REQUIRED

    def test_37_attack_h_cannot_bypass_m18_safety_gate(
        self,
        runtime_context: RuntimeContext,
        message_dispatcher: MessageDispatcher,
        secret_filter: SecretFilter,
    ) -> None:
        """Attack H: Cannot bypass M18 safety gate authorization during recovery resumption."""
        svc = _setup_started_workflow_service(runtime_context, message_dispatcher, secret_filter)
        session_id = "session-atk-h"
        _advance_to_recovery_required(svc, session_id)

        # 1. Missing safety gate service fails closed
        req = WorkflowResumptionRequest(
            session_id=session_id,
            sequence_number=6,
            now_utc="2026-09-02T12:00:00.000000+00:00",
            recovery_revision=1,
            cleared_interlock_ids=("some-it",),
        )
        with pytest.raises(WorkflowSafetyInterlockError) as excinfo:
            svc.resume_from_recovery(req, None)
        assert "SafetyGateService is required" in str(excinfo.value)

        # 2. Denied M18 gate fails closed without modifying state
        mock_gate = MagicMock()
        mock_gate.evaluate.return_value = GateStatusRecord(
            session_id=session_id,
            decision=GateDecision.DENIED_INTERLOCKED,
            severity=GateSeverity.BLOCKING,
            reason_code=GateReasonCode.SPATIAL_RECOVERY_FAILED,
            action=SafetyGateAction.WORKFLOW_RESUMPTION,
            sequence_number=6,
            subsystem_snapshots=(),
            evaluated_at_utc="2026-09-02T12:00:00.000000+00:00",
        )
        with pytest.raises(WorkflowSafetyInterlockError) as excinfo:
            svc.resume_from_recovery(req, mock_gate)
        assert "M18 Safety Gate refused workflow resumption" in str(excinfo.value)
        assert svc.get_workflow_state(session_id).current_phase == WorkflowPhase.RECOVERY_REQUIRED

    def test_38_attack_i_cannot_bypass_resume_from_recovery(
        self,
        runtime_context: RuntimeContext,
        message_dispatcher: MessageDispatcher,
        secret_filter: SecretFilter,
    ) -> None:
        """Attack I: resume_from_recovery() is the ONLY public path from RECOVERY_REQUIRED to NAVIGATION."""
        svc = _setup_started_workflow_service(runtime_context, message_dispatcher, secret_filter)
        session_id = "session-atk-i"
        _advance_to_recovery_required(svc, session_id)

        # Generic transition_phase to NAVIGATION is blocked
        with pytest.raises(WorkflowTransitionError):
            svc.transition_phase(session_id, WorkflowPhase.NAVIGATION, sequence_number=6)

        assert svc.get_workflow_state(session_id).current_phase == WorkflowPhase.RECOVERY_REQUIRED

    def test_39_fake_constructed_capability_rejected(self) -> None:
        """Verify duck-typed fake capability is rejected by both state machine and interlock engine."""
        from holomed.workflow.exceptions import WorkflowAuthorizationError
        from holomed.workflow.interlocks import SafetyInterlockEngine
        from holomed.workflow.procedure import STANDARD_SURGICAL_GUIDANCE_V1
        from holomed.workflow.state_machine import WorkflowStateMachine

        class FakeCapability:
            is_active = True
            session_id = "session-fake"
            workflow_id = "wf-fake"
            sequence_number = 3
            recovery_revision = 1
            service_instance_id = 1
            transaction_id = "fake-tx"

        fake = FakeCapability()
        sm = WorkflowStateMachine("wf-fake", "session-fake", 1, STANDARD_SURGICAL_GUIDANCE_V1)
        sm.transition_to(WorkflowPhase.PRE_PROCEDURE_PLANNING, sequence_number=1)
        sm.transition_to(WorkflowPhase.RECOVERY_REQUIRED, sequence_number=2)

        with pytest.raises(WorkflowAuthorizationError):
            sm.begin_recovery_resumption(fake)  # type: ignore

        engine = SafetyInterlockEngine()
        with pytest.raises(WorkflowAuthorizationError):
            engine.stage_recovery_clearance(fake, ("it-fake",))  # type: ignore

    def test_40_capability_cannot_be_reused_after_commit(self) -> None:
        """Verify capability cannot be reused after transaction commit."""
        from holomed.workflow._transaction import _create_recovery_capability
        from holomed.workflow.exceptions import WorkflowAuthorizationError
        from holomed.workflow.procedure import STANDARD_SURGICAL_GUIDANCE_V1
        from holomed.workflow.state_machine import WorkflowStateMachine

        sm = WorkflowStateMachine("wf-reuse", "session-reuse", 1, STANDARD_SURGICAL_GUIDANCE_V1)
        sm.transition_to(WorkflowPhase.PRE_PROCEDURE_PLANNING, sequence_number=1)
        sm.transition_to(WorkflowPhase.RECOVERY_REQUIRED, sequence_number=2)

        cap = _create_recovery_capability(
            service_instance_id=1,
            session_id="session-reuse",
            workflow_id="wf-reuse",
            recovery_revision=1,
            sequence_number=3,
        )

        sm.begin_recovery_resumption(cap)
        sm.commit_recovery_resumption(cap)
        cap.invalidate()
        assert cap.is_active is False

        # Attempt to reuse the invalidated capability in a new transaction
        with pytest.raises(WorkflowAuthorizationError):
            sm.begin_recovery_resumption(cap)

        with pytest.raises(WorkflowAuthorizationError):
            sm.commit_recovery_resumption(cap)

    def test_41_capability_cannot_be_reused_after_abort(self) -> None:
        """Verify capability cannot be reused after transaction abort/rollback."""
        from holomed.workflow._transaction import _create_recovery_capability
        from holomed.workflow.exceptions import WorkflowAuthorizationError
        from holomed.workflow.procedure import STANDARD_SURGICAL_GUIDANCE_V1
        from holomed.workflow.state_machine import WorkflowStateMachine

        sm = WorkflowStateMachine("wf-abort", "session-abort", 1, STANDARD_SURGICAL_GUIDANCE_V1)
        sm.transition_to(WorkflowPhase.PRE_PROCEDURE_PLANNING, sequence_number=1)
        sm.transition_to(WorkflowPhase.RECOVERY_REQUIRED, sequence_number=2)

        cap = _create_recovery_capability(
            service_instance_id=1,
            session_id="session-abort",
            workflow_id="wf-abort",
            recovery_revision=1,
            sequence_number=3,
        )

        sm.begin_recovery_resumption(cap)
        sm.abort_recovery_resumption(cap)
        cap.invalidate()

        # Re-attempt with invalidated capability
        with pytest.raises(WorkflowAuthorizationError):
            sm.begin_recovery_resumption(cap)

    def test_42_capability_cannot_be_serialized(self) -> None:
        """Verify capability cannot be serialized via pickle to prevent replay or leakage."""
        import pickle
        from holomed.workflow._transaction import _create_recovery_capability

        cap = _create_recovery_capability(
            service_instance_id=1,
            session_id="session-ser",
            workflow_id="wf-ser",
            recovery_revision=1,
            sequence_number=1,
        )

        with pytest.raises(TypeError) as excinfo:
            pickle.dumps(cap)
        assert "cannot be serialized" in str(excinfo.value)

    def test_43_direct_capability_constructor_fails_without_internal_key(self) -> None:
        """Verify _RecoveryTransactionCapability cannot be constructed directly without internal module key."""
        from holomed.workflow._transaction import _RecoveryTransactionCapability
        from holomed.workflow.exceptions import WorkflowAuthorizationError

        with pytest.raises(WorkflowAuthorizationError) as excinfo:
            _RecoveryTransactionCapability(
                internal_key="unauthorized_caller",
                service_instance_id=1,
                session_id="session-dir",
                workflow_id="wf-dir",
                recovery_revision=1,
                sequence_number=1,
            )
        assert "strictly prohibited" in str(excinfo.value)

    def test_44_capability_cross_session_or_workflow_mismatch_rejected(self) -> None:
        """Verify capability bound to session A is rejected when submitted to session B."""
        from holomed.workflow._transaction import _create_recovery_capability
        from holomed.workflow.exceptions import WorkflowAuthorizationError
        from holomed.workflow.procedure import STANDARD_SURGICAL_GUIDANCE_V1
        from holomed.workflow.state_machine import WorkflowStateMachine

        sm_b = WorkflowStateMachine("wf-b", "session-b", 1, STANDARD_SURGICAL_GUIDANCE_V1)
        sm_b.transition_to(WorkflowPhase.PRE_PROCEDURE_PLANNING, sequence_number=1)
        sm_b.transition_to(WorkflowPhase.RECOVERY_REQUIRED, sequence_number=2)

        # Capability created for session-a
        cap_a = _create_recovery_capability(
            service_instance_id=1,
            session_id="session-a",
            workflow_id="wf-a",
            recovery_revision=1,
            sequence_number=3,
        )

        # Session mismatch
        with pytest.raises(WorkflowAuthorizationError) as excinfo:
            sm_b.begin_recovery_resumption(cap_a)
        assert "does not match workflow session" in str(excinfo.value)

        # Workflow ID mismatch
        cap_mismatch_wf = _create_recovery_capability(
            service_instance_id=1,
            session_id="session-b",
            workflow_id="wf-different",
            recovery_revision=1,
            sequence_number=3,
        )
        with pytest.raises(WorkflowAuthorizationError) as excinfo:
            sm_b.begin_recovery_resumption(cap_mismatch_wf)
        assert "does not match workflow" in str(excinfo.value)


class TestEndToEndRecoveryReentryIntegration:
    """End-to-End integration testing of the complete recovery-to-navigation lifecycle."""

    def test_26_full_e2e_recovery_reentry_to_m19_gateway(
        self,
        runtime_context: RuntimeContext,
        message_dispatcher: MessageDispatcher,
        secret_filter: SecretFilter,
    ) -> None:
        """Verify full loop:
        Navigation -> Drift failure -> RECOVERY_REQUIRED -> M19 Gateway blocked ->
        Spatial Recovery verified -> M18 Resumption Evaluated -> M10 Resumption ->
        Navigation restored -> M19 Gateway re-permits actuation.
        """
        from holomed.execution.models import ExecutionStatus, NavigationExecutionRequest
        from holomed.execution.service import NavigationExecutionService
        from holomed.navigation.models import TrackedInstrumentPose

        session_id = "session-e2e-recovery-01"
        mock_persistence = MagicMock()
        wf_svc = _setup_started_workflow_service(runtime_context, message_dispatcher, secret_filter, mock_persistence)

        # 1. Start and advance to RECOVERY_REQUIRED
        _advance_to_recovery_required(wf_svc, session_id)
        # Session is currently in RECOVERY_REQUIRED

        # Register drift interlock
        drift_interlock = SafetyInterlock(
            interlock_id="e2e-drift-it",
            severity=InterlockSeverity.BLOCKING,
            condition_name="landmark_drift_exceeded",
            status=False,
            reason="Landmark tracking drifted",
            source_service="drift_service",
            epoch_id=1,
            session_id=session_id,
        )
        wf_svc._interlock_engine.register_interlock(drift_interlock)

        # 2. Setup M18 Mock Gate & M19 Navigation Execution Service
        mock_gate = MagicMock()
        # In RECOVERY_REQUIRED, M18 denies TOOL_NAVIGATION
        mock_gate.evaluate.return_value = GateStatusRecord(
            session_id=session_id,
            decision=GateDecision.DENIED_INTERLOCKED,
            severity=GateSeverity.BLOCKING,
            reason_code=GateReasonCode.WORKFLOW_PHASE_BLOCKED,
            action=SafetyGateAction.TOOL_NAVIGATION,
            sequence_number=6,
            subsystem_snapshots=(),
            evaluated_at_utc="2026-09-02T12:00:01.000000+00:00",
        )

        mock_nav = MagicMock()
        mock_rec = MagicMock()
        logger = StructuredLogger("e2e_test_logger", secret_filter=secret_filter)
        disp_exec = MessageDispatcher()
        disp_exec.initialize(runtime_context)
        exec_svc = NavigationExecutionService(
            dispatcher=disp_exec,
            safety_gate_service=mock_gate,
            workflow_service=wf_svc,
            navigation_service=mock_nav,
            recovery_service=mock_rec,
            persistence_service=mock_persistence,
            secret_filter=secret_filter,
            logger=logger,
        )
        exec_svc.initialize(runtime_context)
        disp_exec.start()
        exec_svc.start()

        pose = TrackedInstrumentPose(
            instrument_id="inst-scalpel-01",
            session_id=session_id,
            epoch_id=1,
            sequence_number=6,
            tip_position_mm=(10.0, 20.0, 30.0),
            orientation_quaternion=(0.0, 0.0, 0.0, 1.0),
            coordinate_frame="PATIENT_TRACKER",
            confidence=1.0,
            uncertainty=0.1,
            timestamp_utc="2026-09-02T12:00:01.000000+00:00",
        )
        nav_req = NavigationExecutionRequest(
            session_id=session_id,
            sequence_number=6,
            now_utc="2026-09-02T12:00:01.000000+00:00",
            instrument_id="inst-scalpel-01",
            target_trajectory_id="traj-tumor-01",
            pose=pose,
        )

        # Actuation during RECOVERY_REQUIRED must be rejected by M18 safety gate
        res_blocked = exec_svc.execute_navigation(nav_req)
        assert res_blocked.execution_status == ExecutionStatus.BLOCKED_SAFETY_GATE
        assert res_blocked.gate_decision == GateDecision.DENIED_INTERLOCKED
        assert res_blocked.gate_reason_code == GateReasonCode.WORKFLOW_PHASE_BLOCKED

        # 3. Now simulate Spatial Recovery completion:
        # M18 evaluates WORKFLOW_RESUMPTION -> returns PERMITTED_CLEAR
        mock_gate.evaluate.return_value = GateStatusRecord(
            session_id=session_id,
            decision=GateDecision.PERMITTED_CLEAR,
            severity=GateSeverity.NONE,
            reason_code=GateReasonCode.NONE,
            action=SafetyGateAction.WORKFLOW_RESUMPTION,
            sequence_number=7,
            subsystem_snapshots=(),
            evaluated_at_utc="2026-09-02T12:00:02.000000+00:00",
        )

        resump_req = WorkflowResumptionRequest(
            session_id=session_id,
            sequence_number=7,
            now_utc="2026-09-02T12:00:02.000000+00:00",
            recovery_revision=2,
            cleared_interlock_ids=("e2e-drift-it",),
            reason="Verified landmark re-registration recovery",
        )

        # Authoritative resumption executed
        resump_snapshot = wf_svc.resume_from_recovery(resump_req, mock_gate)
        assert resump_snapshot.current_phase == WorkflowPhase.NAVIGATION
        assert wf_svc._interlock_engine.has_blocking_interlock() is False

        # 4. Now M18 evaluates TOOL_NAVIGATION as PERMITTED_CLEAR
        mock_gate.evaluate.return_value = GateStatusRecord(
            session_id=session_id,
            decision=GateDecision.PERMITTED_CLEAR,
            severity=GateSeverity.NONE,
            reason_code=GateReasonCode.NONE,
            action=SafetyGateAction.TOOL_NAVIGATION,
            sequence_number=8,
            subsystem_snapshots=(),
            evaluated_at_utc="2026-09-02T12:00:03.000000+00:00",
        )
        deviation_mock = MagicMock()
        mock_nav.evaluate.return_value = deviation_mock

        post_rec_pose = TrackedInstrumentPose(
            instrument_id="inst-scalpel-01",
            session_id=session_id,
            epoch_id=1,
            sequence_number=8,
            tip_position_mm=(10.0, 20.0, 30.0),
            orientation_quaternion=(0.0, 0.0, 0.0, 1.0),
            coordinate_frame="PATIENT_TRACKER",
            confidence=1.0,
            uncertainty=0.1,
            timestamp_utc="2026-09-02T12:00:03.000000+00:00",
        )
        post_rec_nav_req = NavigationExecutionRequest(
            session_id=session_id,
            sequence_number=8,
            now_utc="2026-09-02T12:00:03.000000+00:00",
            instrument_id="inst-scalpel-01",
            target_trajectory_id="traj-tumor-01",
            pose=post_rec_pose,
        )
        act_result = exec_svc.execute_navigation(post_rec_nav_req)

        # Invariants verified:
        # Actuation succeeded post-resumption
        assert act_result.execution_status == ExecutionStatus.EXECUTED_CLEAR
        assert act_result.session_id == session_id
        assert act_result.action == SafetyGateAction.TOOL_NAVIGATION
        assert act_result.deviation_record is deviation_mock

        exec_svc.stop()
        disp_exec.stop()
        wf_svc.stop()
