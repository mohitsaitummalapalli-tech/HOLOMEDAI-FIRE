# -*- coding: utf-8 -*-
"""Unit Tests for ConfirmationManager and Human-in-the-Loop Approval Gating."""

from __future__ import annotations

import pytest

from holomed.workflow.confirmation import ConfirmationManager
from holomed.workflow.exceptions import (
    WorkflowCapacityError,
    WorkflowConfirmationError,
    WorkflowEpochMismatchError,
    WorkflowSessionError,
)
from holomed.workflow.models import (
    MAX_PENDING_CONFIRMATIONS,
    ConfirmationResponse,
    WorkflowPhase,
)


def test_human_confirmation_request_and_approval() -> None:
    """Verify requesting and approving a confirmation registers as phase confirmed."""
    cm = ConfirmationManager(session_id="sess_conf_01", epoch_id=1)
    req = cm.request_confirmation(
        workflow_id="wf_01",
        target_phase=WorkflowPhase.NAVIGATION,
        rationale="Sterile timeout complete",
        sequence_number=1,
    )
    assert cm.pending_count == 1
    assert cm.is_phase_confirmed(WorkflowPhase.NAVIGATION) is False

    resp = ConfirmationResponse(
        confirmation_id=req.confirmation_id,
        session_id="sess_conf_01",
        epoch_id=1,
        approved=True,
        operator_id="surgeon_smith",
        timestamp_utc="2026-09-01T20:00:00Z",
        sequence_number=2,
    )
    cm.resolve_confirmation(resp)
    assert cm.pending_count == 0
    assert cm.is_phase_confirmed(WorkflowPhase.NAVIGATION) is True


def test_human_confirmation_rejection() -> None:
    """Verify operator rejection leaves phase unconfirmed."""
    cm = ConfirmationManager(session_id="sess_conf_02", epoch_id=1)
    req = cm.request_confirmation(
        workflow_id="wf_02",
        target_phase=WorkflowPhase.INTERVENTION,
        rationale="Requesting entry to intervention",
        sequence_number=1,
    )
    resp = ConfirmationResponse(
        confirmation_id=req.confirmation_id,
        session_id="sess_conf_02",
        epoch_id=1,
        approved=False,
        operator_id="surgeon_smith",
        timestamp_utc="2026-09-01T20:00:00Z",
        sequence_number=2,
    )
    cm.resolve_confirmation(resp)
    assert cm.is_phase_confirmed(WorkflowPhase.INTERVENTION) is False


def test_stale_confirmation_response_rejected() -> None:
    """Verify resolving unknown or already resolved confirmation raises WorkflowConfirmationError."""
    cm = ConfirmationManager(session_id="sess_conf_03", epoch_id=1)
    resp = ConfirmationResponse(
        confirmation_id="unknown_cid",
        session_id="sess_conf_03",
        epoch_id=1,
        approved=True,
        operator_id="surgeon",
        timestamp_utc="2026-09-01T20:00:00Z",
        sequence_number=1,
    )
    with pytest.raises(WorkflowConfirmationError):
        cm.resolve_confirmation(resp)


def test_epoch_and_session_mismatch_confirmation_rejected() -> None:
    """Verify epoch and session isolation for confirmation responses."""
    cm = ConfirmationManager(session_id="sess_conf_04", epoch_id=1)
    req = cm.request_confirmation("wf_04", WorkflowPhase.NAVIGATION, "Test", 1)

    # Epoch mismatch
    resp_epoch = ConfirmationResponse(
        confirmation_id=req.confirmation_id,
        session_id="sess_conf_04",
        epoch_id=99,
        approved=True,
        operator_id="op",
        timestamp_utc="2026-09-01T20:00:00Z",
        sequence_number=2,
    )
    with pytest.raises(WorkflowEpochMismatchError):
        cm.resolve_confirmation(resp_epoch)

    # Session mismatch
    resp_sess = ConfirmationResponse(
        confirmation_id=req.confirmation_id,
        session_id="other_session",
        epoch_id=1,
        approved=True,
        operator_id="op",
        timestamp_utc="2026-09-01T20:00:00Z",
        sequence_number=2,
    )
    with pytest.raises(WorkflowSessionError):
        cm.resolve_confirmation(resp_sess)


def test_capacity_limit_max_pending_confirmations() -> None:
    """Verify MAX_PENDING_CONFIRMATIONS = 16 capacity enforcement."""
    cm = ConfirmationManager(session_id="sess_cap", epoch_id=1)
    for i in range(MAX_PENDING_CONFIRMATIONS):
        cm.request_confirmation("wf_cap", WorkflowPhase.NAVIGATION, f"Req {i}", i + 1)

    assert cm.pending_count == 16
    with pytest.raises(WorkflowCapacityError):
        cm.request_confirmation("wf_cap", WorkflowPhase.NAVIGATION, "Overflow", 99)
