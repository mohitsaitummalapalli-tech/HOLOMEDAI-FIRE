# -*- coding: utf-8 -*-
"""Adversarial and lifecycle tests for TemporalAcousticTracker."""

from holomed.audio.models import AcousticDirection, TrackState
from holomed.audio.tracker import TemporalAcousticTracker


def test_track_confirmation_after_three_observations() -> None:
    """Verify track state machine transitions TENTATIVE -> CONFIRMED after 3 observations."""
    tracker = TemporalAcousticTracker()
    dir1 = AcousticDirection(azimuth_deg=10.0, elevation_deg=0.0, confidence=0.9)

    # Observation 1: TENTATIVE
    tracks = tracker.update(dir1, "2026-09-01T00:00:00Z")
    assert len(tracks) == 1
    assert tracks[0].state == TrackState.TENTATIVE
    assert tracks[0].observations_count == 1

    # Observation 2: TENTATIVE
    tracks = tracker.update(dir1, "2026-09-01T00:00:01Z")
    assert tracks[0].state == TrackState.TENTATIVE
    assert tracks[0].observations_count == 2

    # Observation 3: CONFIRMED
    tracks = tracker.update(dir1, "2026-09-01T00:00:02Z")
    assert tracks[0].state == TrackState.CONFIRMED
    assert tracks[0].observations_count == 3


def test_track_coasting_limit_five_frames() -> None:
    """Verify CONFIRMED track coasts for exactly 5 frames and is retired on the 6th."""
    tracker = TemporalAcousticTracker()
    dir1 = AcousticDirection(azimuth_deg=0.0, elevation_deg=0.0, confidence=0.9)

    # Confirm track (3 observations)
    tracker.update(dir1, "t0")
    tracker.update(dir1, "t1")
    tracks = tracker.update(dir1, "t2")
    assert tracks[0].state == TrackState.CONFIRMED

    # 5 Coasting frames
    for i in range(5):
        tracks = tracker.update(None, f"t_coast_{i}")
        assert len(tracks) == 1
        assert tracks[0].state == TrackState.COASTING
        assert tracks[0].coasting_frames == i + 1

    # 6th frame: retired / purged
    tracks = tracker.update(None, "t_coast_5")
    assert len(tracks) == 0


def test_track_table_capacity_bound_sixteen() -> None:
    """Verify table capacity cannot exceed MAX_AUDIO_TRACKS = 16."""
    tracker = TemporalAcousticTracker()

    # Introduce 20 distinct acoustic angles separated by > 30 degrees
    for i in range(20):
        # Azimuths: -150, -120, -90, -60, -30, 0, 30, 60, 90, 120, 150, etc.
        az = -170.0 + (i * 35.0)
        if az > 180.0:
            az = az - 360.0
        d = AcousticDirection(azimuth_deg=az, elevation_deg=0.0, confidence=0.8)
        tracker.update(d, f"t_{i}")

    assert tracker.active_track_count <= 16


def test_track_history_length_bound_thirtytwo() -> None:
    """Verify track history does not exceed 32 entries."""
    tracker = TemporalAcousticTracker()
    dir1 = AcousticDirection(azimuth_deg=20.0, elevation_deg=10.0, confidence=0.9)

    for i in range(50):
        tracks = tracker.update(dir1, f"t_{i}")

    assert len(tracks) == 1
    assert len(tracks[0].history) == 32
