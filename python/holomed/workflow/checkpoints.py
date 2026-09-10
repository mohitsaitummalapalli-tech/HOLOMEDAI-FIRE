# -*- coding: utf-8 -*-
"""Anatomical Checkpoint Validator for M10."""

from __future__ import annotations

from typing import Mapping, Optional

from holomed.workflow.exceptions import (
    WorkflowCapacityError,
    WorkflowValidationError,
)
from holomed.workflow.models import (
    MAX_REGISTERED_CHECKPOINTS,
    AnatomicalCheckpoint,
    InterlockSeverity,
    SafetyInterlock,
)


class AnatomicalCheckpointValidator:
    """Validates anatomical checkpoints against cycle perception and simulation metrics."""

    def __init__(self) -> None:
        self._checkpoints: dict[str, AnatomicalCheckpoint] = {}
        self._session_checkpoints: dict[str, set[str]] = {}

    @property
    def checkpoint_count(self) -> int:
        return len(self._checkpoints)

    def register_checkpoint(self, checkpoint: AnatomicalCheckpoint, session_id: str) -> None:
        """Register an anatomical checkpoint with mandatory session ownership (M33)."""
        if not isinstance(session_id, str) or not session_id.strip():
            raise WorkflowValidationError("session_id must be a non-empty string for checkpoint registration")

        if not isinstance(checkpoint, AnatomicalCheckpoint):
            raise WorkflowValidationError("checkpoint must be an AnatomicalCheckpoint instance")

        cid = checkpoint.checkpoint_id
        if cid in self._checkpoints:
            # Check existing owner
            owner_set = self._session_checkpoints.get(session_id, set())
            if cid in owner_set:
                # Same-owner duplicate: update definition in-place without incrementing capacity
                self._checkpoints[cid] = checkpoint
                return
            # Cross-session duplicate: another session already owns this checkpoint ID
            raise WorkflowValidationError(
                f"Checkpoint {cid!r} is already registered under another session"
            )

        if len(self._checkpoints) >= MAX_REGISTERED_CHECKPOINTS:
            raise WorkflowCapacityError(
                f"Registered checkpoint limit ({MAX_REGISTERED_CHECKPOINTS}) exceeded"
            )

        # Atomic dual-store registration
        self._checkpoints[cid] = checkpoint
        if session_id not in self._session_checkpoints:
            self._session_checkpoints[session_id] = set()
        self._session_checkpoints[session_id].add(cid)

    def unregister_checkpoint(self, checkpoint_id: str, session_id: str) -> bool:
        """Unregister a specific checkpoint owned by session_id (for atomic rollback) (M33)."""
        if not isinstance(session_id, str) or not session_id.strip():
            return False
        owner_set = self._session_checkpoints.get(session_id)
        if owner_set is not None and checkpoint_id in owner_set:
            owner_set.discard(checkpoint_id)
            if not owner_set:
                self._session_checkpoints.pop(session_id, None)
            self._checkpoints.pop(checkpoint_id, None)
            return True
        return False

    def evaluate_checkpoint(
        self,
        checkpoint_id: str,
        measured_confidence: float,
        measured_uncertainty: float,
        measured_deviation_mm: float,
        epoch_id: int,
        session_id: str,
    ) -> SafetyInterlock:
        """Evaluate a checkpoint against observation metrics and return a SafetyInterlock."""
        # 1. Require valid session context
        if not isinstance(session_id, str) or not session_id.strip():
            return SafetyInterlock(
                interlock_id=f"chk_missing_{checkpoint_id}",
                severity=InterlockSeverity.BLOCKING,
                condition_name="CHECKPOINT_MISSING",
                status=False,
                reason=f"Invalid session context for checkpoint {checkpoint_id}",
                source_service="workflow_service",
                epoch_id=epoch_id,
                session_id=session_id if isinstance(session_id, str) else "unknown",
            )

        # 2. Verify ownership in session index (Do NOT mutate _session_checkpoints)
        session_checkpoints = self._session_checkpoints.get(session_id)
        if session_checkpoints is None or checkpoint_id not in session_checkpoints:
            return SafetyInterlock(
                interlock_id=f"chk_missing_{checkpoint_id}",
                severity=InterlockSeverity.BLOCKING,
                condition_name="CHECKPOINT_MISSING",
                status=False,
                reason=f"Checkpoint {checkpoint_id} not registered for session {session_id}",
                source_service="workflow_service",
                epoch_id=epoch_id,
                session_id=session_id,
            )

        # 3. Locate checkpoint in canonical store
        cp = self._checkpoints.get(checkpoint_id)
        if cp is None:
            return SafetyInterlock(
                interlock_id=f"chk_missing_{checkpoint_id}",
                severity=InterlockSeverity.BLOCKING,
                condition_name="CHECKPOINT_MISSING",
                status=False,
                reason=f"Checkpoint {checkpoint_id} not registered",
                source_service="workflow_service",
                epoch_id=epoch_id,
                session_id=session_id,
            )

        # Check confidence
        if measured_confidence < cp.min_confidence:
            return SafetyInterlock(
                interlock_id=f"chk_conf_{checkpoint_id}",
                severity=InterlockSeverity.BLOCKING,
                condition_name="INSUFFICIENT_CONFIDENCE",
                status=False,
                reason=f"Confidence {measured_confidence:.2f} below threshold {cp.min_confidence:.2f}",
                source_service="workflow_service",
                epoch_id=epoch_id,
                session_id=session_id,
            )

        # Check uncertainty
        if measured_uncertainty > cp.max_uncertainty:
            return SafetyInterlock(
                interlock_id=f"chk_unc_{checkpoint_id}",
                severity=InterlockSeverity.BLOCKING,
                condition_name="EXCESSIVE_UNCERTAINTY",
                status=False,
                reason=f"Uncertainty {measured_uncertainty:.2f} exceeds threshold {cp.max_uncertainty:.2f}",
                source_service="workflow_service",
                epoch_id=epoch_id,
                session_id=session_id,
            )

        # Check tolerance
        if measured_deviation_mm > cp.max_tolerance_mm:
            return SafetyInterlock(
                interlock_id=f"chk_tol_{checkpoint_id}",
                severity=InterlockSeverity.BLOCKING,
                condition_name="TOLERANCE_EXCEEDED",
                status=False,
                reason=f"Spatial deviation {measured_deviation_mm:.1f}mm exceeds tolerance {cp.max_tolerance_mm:.1f}mm",
                source_service="workflow_service",
                epoch_id=epoch_id,
                session_id=session_id,
            )

        return SafetyInterlock(
            interlock_id=f"chk_ok_{checkpoint_id}",
            severity=InterlockSeverity.INFO,
            condition_name="CHECKPOINT_PASSED",
            status=True,
            reason=f"Checkpoint {checkpoint_id} verified within tolerance",
            source_service="workflow_service",
            epoch_id=epoch_id,
            session_id=session_id,
        )

    def evict_session(self, session_id: str) -> bool:
        """Evict session-scoped checkpoints, releasing capacity (M27, M33)."""
        if not isinstance(session_id, str) or not session_id.strip():
            return False
        chk_ids = self._session_checkpoints.pop(session_id, None)
        if not chk_ids:
            return False
        evicted = False
        for cid in chk_ids:
            if cid in self._checkpoints:
                del self._checkpoints[cid]
                evicted = True
        return evicted

    def clear(self) -> None:
        self._checkpoints.clear()
        self._session_checkpoints.clear()
