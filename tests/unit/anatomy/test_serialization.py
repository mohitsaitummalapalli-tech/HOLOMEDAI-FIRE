# -*- coding: utf-8 -*-
"""Unit tests for Anatomy Deterministic Serialization and Wire Bounds."""

from __future__ import annotations

import pytest

from holomed.anatomy.exceptions import (
    AnatomyCapacityError,
    AnatomyValidationError,
)
from holomed.anatomy.serialization import (
    serialize_anatomy_bytes,
    serialize_anatomy_payload,
)


def test_serialization_nan_infinity_rejection() -> None:
    """Verify rejection of NaN and Infinity values."""
    with pytest.raises(AnatomyValidationError):
        serialize_anatomy_payload({"val": float("nan")})

    with pytest.raises(AnatomyValidationError):
        serialize_anatomy_payload({"val": float("inf")})


def test_serialization_negative_zero_normalization() -> None:
    """Verify -0.0 is normalized to +0.0."""
    res = serialize_anatomy_payload({"zero": -0.0})
    assert res["zero"] == 0.0
    raw_bytes = serialize_anatomy_bytes({"zero": -0.0})
    assert b"-0.0" not in raw_bytes


def test_serialization_capacity_boundary() -> None:
    """Verify MAX_ANATOMY_PAYLOAD_BYTES (65,536) strictly raises AnatomyCapacityError on overflow."""
    large_payload = {"data": "x" * 70000}
    with pytest.raises(AnatomyCapacityError):
        serialize_anatomy_payload(large_payload)
