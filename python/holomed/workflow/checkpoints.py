# -*- coding: utf-8 -*-
"""Anatomical Checkpoint Validator for M10."""

from __future__ import annotations

from typing import Mapping, Optional

from holomed.workflow.exceptions import WorkflowCapacityError
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

    @property
    def checkpoint_count(self) -> int:
        return len(self._checkpoints)

    def register_checkpoint(self, checkpoint: AnatomicalCheckpoint) -> None:
        """Register an anatomical checkpoint."""
        if len(self._checkpoints) >= MAX_REGISTERED_CHECKPOINTS:
            raise WorkflowCapacityError(
                f"Registered checkpoint limit ({MAX_REGISTERED_CHECKPOINTS}) exceeded"
            )
        self._checkpoints[checkpoint.checkpoint_id] = checkpoint

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

    def clear(self) -> None:
        self._checkpoints.clear()
