# -*- coding: utf-8 -*-
"""Unit Tests for DurableSessionStore and Session Monotonicity."""

from __future__ import annotations

from pathlib import Path
import pytest

from holomed.persistence.exceptions import (
    PersistenceCapacityError,
    PersistenceLifecycleError,
    PersistenceSequenceError,
)
from holomed.persistence.models import MAX_DURABLE_SESSIONS
from holomed.persistence.sessions import DurableSessionStore
from holomed.platform.models import CycleStatus, CycleSummary, SessionStatus


@pytest.fixture
def sample_cycle_summary() -> CycleSummary:
    return CycleSummary(
        cycle_id="c_01",
        epoch_id=1,
        session_id="sess_01",
        sequence_number=0,
        status=CycleStatus.COMPLETED,
        execution_time_ms=1.5,
        phases_completed=("PERCEPTION", "REASONING", "PRESENTATION"),
        fused_entity_count=2,
        tool_invocations_count=0,
        xr_node_count=3,
        confidence=1.0,
        uncertainty_metric=0.0,
    )


def test_durable_session_lifecycle(temp_storage_root: Path, sample_cycle_summary: CycleSummary) -> None:
    """Verify session creation, cycle recording, and closing."""
    store = DurableSessionStore(temp_storage_root, epoch_id=1)
    rec = store.start_session("sess_01", epoch_id=1)
    assert rec.status == SessionStatus.ACTIVE
    assert rec.total_cycles == 0

    # Record cycle 0
    updated = store.record_cycle("sess_01", sequence_number=0, summary=sample_cycle_summary)
    assert updated.total_cycles == 1
    assert updated.last_sequence == 0

    # Close session
    closed = store.close_session("sess_01")
    assert closed.status == SessionStatus.STOPPED
    assert closed.closed_timestamp_utc is not None


def test_sequence_monotonicity_enforcement(
    temp_storage_root: Path, sample_cycle_summary: CycleSummary
) -> None:
    """Verify duplicate and decreasing sequences are rejected."""
    store = DurableSessionStore(temp_storage_root, epoch_id=1)
    store.start_session("sess_mono", epoch_id=1)

    store.record_cycle("sess_mono", sequence_number=0, summary=sample_cycle_summary)

    # Duplicate sequence 0
    with pytest.raises(PersistenceSequenceError):
        store.record_cycle("sess_mono", sequence_number=0, summary=sample_cycle_summary)


def test_closed_session_rejects_cycle(
    temp_storage_root: Path, sample_cycle_summary: CycleSummary
) -> None:
    """Verify closed session rejects new cycle records."""
    store = DurableSessionStore(temp_storage_root, epoch_id=1)
    store.start_session("sess_closed", epoch_id=1)
    store.close_session("sess_closed")

    with pytest.raises(PersistenceLifecycleError):
        store.record_cycle("sess_closed", sequence_number=0, summary=sample_cycle_summary)


def test_capacity_limits(temp_storage_root: Path) -> None:
    """Verify MAX_DURABLE_SESSIONS = 16 capacity limit."""
    store = DurableSessionStore(temp_storage_root, epoch_id=1)
    for i in range(MAX_DURABLE_SESSIONS):
        store.start_session(f"sess_cap_{i}", epoch_id=1)

    assert store.session_count == 16

    with pytest.raises(PersistenceCapacityError):
        store.start_session("sess_overflow", epoch_id=1)


def test_restore_session_from_disk(
    temp_storage_root: Path, sample_cycle_summary: CycleSummary
) -> None:
    """Verify session metadata can be recovered from an existing on-disk journal."""
    store_1 = DurableSessionStore(temp_storage_root, epoch_id=1)
    store_1.start_session("sess_restore_01", epoch_id=1)
    store_1.record_cycle("sess_restore_01", sequence_number=0, summary=sample_cycle_summary)
    store_1.close_session("sess_restore_01")

    # Reconstruct in a new store instance
    store_2 = DurableSessionStore(temp_storage_root, epoch_id=1)
    assert not store_2.has_session("sess_restore_01")

    recovered = store_2.restore_session_from_disk("sess_restore_01")
    assert recovered.session_id == "sess_restore_01"
    assert recovered.total_cycles == 1
    assert recovered.last_sequence == 0
    assert recovered.status == SessionStatus.STOPPED


def test_idempotent_session_close(temp_storage_root: Path) -> None:
    """Verify closing an already stopped session is a safe no-op."""
    store = DurableSessionStore(temp_storage_root, epoch_id=1)
    store.start_session("sess_idem", epoch_id=1)
    c1 = store.close_session("sess_idem")
    c2 = store.close_session("sess_idem")
    assert c1 == c2
