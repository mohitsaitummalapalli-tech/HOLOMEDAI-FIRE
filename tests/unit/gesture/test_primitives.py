# -*- coding: utf-8 -*-
"""Unit tests for finger state detection and gesture primitive classification."""

from __future__ import annotations

import pytest

from holomed.gesture.models import (
    FingerState,
    GestureType,
    unproject_spatial_landmark,
)
from holomed.gesture.primitives import (
    classify_gesture_primitive,
    compute_finger_states,
)
from holomed.vision.models import SpatialLandmark


def test_finger_states_open_hand(open_hand_landmarks: tuple[SpatialLandmark, ...]) -> None:
    """Verify D204: all 5 fingers classified as EXTENDED for flat open hand."""
    lms_3d = {lm.landmark_id: unproject_spatial_landmark(lm) for lm in open_hand_landmarks}
    states = compute_finger_states(lms_3d)

    assert states.thumb == FingerState.EXTENDED
    assert states.index == FingerState.EXTENDED
    assert states.middle == FingerState.EXTENDED
    assert states.ring == FingerState.EXTENDED
    assert states.pinky == FingerState.EXTENDED


def test_finger_states_closed_fist(closed_fist_landmarks: tuple[SpatialLandmark, ...]) -> None:
    """Verify D204: all 5 fingers classified as FOLDED for curled fist."""
    lms_3d = {lm.landmark_id: unproject_spatial_landmark(lm) for lm in closed_fist_landmarks}
    states = compute_finger_states(lms_3d)

    assert states.thumb == FingerState.FOLDED
    assert states.index == FingerState.FOLDED
    assert states.middle == FingerState.FOLDED
    assert states.ring == FingerState.FOLDED
    assert states.pinky == FingerState.FOLDED


def test_finger_states_pointing_hand(pointing_landmarks: tuple[SpatialLandmark, ...]) -> None:
    """Verify pointing hand has Index EXTENDED and Middle/Ring/Pinky FOLDED."""
    lms_3d = {lm.landmark_id: unproject_spatial_landmark(lm) for lm in pointing_landmarks}
    states = compute_finger_states(lms_3d)

    assert states.index == FingerState.EXTENDED
    assert states.middle == FingerState.FOLDED
    assert states.ring == FingerState.FOLDED
    assert states.pinky == FingerState.FOLDED


def test_gesture_primitive_pinch_detection(pinch_landmarks: tuple[SpatialLandmark, ...]) -> None:
    """Verify D203 Priority 1: PINCH detected when thumb and index tips <= 0.035m."""
    lms_3d = {lm.landmark_id: unproject_spatial_landmark(lm) for lm in pinch_landmarks}
    states = compute_finger_states(lms_3d)
    gesture, conf = classify_gesture_primitive(lms_3d, states)

    assert gesture == GestureType.PINCH
    assert 0.0 <= conf <= 1.0


def test_gesture_primitive_pointing_precedence(pointing_landmarks: tuple[SpatialLandmark, ...]) -> None:
    """Verify D203 Priority 2: POINT detected and NEVER misclassified as CLOSED_HAND."""
    lms_3d = {lm.landmark_id: unproject_spatial_landmark(lm) for lm in pointing_landmarks}
    states = compute_finger_states(lms_3d)
    gesture, conf = classify_gesture_primitive(lms_3d, states)

    assert gesture == GestureType.POINT
    assert gesture != GestureType.CLOSED_HAND
    assert conf > 0.5


def test_gesture_primitive_two_finger(two_finger_landmarks: tuple[SpatialLandmark, ...]) -> None:
    """Verify D203 Priority 3: TWO_FINGER detected when Index and Middle extended."""
    lms_3d = {lm.landmark_id: unproject_spatial_landmark(lm) for lm in two_finger_landmarks}
    states = compute_finger_states(lms_3d)
    gesture, conf = classify_gesture_primitive(lms_3d, states)

    assert gesture == GestureType.TWO_FINGER
    assert conf > 0.5


def test_gesture_primitive_thumb_up(thumb_up_landmarks: tuple[SpatialLandmark, ...]) -> None:
    """Verify D203 Priority 4: THUMB_UP detected when thumb points up (+Y)."""
    lms_3d = {lm.landmark_id: unproject_spatial_landmark(lm) for lm in thumb_up_landmarks}
    states = compute_finger_states(lms_3d)
    gesture, conf = classify_gesture_primitive(lms_3d, states)

    assert gesture == GestureType.THUMB_UP
    assert conf > 0.5


def test_gesture_primitive_thumb_down(thumb_down_landmarks: tuple[SpatialLandmark, ...]) -> None:
    """Verify D203 Priority 5: THUMB_DOWN detected when thumb points down (-Y)."""
    lms_3d = {lm.landmark_id: unproject_spatial_landmark(lm) for lm in thumb_down_landmarks}
    states = compute_finger_states(lms_3d)
    gesture, conf = classify_gesture_primitive(lms_3d, states)

    assert gesture == GestureType.THUMB_DOWN
    assert conf > 0.5


def test_gesture_primitive_open_palm(open_hand_landmarks: tuple[SpatialLandmark, ...]) -> None:
    """Verify D203 Priority 6: OPEN_PALM detected when >= 4 fingers extended."""
    lms_3d = {lm.landmark_id: unproject_spatial_landmark(lm) for lm in open_hand_landmarks}
    states = compute_finger_states(lms_3d)
    gesture, conf = classify_gesture_primitive(lms_3d, states)

    assert gesture == GestureType.OPEN_PALM
    assert conf > 0.7


def test_gesture_primitive_closed_hand(closed_fist_landmarks: tuple[SpatialLandmark, ...]) -> None:
    """Verify D203 Priority 7: CLOSED_HAND detected when 0 or 1 finger extended (and no higher priority)."""
    lms_3d = {lm.landmark_id: unproject_spatial_landmark(lm) for lm in closed_fist_landmarks}
    states = compute_finger_states(lms_3d)
    gesture, conf = classify_gesture_primitive(lms_3d, states)

    assert gesture == GestureType.CLOSED_HAND
    assert conf > 0.5
