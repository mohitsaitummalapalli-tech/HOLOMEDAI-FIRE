# -*- coding: utf-8 -*-
"""Deterministic Canonical Serialization for M10 Workflow State."""

from __future__ import annotations

import json
import math
from types import MappingProxyType
from typing import Any, Mapping
import unicodedata

from holomed.workflow.exceptions import WorkflowValidationError


def normalize_workflow_value(val: Any) -> Any:
    """Recursively normalize values for deterministic canonical byte generation."""
    if isinstance(val, (dict, MappingProxyType)):
        sorted_keys = sorted(val.keys())
        return {
            unicodedata.normalize("NFC", str(k)): normalize_workflow_value(val[k])
            for k in sorted_keys
        }
    if isinstance(val, (list, tuple)):
        return [normalize_workflow_value(v) for v in val]
    if isinstance(val, (set, frozenset)):
        norm_items = [normalize_workflow_value(v) for v in val]
        return sorted(norm_items, key=lambda x: json.dumps(x, sort_keys=True))
    if isinstance(val, str):
        return unicodedata.normalize("NFC", val)
    if isinstance(val, float):
        if not math.isfinite(val):
            raise WorkflowValidationError(f"Non-finite float encountered: {val}")
        if val == 0.0:
            return 0.0
        return round(val, 6)
    if hasattr(val, "value") and isinstance(val.value, (str, int)):
        return val.value
    return val


def serialize_canonical_workflow_bytes(data: Mapping[str, Any]) -> bytes:
    """Serialize a mapping into canonical NFC UTF-8 bytes."""
    normalized = normalize_workflow_value(data)
    return json.dumps(
        normalized,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")
