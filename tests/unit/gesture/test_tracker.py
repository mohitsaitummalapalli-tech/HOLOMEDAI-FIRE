# -*- coding: utf-8 -*-
"""Unit tests for deterministic hand tracking, lifecycle, and capacity limits."""

from __future__ import annotations

import pytest

from holomed.gesture.models import (
    HandTrackState,
    Handedness,
    MAX_HAND_COASTING_FRAMES,
    MAX_HAND_HISTORY,
    MAX_TRACKED_HANDS,
    unproject_spatial_landmark,
)
from holomed.gesture.tracker import HandTracker
from holomed.vision.models import SpatialLandmark


def test_tracker_confirmation_three_frames(open_hand_landmarks: tuple[SpatialLandmark, ...]) -> None:
    """Verify hand track requires 3 consecutive detections to become CONFIRMED."""
    tracker = HandTracker()
    lms_3d = [unproject_spatial_landmark(lm) for lm in open_hand_landmarks]

    # Frame 1: TENTATIVE
    tracks, tid = tracker.update(lms_3d, Handedness.RIGHT)
    assert len(tracks) == 1
    assert tracks[0].state == HandTrackState.TENTATIVE
    assert tracks[0].consecutive_detections == 1

    # Frame 2: TENTATIVE
    tracks, _ = tracker.update(lms_3d, Handedness.RIGHT)
    assert tracks[0].state == HandTrackState.TENTATIVE
    assert tracks[0].consecutive_detections == 2

    # Frame 3: CONFIRMED
    tracks, _ = tracker.update(lms_3d, Handedness.RIGHT)
    assert tracks[0].state == HandTrackState.CONFIRMED
    assert tracks[0].consecutive_detections == 3


def test_tracker_coasting_and_retirement(open_hand_landmarks: tuple[SpatialLandmark, ...]) -> None:
    """Verify track coasts for 5 frames when observation missing and retires on 6th."""
    tracker = HandTracker()
    lms_3d = [unproject_spatial_landmark(lm) for lm in open_hand_landmarks]

    # Establish CONFIRMED track
    for _ in range(3):
        tracker.update(lms_3d, Handedness.RIGHT)

    # Coasting frames 1 through 5
    for c in range(1, MAX_HAND_COASTING_FRAMES + 1):
        tracks, _ = tracker.update(None, Handedness.RIGHT)
        assert len(tracks) == 1
        assert tracks[0].state == HandTrackState.COASTING
        assert tracks[0].coasting_frames == c

    # 6th missing frame -> RETIRED and pruned
    tracks, _ = tracker.update(None, Handedness.RIGHT)
    assert len(tracks) == 0


def test_tracker_reactivation_from_coasting(open_hand_landmarks: tuple[SpatialLandmark, ...]) -> None:
    """Verify coasting track reactivates to CONFIRMED on new observation."""
    tracker = HandTracker()
    lms_3d = [unproject_spatial_landmark(lm) for lm in open_hand_landmarks]

    # Confirm
    for _ in range(3):
        tracker.update(lms_3d, Handedness.RIGHT)

    # Coast 2 frames
    tracker.update(None, Handedness.RIGHT)
    tracker.update(None, Handedness.RIGHT)
    tracks = tracker.export_sorted_tracks()
    assert tracks[0].state == HandTrackState.COASTING

    # Ingest observation -> Reactivated
    tracks, tid = tracker.update(lms_3d, Handedness.RIGHT)
    assert tracks[0].state == HandTrackState.CONFIRMED
    assert tracks[0].coasting_frames == 0


def test_tracker_eight_hand_capacity_limit(open_hand_landmarks: tuple[SpatialLandmark, ...]) -> None:
    """Verify MAX_TRACKED_HANDS = 8 and eviction of non-confirmed tracks."""
    tracker = HandTracker()

    # Create 8 confirmed tracks separated spatially (> 0.35m)
    for i in range(MAX_TRACKED_HANDS):
        # Shift wrist depth position by 0.5m each so they don't associate
        shifted = []
        for lm in open_hand_landmarks:
            shifted.append(SpatialLandmark(lm.landmark_id, lm.name, lm.u, lm.v, lm.depth_m + 0.5 * i, lm.confidence))
        s_3d = [unproject_spatial_landmark(lm) for lm in shifted]
        for _ in range(3):
            tracker.update(s_3d, Handedness.RIGHT)

    tracks = tracker.export_sorted_tracks()
    assert len(tracks) == 8
    assert all(t.state == HandTrackState.CONFIRMED for t in tracks)

    # 9th hand arrives: all 8 are CONFIRMED, so 9th hand is dropped
    extra = []
    for lm in open_hand_landmarks:
        extra.append(SpatialLandmark(lm.landmark_id, lm.name, lm.u, lm.v, lm.depth_m + 5.0, lm.confidence))
    extra_3d = [unproject_spatial_landmark(lm) for lm in extra]
    tracks, new_id = tracker.update(extra_3d, Handedness.RIGHT)
    assert len(tracks) == 8
    assert new_id is None


def test_tracker_history_bounded_32(open_hand_landmarks: tuple[SpatialLandmark, ...]) -> None:
    """Verify wrist trajectory history is strictly bounded at MAX_HAND_HISTORY (32)."""
    tracker = HandTracker()
    lms_3d = [unproject_spatial_landmark(lm) for lm in open_hand_landmarks]

    # Ingest 40 frames
    for _ in range(40):
        tracker.update(lms_3d, Handedness.RIGHT)

    tracks = tracker.export_sorted_tracks()
    assert len(tracks[0].wrist_history) == MAX_HAND_HISTORY
