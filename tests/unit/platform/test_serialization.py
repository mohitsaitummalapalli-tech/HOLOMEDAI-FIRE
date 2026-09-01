# -*- coding: utf-8 -*-
"""Unit tests for Platform Deterministic Serialization and Wire Bounds (D252)."""

from __future__ import annotations

import pytest

from holomed.platform.exceptions import (
    PlatformCapacityError,
    PlatformValidationError,
)
from holomed.platform.serialization import (
    serialize_platform_bytes,
    serialize_platform_payload,
)


def test_d252_serialization_bounds() -> None:
    """Verify D252 payloads exceeding 64 KiB raise PlatformCapacityError."""
    large_payload = {"summary": "x" * 70000}
    with pytest.raises(PlatformCapacityError):
        serialize_platform_payload(large_payload)


def test_serialization_nan_infinity_rejection() -> None:
    """Verify non-finite float rejection."""
    with pytest.raises(PlatformValidationError):
        serialize_platform_payload({"val": float("nan")})


def test_serialization_negative_zero_normalized() -> None:
    """Verify -0.0 is normalized to +0.0."""
    res = serialize_platform_payload({"zero": -0.0})
    assert res["zero"] == 0.0
    raw_bytes = serialize_platform_bytes({"zero": -0.0})
    assert b"-0.0" not in raw_bytes
