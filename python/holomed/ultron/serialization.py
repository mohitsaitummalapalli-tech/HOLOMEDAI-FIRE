# -*- coding: utf-8 -*-
"""Deterministic Serialization and Unicode NFC Normalization for M04 Ultron."""

from __future__ import annotations

import json
import math
from types import MappingProxyType
from typing import Any, Mapping
import unicodedata

from holomed.ultron.exceptions import UltronCapacityError, UltronValidationError
from holomed.ultron.models import MAX_ULTRON_PAYLOAD_BYTES


def normalize_value(val: Any) -> Any:
    """Recursively normalize data structures for deterministic serialization."""
    if isinstance(val, (dict, MappingProxyType)):
        sorted_keys = sorted(val.keys())
        return {unicodedata.normalize("NFC", str(k)): normalize_value(val[k]) for k in sorted_keys}
    if isinstance(val, (list, tuple)):
        return [normalize_value(v) for v in val]
    if isinstance(val, (set, frozenset)):
        norm_items = [normalize_value(v) for v in val]
        return sorted(norm_items, key=lambda x: json.dumps(x, sort_keys=True))
    if isinstance(val, str):
        return unicodedata.normalize("NFC", val)
    if isinstance(val, float):
        if not math.isfinite(val):
            raise UltronValidationError(f"Non-finite float value encountered during serialization: {val}")
        # Normalize -0.0 to +0.0
        if val == 0.0:
            return 0.0
        return round(val, 6)
    if hasattr(val, "value") and isinstance(val.value, (str, int)):
        return val.value
    return val


def serialize_ultron_payload(payload: Mapping[str, Any]) -> MappingProxyType[str, Any]:
    """Validate and canonically normalize payload dictionary within MAX_ULTRON_PAYLOAD_BYTES."""
    if not isinstance(payload, (dict, MappingProxyType)):
        raise UltronValidationError(f"Payload must be a mapping, got {type(payload).__name__}")

    normalized = normalize_value(payload)
    json_bytes = json.dumps(normalized, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    if len(json_bytes) > MAX_ULTRON_PAYLOAD_BYTES:
        raise UltronCapacityError(
            f"Ultron payload size ({len(json_bytes)} bytes) exceeds maximum allowable limit of {MAX_ULTRON_PAYLOAD_BYTES} bytes"
        )
    return MappingProxyType(normalized)


def canonical_json_parameters(parameters: Mapping[str, Any]) -> str:
    """Return deterministic compact JSON string of parameters for intent deduplication."""
    normalized = normalize_value(parameters)
    return json.dumps(normalized, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def serialize_ultron_bytes(payload: Mapping[str, Any]) -> bytes:
    """Convert payload mapping directly to verified canonical UTF-8 bytes."""
    normalized = normalize_value(payload)
    raw_bytes = json.dumps(normalized, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    if len(raw_bytes) > MAX_ULTRON_PAYLOAD_BYTES:
        raise UltronCapacityError(
            f"Ultron payload size ({len(raw_bytes)} bytes) exceeds maximum allowable limit of {MAX_ULTRON_PAYLOAD_BYTES} bytes"
        )
    return raw_bytes
