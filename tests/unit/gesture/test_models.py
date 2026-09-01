# -*- coding: utf-8 -*-
"""Unit tests for M03 data models, unprojection, and serialization contracts."""

from __future__ import annotations

import math
import uuid
import pytest

from holomed.gesture.exceptions import (
    GestureCapacityError,
    GestureValidationError,
)
from holomed.gesture.models import (
    HandLandmark3D,
    HandObservation,
    Handedness,
    MAX_HAND_LANDMARKS,
    quantize_coord,
    serialize_gesture_payload,
    unproject_spatial_landmark,
)
from holomed.vision.models import SpatialLandmark
from tests.unit.gesture.conftest import make_observation


def test_hand_observation_valid(open_hand_landmarks: tuple[SpatialLandmark, ...]) -> None:
    """Verify clean instantiation of HandObservation with canonical landmarks."""
    obs = make_observation(open_hand_landmarks)
    assert obs.device_id == "cam_optical_0"
    assert obs.physical_id == "phys_cam_0"
    assert obs.sequence_number == 1
    assert obs.epoch_id == 1
    assert obs.handedness == Handedness.RIGHT
    assert len(obs.landmarks) == 21
    assert not obs.is_partial


def test_hand_observation_uuid_validation(open_hand_landmarks: tuple[SpatialLandmark, ...]) -> None:
    """Verify frame_id must be canonical lowercase UUIDv4."""
    with pytest.raises(GestureValidationError, match="canonical lowercase UUIDv4"):
        make_observation(open_hand_landmarks, frame_id="not-a-uuid")

    with pytest.raises(GestureValidationError, match="canonical lowercase UUIDv4"):
        make_observation(open_hand_landmarks, frame_id=str(uuid.uuid4()).upper())


def test_hand_observation_partial_flag_contract(open_hand_landmarks: tuple[SpatialLandmark, ...]) -> None:
    """Verify D202: landmark count < 21 requires is_partial=True."""
    partial_15 = open_hand_landmarks[:15]

    # Without is_partial=True, must raise GestureValidationError
    with pytest.raises(GestureValidationError, match="requires is_partial=True"):
        make_observation(partial_15, is_partial=False)

    # With is_partial=True, succeeds
    obs = make_observation(partial_15, is_partial=True)
    assert len(obs.landmarks) == 15
    assert obs.is_partial is True


def test_hand_observation_empty_or_excess_landmarks_rejected(
    open_hand_landmarks: tuple[SpatialLandmark, ...],
) -> None:
    """Verify empty landmarks or > 21 landmarks unconditionally rejected."""
    with pytest.raises(GestureValidationError, match="cannot be empty"):
        make_observation((), is_partial=True)

    extra_landmark = SpatialLandmark(21, "EXTRA", 0.5, 0.5, 1.0, 0.9)
    with pytest.raises(GestureValidationError, match="exceeds MAX_HAND_LANDMARKS"):
        make_observation(open_hand_landmarks + (extra_landmark,), is_partial=True)


def test_hand_observation_duplicate_landmark_ids_rejected(
    open_hand_landmarks: tuple[SpatialLandmark, ...],
) -> None:
    """Verify duplicate landmark_id within an observation is strictly rejected."""
    dup = SpatialLandmark(0, "WRIST_DUP", 0.5, 0.5, 1.0, 0.9)
    with pytest.raises(GestureValidationError, match="Duplicate landmark_id"):
        make_observation(open_hand_landmarks[:20] + (dup,))


def test_hand_observation_negative_sequence_or_epoch(
    open_hand_landmarks: tuple[SpatialLandmark, ...],
) -> None:
    """Verify non-negative sequence numbers and positive epoch IDs."""
    with pytest.raises(GestureValidationError, match="sequence_number"):
        make_observation(open_hand_landmarks, sequence_number=-1)

    with pytest.raises(GestureValidationError, match="epoch_id"):
        make_observation(open_hand_landmarks, epoch_id=0)


def test_hand_landmark_3d_bounds_enforcement() -> None:
    """Verify D201: metric coordinate bounds [-10m, +10m]."""
    # Valid landmark
    lm = HandLandmark3D(0, "WRIST", 0.1, -0.2, 1.5, 0.5, 0.5, 0.95)
    assert lm.x == 0.1
    assert lm.y == -0.2
    assert lm.z == 1.5

    # Out of bounds X
    with pytest.raises(GestureValidationError, match="exceeds bound"):
        HandLandmark3D(0, "WRIST", 10.01, 0.0, 1.0, 0.5, 0.5, 0.95)

    # Out of bounds Y
    with pytest.raises(GestureValidationError, match="exceeds bound"):
        HandLandmark3D(0, "WRIST", 0.0, -10.05, 1.0, 0.5, 0.5, 0.95)

    # Out of bounds Z
    with pytest.raises(GestureValidationError, match="exceeds bound"):
        HandLandmark3D(0, "WRIST", 0.0, 0.0, 10.5, 0.5, 0.5, 0.95)


def test_unproject_spatial_landmark_m01_optical_convention() -> None:
    """Verify D201: unprojection Z = depth, X = (u - 0.5)*Z, Y = (0.5 - v)*Z."""
    # Center screen (u=0.5, v=0.5) at depth 2.0m -> X=0, Y=0, Z=2.0
    slm_center = SpatialLandmark(0, "WRIST", 0.5, 0.5, depth_m=2.0, confidence=0.95)
    p3d = unproject_spatial_landmark(slm_center)
    assert p3d.x == 0.0
    assert p3d.y == 0.0
    assert p3d.z == 2.0

    # Top-Right (u=1.0, v=0.0) at depth 1.0m -> X = +0.5m, Y = +0.5m (+Y is UP)
    slm_tr = SpatialLandmark(4, "THUMB_TIP", 1.0, 0.0, depth_m=1.0, confidence=0.9)
    p3d_tr = unproject_spatial_landmark(slm_tr)
    assert p3d_tr.x == 0.5
    assert p3d_tr.y == 0.5
    assert p3d_tr.z == 1.0

    # Missing depth raises GestureValidationError
    slm_no_depth = SpatialLandmark(0, "WRIST", 0.5, 0.5, depth_m=None, confidence=0.9)
    with pytest.raises(GestureValidationError, match="invalid or missing depth_m"):
        unproject_spatial_landmark(slm_no_depth)


def test_quantize_coord_and_negative_zero() -> None:
    """Verify quantization to 1e-6 and -0.0 normalization."""
    assert quantize_coord(-0.0) == 0.0
    assert quantize_coord(0.1234567) == 0.123457
    with pytest.raises(GestureValidationError, match="must be finite"):
        quantize_coord(float("nan"))
    with pytest.raises(GestureValidationError, match="must be finite"):
        quantize_coord(float("inf"))


def test_serialize_gesture_payload_bounds() -> None:
    """Verify JSON canonical serialization and 64 KiB cap."""
    small = {"track_id": "hand_0001", "state": "CONFIRMED", "count": 10}
    clean = serialize_gesture_payload(small)
    assert clean["track_id"] == "hand_0001"

    # NaN rejection
    with pytest.raises(GestureValidationError, match="Non-finite float"):
        serialize_gesture_payload({"val": float("nan")})

    # 64 KiB cap rejection
    oversized = {"big": "x" * 70000}
    with pytest.raises(GestureCapacityError, match="exceeds MAX_GESTURE_PAYLOAD_BYTES"):
        serialize_gesture_payload(oversized)
