# -*- coding: utf-8 -*-
"""Deterministic Workflow State Recovery for M10."""

from __future__ import annotations

from typing import Optional

from holomed.persistence.models import ReplayStatus
from holomed.persistence.service import PersistenceService
from holomed.workflow.exceptions import WorkflowRecoveryError
from holomed.workflow.models import (
    ProcedureDefinition,
    WorkflowPhase,
    WorkflowStateSnapshot,
)
from holomed.workflow.state_machine import WorkflowStateMachine


class WorkflowRecoveryEngine:
    """Recovers workflow state from persistent M09 session journals."""

    @staticmethod
    def recover_workflow_state(
        session_id: str,
        epoch_id: int,
        procedure: ProcedureDefinition,
        persistence_service: PersistenceService,
    ) -> WorkflowStateSnapshot:
        """Reconstruct workflow state from persistence service journal."""
        # 1. Run deterministic replay through persistence service
        try:
            rep = persistence_service.replay_session(session_id)
        except Exception:
            # Persistence corrupted, unreadable, or failing hash checks -> RECOVERY_REQUIRED
            sm = WorkflowStateMachine(
                workflow_id=f"wf_rec_{session_id}",
                session_id=session_id,
                epoch_id=epoch_id,
                procedure=procedure,
                initial_phase=WorkflowPhase.RECOVERY_REQUIRED,
            )
            return sm.snapshot

        if rep.replay_status == ReplayStatus.CORRUPTED or not rep.hash_chain_valid:
            # Corrupted journal -> RECOVERY_REQUIRED
            sm = WorkflowStateMachine(
                workflow_id=f"wf_rec_{session_id}",
                session_id=session_id,
                epoch_id=epoch_id,
                procedure=procedure,
                initial_phase=WorkflowPhase.RECOVERY_REQUIRED,
            )
            return sm.snapshot

        if rep.replay_status == ReplayStatus.EMPTY or rep.total_entries_verified == 0:
            # Clean new session starting at PATIENT_CONTEXT
            sm = WorkflowStateMachine(
                workflow_id=f"wf_{session_id}",
                session_id=session_id,
                epoch_id=epoch_id,
                procedure=procedure,
                initial_phase=WorkflowPhase.PATIENT_CONTEXT,
            )
            return sm.snapshot

        # Verify durable session metadata
        try:
            durable_sess = persistence_service.get_session(session_id)
        except Exception as e:
            raise WorkflowRecoveryError(f"Failed to fetch durable session record: {e}") from e

        # If session closed, recover as COMPLETION
        if durable_sess.status.value == "STOPPED":
            sm = WorkflowStateMachine(
                workflow_id=f"wf_{session_id}",
                session_id=session_id,
                epoch_id=epoch_id,
                procedure=procedure,
                initial_phase=WorkflowPhase.COMPLETION,
            )
            return sm.snapshot

        # Valid active session
        sm = WorkflowStateMachine(
            workflow_id=f"wf_{session_id}",
            session_id=session_id,
            epoch_id=epoch_id,
            procedure=procedure,
            initial_phase=WorkflowPhase.NAVIGATION,
        )
        return sm.snapshot
