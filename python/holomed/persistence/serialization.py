# -*- coding: utf-8 -*-
"""Deterministic Canonical Serialization and SHA-256 Hashing for M09 Journal."""

from __future__ import annotations

import hashlib
import json
import math
from types import MappingProxyType
from typing import Any, Mapping
import unicodedata

from holomed.persistence.exceptions import (
    PersistenceCapacityError,
    PersistenceValidationError,
)
from holomed.persistence.models import MAX_JOURNAL_RECORD_BYTES


def normalize_persistence_value(val: Any) -> Any:
    """Recursively normalize values for deterministic canonical byte generation."""
    if isinstance(val, (dict, MappingProxyType)):
        sorted_keys = sorted(val.keys())
        return {
            unicodedata.normalize("NFC", str(k)): normalize_persistence_value(val[k])
            for k in sorted_keys
        }
    if isinstance(val, (list, tuple)):
        return [normalize_persistence_value(v) for v in val]
    if isinstance(val, (set, frozenset)):
        norm_items = [normalize_persistence_value(v) for v in val]
        return sorted(norm_items, key=lambda x: json.dumps(x, sort_keys=True))
    if isinstance(val, str):
        return unicodedata.normalize("NFC", val)
    if isinstance(val, float):
        if not math.isfinite(val):
            raise PersistenceValidationError(f"Non-finite float encountered: {val}")
        if val == 0.0:
            return 0.0
        return round(val, 6)
    if hasattr(val, "value") and isinstance(val.value, (str, int)):
        return val.value
    return val


def filter_observational_fields(payload: Mapping[str, Any]) -> dict[str, Any]:
    """Filter non-deterministic observational timing fields before cryptographic hashing.

    Explicitly excludes 'execution_time_ms' which measures system wall-clock duration
    and does not affect semantic cycle determinism.
    """
    return {k: v for k, v in payload.items() if k != "execution_time_ms"}


def serialize_canonical_bytes(data: Mapping[str, Any]) -> bytes:
    """Serialize a mapping into canonical NFC UTF-8 bytes within 64 KiB."""
    normalized = normalize_persistence_value(data)
    raw_bytes = json.dumps(
        normalized,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")

    if len(raw_bytes) > MAX_JOURNAL_RECORD_BYTES:
        raise PersistenceCapacityError(
            f"Record size ({len(raw_bytes)} bytes) exceeds maximum limit of {MAX_JOURNAL_RECORD_BYTES} bytes"
        )
    return raw_bytes


def compute_entry_hash(entry_dict: Mapping[str, Any]) -> str:
    """Compute SHA-256 over all semantic integrity fields, excluding sha256_hash.

    The hash covers:
    - entry_id
    - entry_type
    - schema_version
    - timestamp_utc
    - epoch_id
    - session_id
    - sequence_number
    - semantic payload (with execution_time_ms excluded)
    - previous_entry_hash
    """
    raw_payload = entry_dict.get("payload", {})
    semantic_payload = filter_observational_fields(raw_payload)

    hashable_dict = {
        "entry_id": entry_dict["entry_id"],
        "entry_type": getattr(entry_dict["entry_type"], "value", entry_dict["entry_type"]),
        "schema_version": entry_dict["schema_version"],
        "timestamp_utc": entry_dict["timestamp_utc"],
        "epoch_id": entry_dict["epoch_id"],
        "session_id": entry_dict["session_id"],
        "sequence_number": entry_dict["sequence_number"],
        "payload": semantic_payload,
        "previous_entry_hash": entry_dict["previous_entry_hash"],
    }
    canonical = serialize_canonical_bytes(hashable_dict)
    return hashlib.sha256(canonical).hexdigest()
