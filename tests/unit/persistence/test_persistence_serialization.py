# -*- coding: utf-8 -*-
"""Unit Tests for Persistence Serialization, Hashing, and Wire Bounds."""

from __future__ import annotations

import pytest

from holomed.persistence.exceptions import PersistenceCapacityError, PersistenceValidationError
from holomed.persistence.serialization import (
    compute_entry_hash,
    filter_observational_fields,
    serialize_canonical_bytes,
)


def test_sha256_hash_computation() -> None:
    """Verify SHA-256 hash is 64 hex characters and bitwise deterministic."""
    entry_dict = {
        "entry_id": "e_01",
        "entry_type": "CYCLE_COMPLETED",
        "schema_version": "1.0",
        "timestamp_utc": "2026-09-01T20:00:00Z",
        "epoch_id": 1,
        "session_id": "sess_01",
        "sequence_number": 0,
        "payload": {"status": "COMPLETED", "fused_count": 2},
        "previous_entry_hash": "0" * 64,
    }
    h1 = compute_entry_hash(entry_dict)
    h2 = compute_entry_hash(entry_dict)
    assert h1 == h2
    assert len(h1) == 64


def test_hash_input_semantic_fields_and_observational_exclusion() -> None:
    """Verify execution_time_ms is explicitly excluded from semantic integrity hash."""
    entry_1 = {
        "entry_id": "e_01",
        "entry_type": "CYCLE_COMPLETED",
        "schema_version": "1.0",
        "timestamp_utc": "2026-09-01T20:00:00Z",
        "epoch_id": 1,
        "session_id": "sess_01",
        "sequence_number": 0,
        "payload": {"status": "COMPLETED", "execution_time_ms": 1.2345},
        "previous_entry_hash": "0" * 64,
    }
    entry_2 = dict(entry_1)
    entry_2["payload"] = {"status": "COMPLETED", "execution_time_ms": 99.8765}

    h1 = compute_entry_hash(entry_1)
    h2 = compute_entry_hash(entry_2)
    # Excluded execution_time_ms means hashes must match exactly
    assert h1 == h2

    # But modifying a semantic field MUST change the hash
    entry_3 = dict(entry_1)
    entry_3["payload"] = {"status": "DEGRADED", "execution_time_ms": 1.2345}
    h3 = compute_entry_hash(entry_3)
    assert h1 != h3


def test_record_size_boundary() -> None:
    """Verify record payload exceeding 64 KiB raises PersistenceCapacityError."""
    large_payload = {"key": "x" * 70000}
    with pytest.raises(PersistenceCapacityError):
        serialize_canonical_bytes(large_payload)


def test_canonical_float_normalization() -> None:
    """Verify -0.0 is normalized to 0.0 and non-finite floats are rejected."""
    raw = serialize_canonical_bytes({"val": -0.0})
    assert b"-0.0" not in raw
    assert b"0.0" in raw

    with pytest.raises(PersistenceValidationError):
        serialize_canonical_bytes({"val": float("nan")})
