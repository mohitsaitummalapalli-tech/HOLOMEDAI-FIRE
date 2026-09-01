# -*- coding: utf-8 -*-
"""Unit Tests for WorkflowService Lifecycle, Dispatcher Routes, and Gating."""

from __future__ import annotations

import pytest

from holomed.core.dispatcher import MessageDispatcher
from holomed.protocol.builders import create_command, create_query
from holomed.runtime.context import RuntimeContext
from holomed.runtime.logging import SecretFilter
from holomed.runtime.models import HealthStatus
from holomed.runtime.service import ServiceState
from holomed.workflow.exceptions import (
    WorkflowConfirmationRequiredError,
    WorkflowLifecycleError,
    WorkflowSafetyInterlockError,
)
from holomed.workflow.models import (
    ConfirmationResponse,
    InterlockSeverity,
    SafetyInterlock,
    WorkflowPhase,
)
from holomed.workflow.service import WorkflowService


def test_workflow_service_lifecycle_and_resources(
    runtime_context: RuntimeContext,
    message_dispatcher: MessageDispatcher,
    secret_filter: SecretFilter,
) -> None:
    """Verify WorkflowService acquires and releases exactly 4 structural handles."""
    srv = WorkflowService(dispatcher=message_dispatcher, secret_filter=secret_filter)
    assert srv.state == ServiceState.UNINITIALIZED

    srv.initialize(runtime_context)
    assert srv.state == ServiceState.INITIALIZED
    assert len(srv.resources.outstanding_handles) == 4
    expected_handles = {
        "workflow.state",
        "workflow.interlocks",
        "workflow.confirmations",
        "workflow.metrics",
    }
    assert {h.resource_id for h in srv.resources.outstanding_handles} == expected_handles

    message_dispatcher.start()
    srv.start()
    assert srv.state == ServiceState.STARTED
    assert srv.health().status == HealthStatus.HEALTHY

    srv.stop()
    assert srv.state == ServiceState.STOPPED
    assert len(srv.resources.outstanding_handles) == 0


def test_start_workflow_and_phase_transition(
    runtime_context: RuntimeContext,
    message_dispatcher: MessageDispatcher,
    secret_filter: SecretFilter,
) -> None:
    """Verify workflow start and sequential progression."""
    srv = WorkflowService(dispatcher=message_dispatcher, secret_filter=secret_filter)
    srv.initialize(runtime_context)
    message_dispatcher.start()
    srv.start()

    sess_id = "sess_wf_01"
    snap0 = srv.start_workflow(sess_id)
    assert snap0.current_phase == WorkflowPhase.PATIENT_CONTEXT

    # Transition to PLANNING
    snap1 = srv.transition_phase(sess_id, WorkflowPhase.PRE_PROCEDURE_PLANNING, sequence_number=1)
    assert snap1.current_phase == WorkflowPhase.PRE_PROCEDURE_PLANNING

    # Transition to REGISTRATION
    snap2 = srv.transition_phase(sess_id, WorkflowPhase.REGISTRATION, sequence_number=2)
    assert snap2.current_phase == WorkflowPhase.REGISTRATION

    # Transition to TIMEOUT
    snap3 = srv.transition_phase(sess_id, WorkflowPhase.SAFETY_TIMEOUT, sequence_number=3)
    assert snap3.current_phase == WorkflowPhase.SAFETY_TIMEOUT

    # Transition to NAVIGATION without confirmation -> Rejected!
    with pytest.raises(WorkflowConfirmationRequiredError):
        srv.transition_phase(sess_id, WorkflowPhase.NAVIGATION, sequence_number=4)

    srv.stop()


def test_human_confirmation_gate_in_service(
    runtime_context: RuntimeContext,
    message_dispatcher: MessageDispatcher,
    secret_filter: SecretFilter,
) -> None:
    """Verify explicit human confirmation unblocks NAVIGATION transition."""
    srv = WorkflowService(dispatcher=message_dispatcher, secret_filter=secret_filter)
    srv.initialize(runtime_context)
    message_dispatcher.start()
    srv.start()

    sess_id = "sess_gate_01"
    srv.start_workflow(sess_id)
    srv.transition_phase(sess_id, WorkflowPhase.PRE_PROCEDURE_PLANNING, 1)
    srv.transition_phase(sess_id, WorkflowPhase.REGISTRATION, 2)
    srv.transition_phase(sess_id, WorkflowPhase.SAFETY_TIMEOUT, 3)

    # Request confirmation
    req = srv.request_confirmation(sess_id, WorkflowPhase.NAVIGATION, "Checklist verified", 4)

    # Submit approval
    resp = ConfirmationResponse(
        confirmation_id=req.confirmation_id,
        session_id=sess_id,
        epoch_id=1,
        approved=True,
        operator_id="surgeon_smith",
        timestamp_utc="2026-09-01T20:00:00Z",
        sequence_number=5,
    )
    srv.confirm(resp)

    # Now transition to NAVIGATION succeeds!
    snap4 = srv.transition_phase(sess_id, WorkflowPhase.NAVIGATION, sequence_number=6)
    assert snap4.current_phase == WorkflowPhase.NAVIGATION

    srv.stop()


def test_blocking_and_critical_interlocks_in_service(
    runtime_context: RuntimeContext,
    message_dispatcher: MessageDispatcher,
    secret_filter: SecretFilter,
) -> None:
    """Verify blocking and critical interlocks halt workflow transitions."""
    srv = WorkflowService(dispatcher=message_dispatcher, secret_filter=secret_filter)
    srv.initialize(runtime_context)
    message_dispatcher.start()
    srv.start()

    sess_id = "sess_it_01"
    srv.start_workflow(sess_id)

    # 1. Tripped BLOCKING interlock
    blk = SafetyInterlock("it_b1", InterlockSeverity.BLOCKING, "PATIENT_MISMATCH", False, "ID error", "wf", 1, sess_id)
    srv.interlock_engine.register_interlock(blk)

    with pytest.raises(WorkflowSafetyInterlockError):
        srv.transition_phase(sess_id, WorkflowPhase.PRE_PROCEDURE_PLANNING, sequence_number=1)

    # Clear blocking, register CRITICAL
    srv.interlock_engine.clear()
    crt = SafetyInterlock("it_c1", InterlockSeverity.CRITICAL, "HARDWARE_FAULT", False, "Sensor dead", "wf", 1, sess_id)
    srv.interlock_engine.register_interlock(crt)

    with pytest.raises(WorkflowSafetyInterlockError):
        srv.transition_phase(sess_id, WorkflowPhase.PRE_PROCEDURE_PLANNING, sequence_number=2)

    # Verify workflow was forced into ABORTED
    snap = srv.get_workflow_state(sess_id)
    assert snap.current_phase == WorkflowPhase.ABORTED
    assert snap.is_terminal is True

    srv.stop()


def test_dispatcher_routes(
    runtime_context: RuntimeContext,
    message_dispatcher: MessageDispatcher,
    secret_filter: SecretFilter,
) -> None:
    """Verify dispatcher query and command handlers route cleanly."""
    srv = WorkflowService(dispatcher=message_dispatcher, secret_filter=secret_filter)
    srv.initialize(runtime_context)
    message_dispatcher.start()
    srv.start()

    # 1. Start workflow via command
    cmd_start = create_command("workflow.start", "client", payload={"session_id": "sess_disp_wf"})
    resp_start = message_dispatcher.dispatch(cmd_start)
    assert resp_start.message_type.value == "RESPONSE"
    assert resp_start.payload["current_phase"] == "PATIENT_CONTEXT"

    # 2. Status query
    q_status = create_query("workflow.status", "client", payload={"session_id": "sess_disp_wf"})
    resp_status = message_dispatcher.dispatch(q_status)
    assert resp_status.message_type.value == "RESPONSE"
    assert resp_status.payload["current_phase"] == "PATIENT_CONTEXT"

    # 3. Transition command
    cmd_tr = create_command(
        "workflow.transition",
        "client",
        payload={
            "session_id": "sess_disp_wf",
            "target_phase": "PRE_PROCEDURE_PLANNING",
            "sequence_number": 1,
        },
    )
    resp_tr = message_dispatcher.dispatch(cmd_tr)
    assert resp_tr.message_type.value == "RESPONSE"
    assert resp_tr.payload["current_phase"] == "PRE_PROCEDURE_PLANNING"

    # 4. Abort command
    cmd_ab = create_command(
        "workflow.abort",
        "client",
        payload={"session_id": "sess_disp_wf", "sequence_number": 2, "reason": "Test abort"},
    )
    resp_ab = message_dispatcher.dispatch(cmd_ab)
    assert resp_ab.message_type.value == "RESPONSE"
    assert resp_ab.payload["current_phase"] == "ABORTED"

    srv.stop()
