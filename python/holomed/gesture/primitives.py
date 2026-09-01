# -*- coding: utf-8 -*-
"""Deterministic geometric finger state and gesture primitive classification."""

from __future__ import annotations

import math
from typing import Mapping

from holomed.gesture.models import (
    FingerState,
    FingerStateSet,
    GestureType,
    HandLandmark3D,
    PINCH_DISTANCE_METERS,
    quantize_coord,
)


def _dist3d(p1: HandLandmark3D, p2: HandLandmark3D) -> float:
    """Calculate Euclidean distance between two 3D landmarks."""
    return math.sqrt((p1.x - p2.x) ** 2 + (p1.y - p2.y) ** 2 + (p1.z - p2.z) ** 2)


def compute_finger_states(landmarks: Mapping[int, HandLandmark3D]) -> FingerStateSet:
    """Classify the extension state of all five digits using D204 distance inequalities."""
    wrist = landmarks.get(0)
    pinky_mcp = landmarks.get(17)

    # 1. Thumb (0: wrist, 1: cmc, 2: mcp, 3: ip, 4: tip, 17: pinky_mcp)
    thumb_tip = landmarks.get(4)
    thumb_ip = landmarks.get(3)
    thumb_mcp = landmarks.get(2)

    thumb_state = FingerState.UNKNOWN
    if wrist is not None and pinky_mcp is not None and thumb_tip is not None and thumb_ip is not None and thumb_mcp is not None:
        try:
            d_w_t = _dist3d(wrist, thumb_tip)
            d_w_ip = _dist3d(wrist, thumb_ip)
            d_p17_t = _dist3d(pinky_mcp, thumb_tip)
            d_p17_m = _dist3d(pinky_mcp, thumb_mcp)
            if d_w_t > d_w_ip and d_p17_t > d_p17_m:
                thumb_state = FingerState.EXTENDED
            else:
                thumb_state = FingerState.FOLDED
        except (ValueError, ArithmeticError):
            thumb_state = FingerState.UNKNOWN

    def _eval_finger(mcp_id: int, pip_id: int, tip_id: int) -> FingerState:
        if wrist is None:
            return FingerState.UNKNOWN
        mcp = landmarks.get(mcp_id)
        pip = landmarks.get(pip_id)
        tip = landmarks.get(tip_id)
        if mcp is None or pip is None or tip is None:
            return FingerState.UNKNOWN
        try:
            d_w_t = _dist3d(wrist, tip)
            d_w_p = _dist3d(wrist, pip)
            d_m_t = _dist3d(mcp, tip)
            d_m_p = _dist3d(mcp, pip)
            if d_w_t > d_w_p and d_m_t > d_m_p:
                return FingerState.EXTENDED
            return FingerState.FOLDED
        except (ValueError, ArithmeticError):
            return FingerState.UNKNOWN

    index_state = _eval_finger(5, 6, 8)
    middle_state = _eval_finger(9, 10, 12)
    ring_state = _eval_finger(13, 14, 16)
    pinky_state = _eval_finger(17, 18, 20)

    return FingerStateSet(
        thumb=thumb_state,
        index=index_state,
        middle=middle_state,
        ring=ring_state,
        pinky=pinky_state,
    )


def classify_gesture_primitive(
    landmarks: Mapping[int, HandLandmark3D],
    finger_states: FingerStateSet,
) -> tuple[GestureType, float]:
    """Classify gesture primitive with deterministic D203 8-tier precedence."""
    # Step 1: PINCH
    t_tip = landmarks.get(4)
    i_tip = landmarks.get(8)
    if t_tip is not None and i_tip is not None:
        pinch_dist = _dist3d(t_tip, i_tip)
        if pinch_dist <= PINCH_DISTANCE_METERS:
            # High confidence when tips are close, scaled by landmark confidence
            margin = 1.0 - (pinch_dist / PINCH_DISTANCE_METERS)
            conf = 0.5 + 0.5 * margin * min(t_tip.confidence, i_tip.confidence)
            return GestureType.PINCH, quantize_coord(conf, 6)

    # Step 2: POINT (Index EXTENDED, Middle/Ring/Pinky FOLDED)
    if (
        finger_states.index == FingerState.EXTENDED
        and finger_states.middle == FingerState.FOLDED
        and finger_states.ring == FingerState.FOLDED
        and finger_states.pinky == FingerState.FOLDED
    ):
        i_conf = i_tip.confidence if i_tip is not None else 0.8
        return GestureType.POINT, quantize_coord(i_conf, 6)

    # Step 3: TWO_FINGER (Index EXTENDED, Middle EXTENDED, Ring/Pinky FOLDED)
    if (
        finger_states.index == FingerState.EXTENDED
        and finger_states.middle == FingerState.EXTENDED
        and finger_states.ring == FingerState.FOLDED
        and finger_states.pinky == FingerState.FOLDED
    ):
        m_tip = landmarks.get(12)
        i_conf = i_tip.confidence if i_tip is not None else 0.8
        m_conf = m_tip.confidence if m_tip is not None else 0.8
        return GestureType.TWO_FINGER, quantize_coord((i_conf + m_conf) / 2.0, 6)

    # Step 4 & 5: THUMB_UP / THUMB_DOWN (Thumb EXTENDED, all others FOLDED)
    if (
        finger_states.thumb == FingerState.EXTENDED
        and finger_states.index == FingerState.FOLDED
        and finger_states.middle == FingerState.FOLDED
        and finger_states.ring == FingerState.FOLDED
        and finger_states.pinky == FingerState.FOLDED
    ):
        t_mcp = landmarks.get(2)
        if t_tip is not None and t_mcp is not None:
            dy = t_tip.y - t_mcp.y  # +Y is UP in optical camera space
            if dy > 0.02:
                return GestureType.THUMB_UP, quantize_coord(t_tip.confidence, 6)
            elif dy < -0.02:
                return GestureType.THUMB_DOWN, quantize_coord(t_tip.confidence, 6)

    # Step 6: OPEN_PALM (at least 4 fingers EXTENDED)
    extended_count = sum(
        1
        for s in (
            finger_states.thumb,
            finger_states.index,
            finger_states.middle,
            finger_states.ring,
            finger_states.pinky,
        )
        if s == FingerState.EXTENDED
    )
    if extended_count >= 4:
        # Average confidence of extended landmarks
        conf = 0.75 + 0.05 * extended_count
        return GestureType.OPEN_PALM, quantize_coord(conf, 6)

    # Step 7: CLOSED_HAND (at most 1 finger EXTENDED)
    if extended_count <= 1:
        return GestureType.CLOSED_HAND, quantize_coord(0.85, 6)

    # Step 8: UNKNOWN
    return GestureType.UNKNOWN, 0.0
