# -*- coding: utf-8 -*-
"""Unit Tests for WorkflowRecoveryEngine and Persistence Integration."""

from __future__ import annotations

from pathlib import Path
import pytest

from holomed.persistence.service import PersistenceService
from holomed.platform.models import CycleStatus, CycleSummary
from holomed.runtime.context import RuntimeContext
from holomed.runtime.logging import SecretFilter
from holomed.workflow.models import WorkflowPhase
from holomed.workflow.procedure import STANDARD_SURGICAL_GUIDANCE_V1
from holomed.workflow.recovery import WorkflowRecoveryEngine


def test_workflow_recovery_from_persistence(
    temp_storage_root: Path,
    runtime_context: RuntimeContext,
    secret_filter: SecretFilter,
) -> None:
    """Verify recovery engine reconstructs workflow state from persistence journal."""
    pers = PersistenceService(
        storage_root=temp_storage_root,
        secret_filter=secret_filter,
    )
    pers.initialize(runtime_context)
    pers.start()

    sess_id = "sess_rec_01"
    pers.start_session(sess_id)
    summary = CycleSummary(
        cycle_id="c_01",
        epoch_id=1,
        session_id=sess_id,
        sequence_number=0,
        status=CycleStatus.COMPLETED,
        execution_time_ms=1.0,
        phases_completed=("PERCEPTION",),
        fused_entity_count=1,
        tool_invocations_count=0,
        xr_node_count=0,
        confidence=1.0,
        uncertainty_metric=0.0,
    )
    pers.record_cycle(sess_id, sequence_number=0, summary=summary)

    # Recover state
    snap = WorkflowRecoveryEngine.recover_workflow_state(
        session_id=sess_id,
        epoch_id=1,
        procedure=STANDARD_SURGICAL_GUIDANCE_V1,
        persistence_service=pers,
    )
    assert snap.session_id == sess_id
    assert snap.current_phase == WorkflowPhase.NAVIGATION

    pers.stop()


def test_workflow_recovery_corrupted_journal_enters_recovery_required(
    temp_storage_root: Path,
    runtime_context: RuntimeContext,
    secret_filter: SecretFilter,
) -> None:
    """Verify corrupted journal causes workflow recovery to enter RECOVERY_REQUIRED."""
    pers = PersistenceService(
        storage_root=temp_storage_root,
        secret_filter=secret_filter,
    )
    pers.initialize(runtime_context)
    pers.start()

    sess_id = "sess_rec_corrupt"
    pers.start_session(sess_id)

    # Record two cycles so we have multiple lines
    s0 = CycleSummary("c0", 1, sess_id, 0, CycleStatus.COMPLETED, 1.0, ("P",), 1, 0, 0, 1.0, 0.0)
    s1 = CycleSummary("c1", 1, sess_id, 1, CycleStatus.COMPLETED, 1.0, ("P",), 1, 0, 0, 1.0, 0.0)
    pers.record_cycle(sess_id, 0, s0)
    pers.record_cycle(sess_id, 1, s1)

    # Corrupt middle line on disk
    journal_path = temp_storage_root / f"{sess_id}.jsonl"
    with open(journal_path, "rb") as f:
        content = f.read()

    lines = content.split(b"\n")
    lines[1] = b"CORRUPTED_MIDDLE_DATA"
    with open(journal_path, "wb") as f:
        f.write(b"\n".join(lines))

    snap = WorkflowRecoveryEngine.recover_workflow_state(
        session_id=sess_id,
        epoch_id=1,
        procedure=STANDARD_SURGICAL_GUIDANCE_V1,
        persistence_service=pers,
    )
    assert snap.current_phase == WorkflowPhase.RECOVERY_REQUIRED

    pers.stop()
