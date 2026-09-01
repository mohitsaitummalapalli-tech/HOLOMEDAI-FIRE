# -*- coding: utf-8 -*-
"""Unit tests for Ultron Deterministic Serialization and Wire Bounds."""

from __future__ import annotations

from types import MappingProxyType
import pytest

from holomed.ultron.exceptions import UltronCapacityError, UltronValidationError
from holomed.ultron.serialization import (
    canonical_json_parameters,
    serialize_ultron_bytes,
    serialize_ultron_payload,
)


def test_serialization_nan_infinity_rejection() -> None:
    """Verify rejection of NaN and Infinity values."""
    with pytest.raises(UltronValidationError):
        serialize_ultron_payload({"val": float("nan")})

    with pytest.raises(UltronValidationError):
        serialize_ultron_payload({"val": float("inf")})


def test_serialization_negative_zero_normalization() -> None:
    """Verify -0.0 normalizes to +0.0."""
    res = serialize_ultron_payload({"zero": -0.0})
    assert res["zero"] == 0.0
    json_bytes = serialize_ultron_bytes({"zero": -0.0})
    assert b"-0.0" not in json_bytes


def test_serialization_capacity_boundary() -> None:
    """Verify MAX_ULTRON_PAYLOAD_BYTES (65,536) strictly raises UltronCapacityError on overflow."""
    # 64 KiB payload
    large_payload = {"data": "x" * 70000}
    with pytest.raises(UltronCapacityError):
        serialize_ultron_payload(large_payload)


def test_canonical_json_parameters_deterministic() -> None:
    """Verify identical JSON string regardless of dictionary insertion order."""
    d1 = {"b": 2, "a": 1, "c": [3, 2, 1]}
    d2 = {"c": [3, 2, 1], "a": 1, "b": 2}
    assert canonical_json_parameters(d1) == canonical_json_parameters(d2)
