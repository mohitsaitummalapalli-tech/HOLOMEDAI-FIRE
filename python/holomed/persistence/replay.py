# -*- coding: utf-8 -*-
"""Deterministic Semantic Verification Replay Engine for M09."""

from __future__ import annotations

from pathlib import Path

from holomed.persistence.journal import GENESIS_PREVIOUS_HASH, JournalReader
from holomed.persistence.models import (
    JournalEntryType,
    ReplayStatus,
    ReplayVerificationReport,
)


class ReplayEngine:
    """Performs deterministic offline semantic replay and hash-chain verification."""

    def __init__(self, storage_root: Path) -> None:
        self._storage_root = Path(storage_root)

    def verify_session_replay(self, session_id: str) -> ReplayVerificationReport:
        """Read and verify historical journal records for semantic consistency."""
        journal_path = self._storage_root / f"{session_id}.jsonl"
        if not journal_path.exists():
            return ReplayVerificationReport(
                session_id=session_id,
                total_entries_verified=0,
                initial_sequence=-1,
                final_sequence=-1,
                hash_chain_valid=False,
                state_divergences=("Journal file not found",),
                replay_status=ReplayStatus.EMPTY,
            )

        entries, _ = JournalReader.read_and_recover_journal(journal_path)
        if not entries:
            return ReplayVerificationReport(
                session_id=session_id,
                total_entries_verified=0,
                initial_sequence=-1,
                final_sequence=-1,
                hash_chain_valid=True,
                state_divergences=(),
                replay_status=ReplayStatus.EMPTY,
            )

        divergences: list[str] = []
        expected_prev_hash = GENESIS_PREVIOUS_HASH
        expected_cycle_seq = 0
        initial_seq = -1
        final_seq = -1

        for i, entry in enumerate(entries):
            # 1. Hash chain check
            if entry.previous_entry_hash != expected_prev_hash:
                divergences.append(
                    f"Entry {i} ({entry.entry_id}): previous hash mismatch "
                    f"(expected {expected_prev_hash[:16]}..., got {entry.previous_entry_hash[:16]}...)"
                )

            # 2. Sequence check for cycle entries
            if entry.entry_type in (JournalEntryType.CYCLE_COMPLETED, JournalEntryType.CYCLE_DEGRADED):
                if initial_seq == -1:
                    initial_seq = entry.sequence_number
                final_seq = entry.sequence_number

                if entry.sequence_number != expected_cycle_seq:
                    divergences.append(
                        f"Entry {i}: sequence discontinuity (expected {expected_cycle_seq}, got {entry.sequence_number})"
                    )
                expected_cycle_seq = entry.sequence_number + 1

                # 3. Provenance validation
                p = entry.payload
                if "is_simulated" not in p:
                    divergences.append(f"Entry {i}: missing is_simulated flag")
                if "confidence" not in p or not (0.0 <= p["confidence"] <= 1.0):
                    divergences.append(f"Entry {i}: invalid or missing confidence")
                if "uncertainty_metric" not in p or not (0.0 <= p["uncertainty_metric"] <= 1.0):
                    divergences.append(f"Entry {i}: invalid or missing uncertainty_metric")

            expected_prev_hash = entry.sha256_hash

        status = ReplayStatus.VERIFIED if not divergences else ReplayStatus.DIVERGENCE_DETECTED

        return ReplayVerificationReport(
            session_id=session_id,
            total_entries_verified=len(entries),
            initial_sequence=initial_seq,
            final_sequence=final_seq,
            hash_chain_valid=len(divergences) == 0,
            state_divergences=tuple(divergences),
            replay_status=status,
        )
