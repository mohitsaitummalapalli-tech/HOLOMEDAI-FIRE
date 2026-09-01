# -*- coding: utf-8 -*-
"""Unit Tests for WorkflowStateMachine Transitions and Monotonicity."""

from __future__ import annotations

import pytest

from holomed.workflow.exceptions import (
    WorkflowCapacityError,
    WorkflowSequenceError,
    WorkflowTransitionError,
)
from holomed.workflow.models import (
    MAX_TRANSITIONS_PER_WORKFLOW,
    WorkflowPhase,
)
from holomed.workflow.procedure import STANDARD_SURGICAL_GUIDANCE_V1
from holomed.workflow.state_machine import WorkflowStateMachine


def test_valid_phase_progression() -> None:
    """Verify standard legal phase transitions advance cleanly."""
    sm = WorkflowStateMachine(
        workflow_id="wf_01",
        session_id="sess_01",
        epoch_id=1,
        procedure=STANDARD_SURGICAL_GUIDANCE_V1,
    )
    assert sm.current_phase == WorkflowPhase.PATIENT_CONTEXT

    # Transition to PLANNING
    snap1 = sm.transition_to(WorkflowPhase.PRE_PROCEDURE_PLANNING, sequence_number=1)
    assert snap1.current_phase == WorkflowPhase.PRE_PROCEDURE_PLANNING
    assert snap1.previous_phase == WorkflowPhase.PATIENT_CONTEXT
    assert snap1.transition_count == 1

    # Transition to REGISTRATION
    snap2 = sm.transition_to(WorkflowPhase.REGISTRATION, sequence_number=2)
    assert snap2.current_phase == WorkflowPhase.REGISTRATION

    # Transition to TIMEOUT
    snap3 = sm.transition_to(WorkflowPhase.SAFETY_TIMEOUT, sequence_number=3)
    assert snap3.current_phase == WorkflowPhase.SAFETY_TIMEOUT


def test_illegal_phase_transition_rejected() -> None:
    """Verify skipping phases raises WorkflowTransitionError."""
    sm = WorkflowStateMachine(
        workflow_id="wf_illegal",
        session_id="sess_01",
        epoch_id=1,
        procedure=STANDARD_SURGICAL_GUIDANCE_V1,
    )
    # Skipping from PATIENT_CONTEXT directly to INTERVENTION
    with pytest.raises(WorkflowTransitionError):
        sm.transition_to(WorkflowPhase.INTERVENTION, sequence_number=1)


def test_explicit_abort_command_moves_to_aborted() -> None:
    """Verify abort transitions immediately to ABORTED from any non-terminal state."""
    sm = WorkflowStateMachine(
        workflow_id="wf_abort",
        session_id="sess_01",
        epoch_id=1,
        procedure=STANDARD_SURGICAL_GUIDANCE_V1,
    )
    snap = sm.abort(sequence_number=1, reason="Test abort")
    assert snap.current_phase == WorkflowPhase.ABORTED
    assert snap.is_terminal is True


def test_aborted_state_is_terminal() -> None:
    """Verify attempting to transition out of ABORTED raises WorkflowTransitionError."""
    sm = WorkflowStateMachine(
        workflow_id="wf_term",
        session_id="sess_01",
        epoch_id=1,
        procedure=STANDARD_SURGICAL_GUIDANCE_V1,
    )
    sm.abort(sequence_number=1)
    with pytest.raises(WorkflowTransitionError):
        sm.transition_to(WorkflowPhase.PATIENT_CONTEXT, sequence_number=2)


def test_sequence_monotonicity_enforcement() -> None:
    """Verify non-increasing sequence numbers are rejected."""
    sm = WorkflowStateMachine(
        workflow_id="wf_mono",
        session_id="sess_01",
        epoch_id=1,
        procedure=STANDARD_SURGICAL_GUIDANCE_V1,
    )
    sm.transition_to(WorkflowPhase.PRE_PROCEDURE_PLANNING, sequence_number=5)
    with pytest.raises(WorkflowSequenceError):
        sm.transition_to(WorkflowPhase.REGISTRATION, sequence_number=5)

    with pytest.raises(WorkflowSequenceError):
        sm.transition_to(WorkflowPhase.REGISTRATION, sequence_number=4)
