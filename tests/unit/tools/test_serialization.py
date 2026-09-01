# -*- coding: utf-8 -*-
"""Unit tests for Tool Deterministic Serialization and Wire Bounds."""

from __future__ import annotations

import pytest

from holomed.tools.exceptions import (
    ToolCapacityError,
    ToolValidationError,
)
from holomed.tools.serialization import (
    serialize_tool_bytes,
    serialize_tool_payload,
)


def test_tool_serialization_nan_infinity_rejection() -> None:
    """Verify rejection of non-finite floats during tool serialization."""
    with pytest.raises(ToolValidationError):
        serialize_tool_payload({"val": float("nan")})

    with pytest.raises(ToolValidationError):
        serialize_tool_payload({"val": float("inf")})


def test_tool_serialization_negative_zero_normalization() -> None:
    """Verify -0.0 is normalized to +0.0."""
    res = serialize_tool_payload({"zero": -0.0})
    assert res["zero"] == 0.0
    raw_bytes = serialize_tool_bytes({"zero": -0.0})
    assert b"-0.0" not in raw_bytes


def test_tool_serialization_capacity_boundary() -> None:
    """Verify MAX_TOOL_PAYLOAD_BYTES (65,536) strictly raises ToolCapacityError on overflow."""
    large_payload = {"data": "x" * 70000}
    with pytest.raises(ToolCapacityError):
        serialize_tool_payload(large_payload)
