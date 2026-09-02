# -*- coding: utf-8 -*-
"""Deterministic Clinical Workflow State Machine for M10."""

from __future__ import annotations

from typing import Optional

from holomed.workflow._transaction import _RecoveryTransactionCapability
from holomed.workflow.exceptions import (
    WorkflowAuthorizationError,
    WorkflowCapacityError,
    WorkflowSequenceError,
    WorkflowTransitionError,
    WorkflowValidationError,
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
        self._pending_resumption_prior: Optional[
            tuple[WorkflowPhase, Optional[WorkflowPhase], int, int, list[WorkflowPhase]]
        ] = None

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

    @property
    def last_sequence_number(self) -> int:
        """Return the sequence number of the most recent transition."""
        return self._last_sequence

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

    def begin_recovery_resumption(
        self,
        capability: _RecoveryTransactionCapability,
    ) -> None:
        """Stage transition from RECOVERY_REQUIRED to NAVIGATION using internal transaction capability."""
        if not isinstance(capability, _RecoveryTransactionCapability) or not capability.is_active:
            raise WorkflowAuthorizationError("Valid active _RecoveryTransactionCapability required")
        if capability.session_id != self._session_id:
            raise WorkflowAuthorizationError(
                f"Capability session {capability.session_id!r} does not match workflow session {self._session_id!r}"
            )
        if capability.workflow_id != self._workflow_id:
            raise WorkflowAuthorizationError(
                f"Capability workflow {capability.workflow_id!r} does not match workflow {self._workflow_id!r}"
            )
        if self._current_phase != WorkflowPhase.RECOVERY_REQUIRED:
            raise WorkflowTransitionError(
                f"Cannot resume from recovery: current phase is {self._current_phase.name}, expected RECOVERY_REQUIRED"
            )

        sequence_number = capability.sequence_number
        if sequence_number <= self._last_sequence:
            raise WorkflowSequenceError(
                f"Sequence {sequence_number} must exceed last sequence {self._last_sequence}"
            )
        if self._transition_count >= MAX_TRANSITIONS_PER_WORKFLOW:
            raise WorkflowCapacityError(
                f"Workflow transition capacity ({MAX_TRANSITIONS_PER_WORKFLOW}) exceeded"
            )
        if WorkflowPhase.NAVIGATION not in self._procedure.allowed_phases:
            raise WorkflowTransitionError(
                f"Phase NAVIGATION not permitted in procedure {self._procedure.procedure_id}"
            )

        self._pending_resumption_prior = (
            self._current_phase,
            self._previous_phase,
            self._transition_count,
            self._last_sequence,
            list(self._history),
        )
        self._previous_phase = self._current_phase
        self._current_phase = WorkflowPhase.NAVIGATION
        self._transition_count += 1
        self._last_sequence = sequence_number
        self._history.append(WorkflowPhase.NAVIGATION)

    def commit_recovery_resumption(
        self,
        capability: _RecoveryTransactionCapability,
    ) -> WorkflowStateSnapshot:
        """Commit staged recovery resumption and return current snapshot."""
        if not isinstance(capability, _RecoveryTransactionCapability) or not capability.is_active:
            raise WorkflowAuthorizationError("Valid active _RecoveryTransactionCapability required")
        if capability.session_id != self._session_id or capability.workflow_id != self._workflow_id:
            raise WorkflowAuthorizationError("Capability mismatch during commit")
        self._pending_resumption_prior = None
        return self.snapshot

    def abort_recovery_resumption(
        self,
        capability: Optional[_RecoveryTransactionCapability] = None,
    ) -> None:
        """Abort staged recovery resumption, restoring pre-staged state."""
        if self._pending_resumption_prior is not None:
            (
                self._current_phase,
                self._previous_phase,
                self._transition_count,
                self._last_sequence,
                self._history,
            ) = self._pending_resumption_prior
            self._pending_resumption_prior = None
