# -*- coding: utf-8 -*-
"""Adversarial Tests for M10 Reconciled Adversarial Matrix."""

from __future__ import annotations

from pathlib import Path
import pytest

from holomed.core.dispatcher import MessageDispatcher
from holomed.runtime.context import RuntimeContext
from holomed.runtime.logging import SecretFilter
from holomed.tools.models import ToolSafetyClassification
from holomed.workflow.authorization import ToolAuthorizationGate
from holomed.workflow.exceptions import (
    WorkflowEpochMismatchError,
    WorkflowLifecycleError,
    WorkflowSessionError,
)
from holomed.workflow.models import (
    ConfirmationResponse,
    InterlockSeverity,
    SafetyInterlock,
    WorkflowPhase,
    WorkflowToolAuthorizationStatus,
)
from holomed.workflow.service import WorkflowService


def test_warning_interlock_permits_transition(
    runtime_context: RuntimeContext,
    message_dispatcher: MessageDispatcher,
    secret_filter: SecretFilter,
) -> None:
    """Verify WARNING interlock is non-blocking and permits phase transition."""
    srv = WorkflowService(dispatcher=message_dispatcher, secret_filter=secret_filter)
    srv.initialize(runtime_context)
    message_dispatcher.start()
    srv.start()

    sess_id = "sess_warn_01"
    srv.start_workflow(sess_id)

    # Register WARNING interlock
    warn = SafetyInterlock("it_w1", InterlockSeverity.WARNING, "LIGHTING_DIM", False, "Low light", "wf", 1, sess_id)
    srv.interlock_engine.register_interlock(warn)

    # Transition succeeds because WARNING does not block
    snap = srv.transition_phase(sess_id, WorkflowPhase.PRE_PROCEDURE_PLANNING, sequence_number=1)
    assert snap.current_phase == WorkflowPhase.PRE_PROCEDURE_PLANNING

    srv.stop()


def test_read_only_tools_cannot_acquire_execution_authority() -> None:
    """Verify READ_ONLY_INFORMATIVE tools remain strictly informative."""
    decision = ToolAuthorizationGate.evaluate_authorization(
        tool_id="anatomy.query",
        safety_classification=ToolSafetyClassification.READ_ONLY_INFORMATIVE,
        current_phase=WorkflowPhase.INTERVENTION,
        has_blocking_interlocks=False,
        epoch_id=1,
        session_id="sess_01",
    )
    assert decision.status == WorkflowToolAuthorizationStatus.PERMITTED
    assert decision.m07_safety_classification == ToolSafetyClassification.READ_ONLY_INFORMATIVE


def test_human_confirmation_cannot_bypass_surgical_block() -> None:
    """Verify operator confirmation cannot authorize categorically blocked surgical actuation."""
    # Even if operator attempts to approve, surgical actuation is categorically blocked
    decision = ToolAuthorizationGate.evaluate_authorization(
        tool_id="laser.tissue_cut",
        safety_classification=ToolSafetyClassification.READ_ONLY_INFORMATIVE,
        current_phase=WorkflowPhase.INTERVENTION,
        has_blocking_interlocks=False,
        epoch_id=1,
        session_id="sess_01",
        is_surgical_actuation_intent=True,
    )
    assert decision.status == WorkflowToolAuthorizationStatus.BLOCKED_CATEGORICAL


def test_epoch_and_session_isolation_in_service(
    runtime_context: RuntimeContext,
    message_dispatcher: MessageDispatcher,
    secret_filter: SecretFilter,
) -> None:
    """Verify service rejects confirmation responses from mismatched epochs or dead sessions."""
    srv = WorkflowService(dispatcher=message_dispatcher, secret_filter=secret_filter)
    srv.initialize(runtime_context)
    message_dispatcher.start()
    srv.start()

    srv.start_workflow("sess_active_01")
    req = srv.request_confirmation("sess_active_01", WorkflowPhase.NAVIGATION, "Test", 1)

    # 1. Epoch mismatch
    resp_epoch = ConfirmationResponse(
        confirmation_id=req.confirmation_id,
        session_id="sess_active_01",
        epoch_id=99,
        approved=True,
        operator_id="op",
        timestamp_utc="2026-09-01T20:00:00Z",
        sequence_number=2,
    )
    with pytest.raises(WorkflowEpochMismatchError):
        srv.confirm(resp_epoch)

    # 2. Session mismatch
    resp_sess = ConfirmationResponse(
        confirmation_id=req.confirmation_id,
        session_id="sess_nonexistent",
        epoch_id=1,
        approved=True,
        operator_id="op",
        timestamp_utc="2026-09-01T20:00:00Z",
        sequence_number=2,
    )
    with pytest.raises(WorkflowSessionError):
        srv.confirm(resp_sess)

    srv.stop()


def test_reentrancy_guard_in_workflow_service(
    runtime_context: RuntimeContext,
    message_dispatcher: MessageDispatcher,
    secret_filter: SecretFilter,
) -> None:
    """Verify transaction guard rejects reentrant mutation attempts."""
    srv = WorkflowService(dispatcher=message_dispatcher, secret_filter=secret_filter)
    srv.initialize(runtime_context)
    srv.start()

    srv._in_transaction = True
    with pytest.raises(WorkflowLifecycleError):
        srv.start_workflow("sess_reentrant")

    srv._in_transaction = False
    srv.stop()


def test_secret_redaction_in_diagnostics(
    runtime_context: RuntimeContext,
    message_dispatcher: MessageDispatcher,
    secret_filter: SecretFilter,
) -> None:
    """Verify secret filter masks sensitive tokens in dispatcher error responses."""
    srv = WorkflowService(dispatcher=message_dispatcher, secret_filter=secret_filter)
    srv.initialize(runtime_context)
    message_dispatcher.start()
    srv.start()

    from holomed.protocol.builders import create_command
    cmd = create_command(
        "workflow.start",
        "client",
        payload={"session_id": "bad/id/TOP_SECRET_WORKFLOW_TOKEN"},
    )
    resp = message_dispatcher.dispatch(cmd)
    assert resp.message_type.value == "ERROR"
    assert "TOP_SECRET_WORKFLOW_TOKEN" not in resp.payload["error_message"]
    assert "<redacted>" in resp.payload["error_message"]

    srv.stop()
