# -*- coding: utf-8 -*-
"""Unit Tests for M09 Persistence Data Models and Invariants."""

from __future__ import annotations

import pytest

from holomed.persistence.exceptions import PersistenceValidationError
from holomed.persistence.models import (
    DurableAuditRecord,
    DurableSessionRecord,
    JournalEntry,
    JournalEntryType,
    ReplayStatus,
    ReplayVerificationReport,
)
from holomed.platform.models import SessionStatus


def test_durable_session_record_validation() -> None:
    """Verify DurableSessionRecord validates syntax, bounds, and immutability."""
    rec = DurableSessionRecord(
        session_id="sess_valid_01",
        epoch_id=1,
        status=SessionStatus.ACTIVE,
        created_timestamp_utc="2026-09-01T20:00:00Z",
        total_cycles=5,
        last_sequence=4,
    )
    assert rec.session_id == "sess_valid_01"
    assert rec.schema_version == "1.0"

    # Invalid session_id characters
    with pytest.raises(PersistenceValidationError):
        DurableSessionRecord(
            session_id="sess/invalid",
            epoch_id=1,
            status=SessionStatus.ACTIVE,
            created_timestamp_utc="2026-09-01T20:00:00Z",
        )

    # Negative epoch
    with pytest.raises(PersistenceValidationError):
        DurableSessionRecord(
            session_id="sess_1",
            epoch_id=-1,
            status=SessionStatus.ACTIVE,
            created_timestamp_utc="2026-09-01T20:00:00Z",
        )


def test_journal_entry_validation() -> None:
    """Verify JournalEntry validates hashes, sequences, and payload."""
    valid_hash = "a" * 64
    entry = JournalEntry(
        entry_id="entry_01",
        entry_type=JournalEntryType.CYCLE_COMPLETED,
        schema_version="1.0",
        timestamp_utc="2026-09-01T20:00:00Z",
        epoch_id=1,
        session_id="sess_01",
        sequence_number=0,
        payload={"status": "COMPLETED"},
        sha256_hash=valid_hash,
        previous_entry_hash="0" * 64,
    )
    assert entry.entry_type == JournalEntryType.CYCLE_COMPLETED

    # Invalid sha256 format
    with pytest.raises(PersistenceValidationError):
        JournalEntry(
            entry_id="entry_01",
            entry_type=JournalEntryType.CYCLE_COMPLETED,
            schema_version="1.0",
            timestamp_utc="2026-09-01T20:00:00Z",
            epoch_id=1,
            session_id="sess_01",
            sequence_number=0,
            payload={},
            sha256_hash="short_hash",
            previous_entry_hash="0" * 64,
        )


def test_durable_audit_record_validation() -> None:
    """Verify DurableAuditRecord validates audit fields and findings tuple."""
    rec = DurableAuditRecord(
        audit_id="audit_1",
        epoch_id=1,
        timestamp_utc="2026-09-01T20:00:00Z",
        audit_passed=True,
        required_services_count=8,
        present_services_count=8,
        active_sessions_count=1,
        cycle_history_count=10,
        findings=("Service A verified",),
    )
    assert rec.audit_passed is True
    assert isinstance(rec.findings, tuple)


def test_replay_verification_report_validation() -> None:
    """Verify ReplayVerificationReport checks parameters and immutability."""
    rep = ReplayVerificationReport(
        session_id="sess_01",
        total_entries_verified=10,
        initial_sequence=0,
        final_sequence=9,
        hash_chain_valid=True,
        state_divergences=(),
        replay_status=ReplayStatus.VERIFIED,
    )
    assert rep.replay_status == ReplayStatus.VERIFIED
    assert rep.hash_chain_valid is True
