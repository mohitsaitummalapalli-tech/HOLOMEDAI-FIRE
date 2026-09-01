# -*- coding: utf-8 -*-
"""Unit tests for XR Deterministic Serialization and Wire Payload Bounding."""

from __future__ import annotations

import pytest

from holomed.xr.exceptions import (
    XRCapacityError,
    XRValidationError,
)
from holomed.xr.serialization import (
    serialize_xr_bytes,
    serialize_xr_payload,
)


def test_xr_serialization_nan_infinity_rejection() -> None:
    """Verify rejection of non-finite float values."""
    with pytest.raises(XRValidationError):
        serialize_xr_payload({"val": float("nan")})

    with pytest.raises(XRValidationError):
        serialize_xr_payload({"val": float("inf")})


def test_xr_serialization_negative_zero_normalization() -> None:
    """Verify -0.0 is normalized to +0.0."""
    res = serialize_xr_payload({"zero": -0.0})
    assert res["zero"] == 0.0
    raw_bytes = serialize_xr_bytes({"zero": -0.0})
    assert b"-0.0" not in raw_bytes


def test_xr_serialization_capacity_boundary() -> None:
    """Verify MAX_XR_PAYLOAD_BYTES (65,536) strictly raises XRCapacityError on overflow (D234)."""
    large_payload = {"data": "x" * 70000}
    with pytest.raises(XRCapacityError):
        serialize_xr_payload(large_payload)
