# -*- coding: utf-8 -*-
"""Deterministic temporal hand trajectory tracker for M03."""

from __future__ import annotations

import math
from typing import Optional, Sequence

from holomed.gesture.models import (
    HAND_ASSOCIATION_THRESHOLD_METERS,
    HAND_CONFIRMATION_FRAMES,
    HandTrack,
    HandTrackState,
    Handedness,
    HandLandmark3D,
    MAX_HAND_COASTING_FRAMES,
    MAX_HAND_HISTORY,
    MAX_TRACKED_HANDS,
)


class _MutableHandTrack:
    """Internal mutable tracking container prior to immutable snapshot export."""

    __slots__ = (
        "track_id",
        "state",
        "handedness",
        "landmarks_3d",
        "wrist_history",
        "consecutive_detections",
        "coasting_frames",
    )

    def __init__(
        self,
        track_id: str,
        handedness: Handedness,
        landmarks_3d: Sequence[HandLandmark3D],
    ) -> None:
        self.track_id: str = track_id
        self.state: HandTrackState = HandTrackState.TENTATIVE
        self.handedness: Handedness = handedness
        self.landmarks_3d: tuple[HandLandmark3D, ...] = tuple(landmarks_3d)
        w = next((lm for lm in landmarks_3d if lm.landmark_id == 0), None)
        self.wrist_history: list[tuple[float, float, float]] = [(w.x, w.y, w.z)] if w else []
        self.consecutive_detections: int = 1
        self.coasting_frames: int = 0

    def to_immutable(self) -> HandTrack:
        return HandTrack(
            track_id=self.track_id,
            state=self.state,
            handedness=self.handedness,
            landmarks_3d=self.landmarks_3d,
            wrist_history=tuple(self.wrist_history),
            consecutive_detections=self.consecutive_detections,
            coasting_frames=self.coasting_frames,
        )


class HandTracker:
    """Deterministic hand tracker maintaining temporal spatial trajectories.

    Enforces:
    - Bounded active tracks (<= 8).
    - Bounded track history (<= 32 points).
    - Deterministic association with Euclidean gating threshold (0.35m).
    - Deterministic tie-breaking on (track_id ASC).
    - Lifecycle: TENTATIVE -> CONFIRMED -> COASTING -> LOST -> RETIRED.
    """

    def __init__(self) -> None:
        self._tracks: dict[str, _MutableHandTrack] = {}
        self._next_track_idx: int = 1

    def update(
        self,
        observed_landmarks: Optional[Sequence[HandLandmark3D]],
        handedness: Handedness,
    ) -> tuple[tuple[HandTrack, ...], Optional[str]]:
        """Update active tracks. Returns (active_tracks, matched_or_created_track_id)."""
        # Case 1: Missing or empty observation -> age all tracks
        if observed_landmarks is None or len(observed_landmarks) == 0:
            for track in list(self._tracks.values()):
                track.coasting_frames += 1
                if track.coasting_frames > MAX_HAND_COASTING_FRAMES:
                    track.state = HandTrackState.RETIRED
                else:
                    track.state = HandTrackState.COASTING
            self._prune_retired()
            return self.export_sorted_tracks(), None

        obs_wrist = next((lm for lm in observed_landmarks if lm.landmark_id == 0), None)
        if obs_wrist is None:
            return self.export_sorted_tracks(), None

        # Step 2: Association
        matched_track: Optional[_MutableHandTrack] = None
        min_dist = float("inf")

        for track_id in sorted(self._tracks.keys()):
            track = self._tracks[track_id]
            if track.state == HandTrackState.RETIRED or not track.wrist_history:
                continue

            # Handedness compatibility check
            if (
                track.handedness != Handedness.UNKNOWN
                and handedness != Handedness.UNKNOWN
                and track.handedness != handedness
            ):
                continue

            last_wrist = track.wrist_history[-1]
            dist = math.sqrt(
                (last_wrist[0] - obs_wrist.x) ** 2
                + (last_wrist[1] - obs_wrist.y) ** 2
                + (last_wrist[2] - obs_wrist.z) ** 2
            )
            if dist < min_dist and dist <= HAND_ASSOCIATION_THRESHOLD_METERS:
                min_dist = dist
                matched_track = track

        matched_id: Optional[str] = None
        if matched_track is not None:
            # Update matched track
            matched_track.landmarks_3d = tuple(observed_landmarks)
            matched_track.coasting_frames = 0
            matched_track.consecutive_detections += 1
            if matched_track.handedness == Handedness.UNKNOWN and handedness != Handedness.UNKNOWN:
                matched_track.handedness = handedness

            if matched_track.consecutive_detections >= HAND_CONFIRMATION_FRAMES:
                matched_track.state = HandTrackState.CONFIRMED

            matched_track.wrist_history.append((obs_wrist.x, obs_wrist.y, obs_wrist.z))
            if len(matched_track.wrist_history) > MAX_HAND_HISTORY:
                matched_track.wrist_history.pop(0)
            matched_id = matched_track.track_id
        else:
            # Create new TENTATIVE track if capacity permits
            if len(self._tracks) >= MAX_TRACKED_HANDS:
                evicted = self._evict_oldest_non_confirmed()
                if not evicted:
                    # Drop new tentative track
                    self._prune_retired()
                    return self.export_sorted_tracks(), None

            new_id = f"hand_{self._next_track_idx:04d}"
            self._next_track_idx += 1
            new_track = _MutableHandTrack(new_id, handedness, observed_landmarks)
            self._tracks[new_id] = new_track
            matched_track = new_track
            matched_id = new_id

        # Step 3: Age unmatched tracks only if observation was missing or age_unmatched is requested
        if observed_landmarks is None or len(observed_landmarks) == 0:
            for track_id, track in self._tracks.items():
                if track is not matched_track and track.state != HandTrackState.RETIRED:
                    track.coasting_frames += 1
                    if track.coasting_frames > MAX_HAND_COASTING_FRAMES:
                        track.state = HandTrackState.RETIRED
                    else:
                        track.state = HandTrackState.COASTING

        self._prune_retired()
        return self.export_sorted_tracks(), matched_id

    def reset(self) -> None:
        """Reset all tracking tables."""
        self._tracks.clear()
        self._next_track_idx = 1

    def _prune_retired(self) -> None:
        retired = [tid for tid, trk in self._tracks.items() if trk.state == HandTrackState.RETIRED]
        for tid in retired:
            del self._tracks[tid]

    def _evict_oldest_non_confirmed(self) -> bool:
        for tid in sorted(self._tracks.keys()):
            if self._tracks[tid].state in (
                HandTrackState.COASTING,
                HandTrackState.LOST,
                HandTrackState.TENTATIVE,
            ):
                del self._tracks[tid]
                return True
        return False

    def export_sorted_tracks(self) -> tuple[HandTrack, ...]:
        sorted_keys = sorted(self._tracks.keys())
        return tuple(self._tracks[k].to_immutable() for k in sorted_keys)
