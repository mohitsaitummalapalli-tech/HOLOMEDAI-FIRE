# -*- coding: utf-8 -*-
"""Deterministic Clinical Workflow State Machine for M10."""

from __future__ import annotations

from typing import Optional

from holomed.workflow.exceptions import (
    WorkflowCapacityError,
    WorkflowSequenceError,
    WorkflowTransitionError,
)
from holomed.workflow.models import (
    MAX_TRANSITIONS_PER_WORKFLOW,
    ProcedureDefinition,
    WorkflowPhase,
    WorkflowStateSnapshot,
)

# Authoritative Legal Transition Graph
LEGAL_TRANSITIONS: dict[WorkflowPhase, tuple[WorkflowPhase, ...]] = {
    WorkflowPhase.PATIENT_CONTEXT: (
        WorkflowPhase.PRE_PROCEDURE_PLANNING,
        WorkflowPhase.ABORTED,
        WorkflowPhase.RECOVERY_REQUIRED,
    ),
    WorkflowPhase.PRE_PROCEDURE_PLANNING: (
        WorkflowPhase.REGISTRATION,
        WorkflowPhase.ABORTED,
        WorkflowPhase.RECOVERY_REQUIRED,
    ),
    WorkflowPhase.REGISTRATION: (
        WorkflowPhase.SAFETY_TIMEOUT,
        WorkflowPhase.ABORTED,
        WorkflowPhase.RECOVERY_REQUIRED,
    ),
    WorkflowPhase.SAFETY_TIMEOUT: (
        WorkflowPhase.NAVIGATION,
        WorkflowPhase.ABORTED,
        WorkflowPhase.RECOVERY_REQUIRED,
    ),
    WorkflowPhase.NAVIGATION: (
        WorkflowPhase.INTERVENTION,
        WorkflowPhase.VERIFICATION,
        WorkflowPhase.ABORTED,
        WorkflowPhase.RECOVERY_REQUIRED,
    ),
    WorkflowPhase.INTERVENTION: (
        WorkflowPhase.VERIFICATION,
        WorkflowPhase.NAVIGATION,
        WorkflowPhase.ABORTED,
        WorkflowPhase.RECOVERY_REQUIRED,
    ),
    WorkflowPhase.VERIFICATION: (
        WorkflowPhase.COMPLETION,
        WorkflowPhase.INTERVENTION,
        WorkflowPhase.ABORTED,
        WorkflowPhase.RECOVERY_REQUIRED,
    ),
    WorkflowPhase.COMPLETION: (),  # Terminal
    WorkflowPhase.ABORTED: (),     # Terminal
    WorkflowPhase.RECOVERY_REQUIRED: (
        WorkflowPhase.ABORTED,
    ),
}

TERMINAL_PHASES: frozenset[WorkflowPhase] = frozenset([
    WorkflowPhase.COMPLETION,
    WorkflowPhase.ABORTED,
])


class WorkflowStateMachine:
    """Manages explicit deterministic clinical workflow state transitions."""

    def __init__(
        self,
        workflow_id: str,
        session_id: str,
        epoch_id: int,
        procedure: ProcedureDefinition,
        initial_phase: WorkflowPhase = WorkflowPhase.PATIENT_CONTEXT,
    ) -> None:
        self._workflow_id = workflow_id
        self._session_id = session_id
        self._epoch_id = epoch_id
        self._procedure = procedure
        self._current_phase = initial_phase
        self._previous_phase: Optional[WorkflowPhase] = None
        self._transition_count: int = 0
        self._last_sequence: int = 0
        self._history: list[WorkflowPhase] = [initial_phase]

    @property
    def workflow_id(self) -> str:
        return self._workflow_id

    @property
    def session_id(self) -> str:
        return self._session_id

    @property
    def epoch_id(self) -> int:
        return self._epoch_id

    @property
    def current_phase(self) -> WorkflowPhase:
        return self._current_phase

    @property
    def previous_phase(self) -> Optional[WorkflowPhase]:
        return self._previous_phase

    @property
    def transition_count(self) -> int:
        return self._transition_count

    @property
    def is_terminal(self) -> bool:
        return self._current_phase in TERMINAL_PHASES

    @property
    def snapshot(self) -> WorkflowStateSnapshot:
        return WorkflowStateSnapshot(
            workflow_id=self._workflow_id,
            session_id=self._session_id,
            epoch_id=self._epoch_id,
            procedure_id=self._procedure.procedure_id,
            current_phase=self._current_phase,
            previous_phase=self._previous_phase,
            transition_count=self._transition_count,
            sequence_number=self._last_sequence,
            is_terminal=self.is_terminal,
        )

    def transition_to(
        self,
        target_phase: WorkflowPhase,
        sequence_number: int,
    ) -> WorkflowStateSnapshot:
        """Attempt to advance state machine to target_phase."""
        if self.is_terminal:
            raise WorkflowTransitionError(
                f"Cannot transition from terminal phase {self._current_phase.name}"
            )

        if sequence_number <= self._last_sequence:
            raise WorkflowSequenceError(
                f"Sequence {sequence_number} must exceed last sequence {self._last_sequence}"
            )

        if self._transition_count >= MAX_TRANSITIONS_PER_WORKFLOW:
            raise WorkflowCapacityError(
                f"Workflow transition capacity ({MAX_TRANSITIONS_PER_WORKFLOW}) exceeded"
            )

        legal_exits = LEGAL_TRANSITIONS.get(self._current_phase, ())
        if target_phase not in legal_exits:
            raise WorkflowTransitionError(
                f"Illegal transition from {self._current_phase.name} to {target_phase.name}"
            )

        if target_phase not in self._procedure.allowed_phases and target_phase not in (
            WorkflowPhase.ABORTED,
            WorkflowPhase.RECOVERY_REQUIRED,
        ):
            raise WorkflowTransitionError(
                f"Phase {target_phase.name} not permitted in procedure {self._procedure.procedure_id}"
            )

        self._previous_phase = self._current_phase
        self._current_phase = target_phase
        self._transition_count += 1
        self._last_sequence = sequence_number
        self._history.append(target_phase)

        return self.snapshot

    def abort(self, sequence_number: int, reason: str = "") -> WorkflowStateSnapshot:
        """Immediately and safely transition to ABORTED from any non-terminal state."""
        return self.transition_to(WorkflowPhase.ABORTED, sequence_number)
