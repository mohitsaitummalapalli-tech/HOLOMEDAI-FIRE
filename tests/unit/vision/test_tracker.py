"""Unit and lifecycle tests for TemporalLandmarkTracker."""

from __future__ import annotations

import pytest

from holomed.vision.models import (
    SpatialLandmark,
    SpatialPose,
    TrackState,
)
from holomed.vision.tracker import TemporalLandmarkTracker


def _make_dummy_pose(tx: float, ty: float, tz: float) -> SpatialPose:
    return SpatialPose(
        tx=tx,
        ty=ty,
        tz=tz,
        qw=1.0,
        qx=0.0,
        qy=0.0,
        qz=0.0,
        source_frame="cam",
        target_frame="world",
        timestamp_utc="2026-09-01T08:00:00.000000Z",
    )


def test_track_lifecycle_tentative_to_confirmed() -> None:
    """A new track starts TENTATIVE, confirms after 3 consecutive frames, and maintains history."""
    tracker = TemporalLandmarkTracker()
    lm = SpatialLandmark(0, "p0", 0.5, 0.5, depth_m=1.0, confidence=1.0)

    # Frame 1: creates TENTATIVE track
    pose1 = _make_dummy_pose(0.1, 0.2, 1.0)
    tracks1 = tracker.update([lm], pose1, "2026-09-01T08:00:00.000000Z")
    assert len(tracks1) == 1
    assert tracks1[0].state == TrackState.TENTATIVE
    assert tracks1[0].consecutive_detections == 1

    # Frame 2: still TENTATIVE
    pose2 = _make_dummy_pose(0.11, 0.21, 1.0)
    tracks2 = tracker.update([lm], pose2, "2026-09-01T08:00:01.000000Z")
    assert len(tracks2) == 1
    assert tracks2[0].state == TrackState.TENTATIVE
    assert tracks2[0].consecutive_detections == 2

    # Frame 3: promotes to CONFIRMED
    pose3 = _make_dummy_pose(0.12, 0.22, 1.0)
    tracks3 = tracker.update([lm], pose3, "2026-09-01T08:00:02.000000Z")
    assert len(tracks3) == 1
    assert tracks3[0].state == TrackState.CONFIRMED
    assert tracks3[0].consecutive_detections == 3
    assert len(tracks3[0].history) == 3


def test_track_coasting_and_retirement() -> None:
    """When an entity is unobserved, track coasts for 5 frames, then is pruned/retired."""
    tracker = TemporalLandmarkTracker()
    lm = SpatialLandmark(0, "p0", 0.5, 0.5, depth_m=1.0, confidence=1.0)
    pose = _make_dummy_pose(0.0, 0.0, 1.0)

    # Create track
    tracker.update([lm], pose, "t0")
    tracker.update([lm], pose, "t1")
    tracks = tracker.update([lm], pose, "t2")
    assert tracks[0].state == TrackState.CONFIRMED

    # Frames 4-8: missing observation -> COASTING
    for i in range(5):
        tracks = tracker.update([], None, f"t_coast_{i}")
        assert len(tracks) == 1
        assert tracks[0].state == TrackState.COASTING
        assert tracks[0].coasting_frames == i + 1

    # Frame 9: 6th unobserved frame -> pruned
    tracks_after = tracker.update([], None, "t_retire")
    assert len(tracks_after) == 0
