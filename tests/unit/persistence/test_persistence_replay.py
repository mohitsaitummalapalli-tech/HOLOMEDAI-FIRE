# -*- coding: utf-8 -*-
"""Unit Tests for ReplayEngine, Provenance Preservation, and Divergence Detection."""

from __future__ import annotations

import json
from pathlib import Path
import pytest

from holomed.persistence.journal import JournalWriter
from holomed.persistence.models import (
    JournalEntryType,
    ReplayStatus,
)
from holomed.persistence.replay import ReplayEngine


def test_deterministic_replay_clean(temp_storage_root: Path) -> None:
    """Verify clean journal replays with VERIFIED status and valid hash chain."""
    writer = JournalWriter(temp_storage_root, "sess_replay_clean", epoch_id=1)
    writer.initialize_storage()

    writer.append_entry(JournalEntryType.SESSION_STARTED, -1, "2026-09-01T20:00:00Z", {"action": "START"})
    writer.append_entry(
        JournalEntryType.CYCLE_COMPLETED,
        0,
        "2026-09-01T20:00:01Z",
        {"status": "COMPLETED", "is_simulated": True, "confidence": 1.0, "uncertainty_metric": 0.0},
    )
    writer.append_entry(
        JournalEntryType.CYCLE_COMPLETED,
        1,
        "2026-09-01T20:00:02Z",
        {"status": "COMPLETED", "is_simulated": True, "confidence": 0.95, "uncertainty_metric": 0.05},
    )

    engine = ReplayEngine(temp_storage_root)
    rep = engine.verify_session_replay("sess_replay_clean")

    assert rep.replay_status == ReplayStatus.VERIFIED
    assert rep.hash_chain_valid is True
    assert rep.total_entries_verified == 3
    assert len(rep.state_divergences) == 0


def test_replay_detects_sequence_gap_divergence(temp_storage_root: Path) -> None:
    """Verify replay detects sequence gaps or jumps."""
    writer = JournalWriter(temp_storage_root, "sess_replay_gap", epoch_id=1)
    writer.initialize_storage()

    writer.append_entry(JournalEntryType.SESSION_STARTED, -1, "2026-09-01T20:00:00Z", {"action": "START"})
    writer.append_entry(
        JournalEntryType.CYCLE_COMPLETED,
        0,
        "2026-09-01T20:00:01Z",
        {"status": "COMPLETED", "is_simulated": True, "confidence": 1.0, "uncertainty_metric": 0.0},
    )
    # Skip sequence 1 and jump to 5
    writer.append_entry(
        JournalEntryType.CYCLE_COMPLETED,
        5,
        "2026-09-01T20:00:02Z",
        {"status": "COMPLETED", "is_simulated": True, "confidence": 1.0, "uncertainty_metric": 0.0},
    )

    engine = ReplayEngine(temp_storage_root)
    rep = engine.verify_session_replay("sess_replay_gap")

    assert rep.replay_status == ReplayStatus.DIVERGENCE_DETECTED
    assert any("sequence discontinuity" in d for d in rep.state_divergences)


def test_provenance_and_simulated_flag_preservation(temp_storage_root: Path) -> None:
    """Verify missing provenance or simulation flags causes replay divergence detection."""
    writer = JournalWriter(temp_storage_root, "sess_replay_prov", epoch_id=1)
    writer.initialize_storage()

    writer.append_entry(JournalEntryType.SESSION_STARTED, -1, "2026-09-01T20:00:00Z", {"action": "START"})
    # Omit is_simulated from payload
    writer.append_entry(
        JournalEntryType.CYCLE_COMPLETED,
        0,
        "2026-09-01T20:00:01Z",
        {"status": "COMPLETED", "confidence": 1.0, "uncertainty_metric": 0.0},
    )

    engine = ReplayEngine(temp_storage_root)
    rep = engine.verify_session_replay("sess_replay_prov")

    assert rep.replay_status == ReplayStatus.DIVERGENCE_DETECTED
    assert any("missing is_simulated flag" in d for d in rep.state_divergences)
