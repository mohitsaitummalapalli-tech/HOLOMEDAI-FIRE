# -*- coding: utf-8 -*-
"""Deterministic Serialization and Wire Bounding for M07 Tools."""

from __future__ import annotations

import json
import math
from types import MappingProxyType
from typing import Any, Mapping
import unicodedata

from holomed.tools.exceptions import ToolCapacityError, ToolValidationError
from holomed.tools.models import MAX_TOOL_PAYLOAD_BYTES


def normalize_tool_value(val: Any) -> Any:
    """Recursively normalize data structures for deterministic serialization."""
    if isinstance(val, (dict, MappingProxyType)):
        sorted_keys = sorted(val.keys())
        return {unicodedata.normalize("NFC", str(k)): normalize_tool_value(val[k]) for k in sorted_keys}
    if isinstance(val, (list, tuple)):
        return [normalize_tool_value(v) for v in val]
    if isinstance(val, (set, frozenset)):
        norm_items = [normalize_tool_value(v) for v in val]
        return sorted(norm_items, key=lambda x: json.dumps(x, sort_keys=True))
    if isinstance(val, str):
        return unicodedata.normalize("NFC", val)
    if isinstance(val, float):
        if not math.isfinite(val):
            raise ToolValidationError(f"Non-finite float value encountered during serialization: {val}")
        if val == 0.0:
            return 0.0
        return round(val, 6)
    if hasattr(val, "value") and isinstance(val.value, (str, int)):
        return val.value
    if hasattr(val, "as_tuple"):
        return [normalize_tool_value(v) for v in val.as_tuple()]
    return val


def serialize_tool_payload(payload: Mapping[str, Any]) -> MappingProxyType[str, Any]:
    """Validate and canonically normalize payload dictionary within MAX_TOOL_PAYLOAD_BYTES."""
    if not isinstance(payload, (dict, MappingProxyType)):
        raise ToolValidationError(f"Payload must be a mapping, got {type(payload).__name__}")

    normalized = normalize_tool_value(payload)
    json_bytes = json.dumps(normalized, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    if len(json_bytes) > MAX_TOOL_PAYLOAD_BYTES:
        raise ToolCapacityError(
            f"Tool payload size ({len(json_bytes)} bytes) exceeds maximum limit of {MAX_TOOL_PAYLOAD_BYTES} bytes"
        )
    return MappingProxyType(normalized)


def serialize_tool_bytes(payload: Mapping[str, Any]) -> bytes:
    """Convert payload mapping directly to verified canonical UTF-8 bytes."""
    normalized = normalize_tool_value(payload)
    raw_bytes = json.dumps(normalized, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    if len(raw_bytes) > MAX_TOOL_PAYLOAD_BYTES:
        raise ToolCapacityError(
            f"Tool payload size ({len(raw_bytes)} bytes) exceeds maximum limit of {MAX_TOOL_PAYLOAD_BYTES} bytes"
        )
    return raw_bytes
