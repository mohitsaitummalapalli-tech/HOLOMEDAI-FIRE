"""Deterministic temporal entity and landmark tracker for M01."""

from __future__ import annotations

import math
from typing import Optional, Sequence

from holomed.vision.exceptions import VisionCapacityError
from holomed.vision.models import (
    CONFIRMATION_DETECTION_THRESHOLD,
    MAX_COASTING_FRAMES,
    MAX_TRACK_HISTORY_LENGTH,
    MAX_TRACKED_ENTITIES,
    SpatialLandmark,
    SpatialPose,
    TrackState,
    TrackedEntity,
)


class _MutableTrack:
    """Internal mutable tracking container prior to immutable snapshot export."""

    __slots__ = (
        "entity_id",
        "state",
        "pose",
        "landmarks",
        "history",
        "consecutive_detections",
        "coasting_frames",
    )

    def __init__(self, entity_id: str, pose: Optional[SpatialPose], landmarks: Sequence[SpatialLandmark]) -> None:
        self.entity_id: str = entity_id
        self.state: TrackState = TrackState.TENTATIVE
        self.pose: Optional[SpatialPose] = pose
        self.landmarks: tuple[SpatialLandmark, ...] = tuple(landmarks)
        self.history: list[SpatialPose] = [pose] if pose else []
        self.consecutive_detections: int = 1
        self.coasting_frames: int = 0

    def to_immutable(self) -> TrackedEntity:
        return TrackedEntity(
            entity_id=self.entity_id,
            state=self.state,
            pose=self.pose,
            landmarks=self.landmarks,
            history=tuple(self.history),
            consecutive_detections=self.consecutive_detections,
            coasting_frames=self.coasting_frames,
        )


class TemporalLandmarkTracker:
    """Deterministic entity tracker maintaining temporal spatial trajectories.
    
    Enforces:
    - Bounded active entities (<= 32).
    - Bounded track history (<= 64 points).
    - Deterministic association with Euclidean gating threshold.
    - Deterministic tie-breaking on (entity_id ASC).
    - Lifecycle: TENTATIVE -> CONFIRMED -> COASTING -> LOST -> RETIRED.
    """

    def __init__(self) -> None:
        self._tracks: dict[str, _MutableTrack] = {}
        self._next_entity_idx: int = 1

    def update(
        self,
        landmarks: Sequence[SpatialLandmark],
        pose: Optional[SpatialPose],
        timestamp_utc: str,
    ) -> tuple[TrackedEntity, ...]:
        """Update active tracks using observed landmarks and estimated pose."""
        # 1. Age existing tracks to COASTING if unobserved
        if pose is None or len(landmarks) == 0:
            for track in list(self._tracks.values()):
                track.coasting_frames += 1
                if track.coasting_frames > MAX_COASTING_FRAMES:
                    track.state = TrackState.RETIRED
                else:
                    track.state = TrackState.COASTING
            self._prune_retired()
            return self._export_sorted_tracks()

        # 2. Association step
        matched_track: Optional[_MutableTrack] = None
        min_dist = float("inf")
        gating_threshold = 0.35  # meters

        # Check existing non-retired tracks
        for track_id in sorted(self._tracks.keys()):
            track = self._tracks[track_id]
            if track.state == TrackState.RETIRED or track.pose is None:
                continue
            dist = math.sqrt(
                (track.pose.tx - pose.tx) ** 2
                + (track.pose.ty - pose.ty) ** 2
                + (track.pose.tz - pose.tz) ** 2
            )
            if dist < min_dist and dist <= gating_threshold:
                min_dist = dist
                matched_track = track

        if matched_track is not None:
            # Update matched track
            matched_track.pose = pose
            matched_track.landmarks = tuple(landmarks)
            matched_track.coasting_frames = 0
            matched_track.consecutive_detections += 1
            if matched_track.consecutive_detections >= CONFIRMATION_DETECTION_THRESHOLD:
                matched_track.state = TrackState.CONFIRMED

            matched_track.history.append(pose)
            if len(matched_track.history) > MAX_TRACK_HISTORY_LENGTH:
                matched_track.history.pop(0)
        else:
            # Create new TENTATIVE track if capacity permits
            if len(self._tracks) >= MAX_TRACKED_ENTITIES:
                # Evict oldest retired or coasting track
                evicted = self._evict_oldest_non_confirmed()
                if not evicted:
                    # Drop new tentative track
                    self._prune_retired()
                    return self._export_sorted_tracks()

            new_id = f"entity_{self._next_entity_idx:04d}"
            self._next_entity_idx += 1
            new_track = _MutableTrack(new_id, pose, landmarks)
            self._tracks[new_id] = new_track
            matched_track = new_track

        # 3. Mark unmatched tracks as COASTING
        for track_id, track in self._tracks.items():
            if track is not matched_track and track.state != TrackState.RETIRED:
                track.coasting_frames += 1
                if track.coasting_frames > MAX_COASTING_FRAMES:
                    track.state = TrackState.RETIRED
                else:
                    track.state = TrackState.COASTING

        self._prune_retired()
        return self._export_sorted_tracks()

    def reset(self) -> None:
        """Reset all tracking tables."""
        self._tracks.clear()
        self._next_entity_idx = 1

    def _prune_retired(self) -> None:
        retired = [tid for tid, trk in self._tracks.items() if trk.state == TrackState.RETIRED]
        for tid in retired:
            del self._tracks[tid]

    def _evict_oldest_non_confirmed(self) -> bool:
        for tid in sorted(self._tracks.keys()):
            if self._tracks[tid].state in (TrackState.COASTING, TrackState.LOST, TrackState.TENTATIVE):
                del self._tracks[tid]
                return True
        return False

    def _export_sorted_tracks(self) -> tuple[TrackedEntity, ...]:
        sorted_keys = sorted(self._tracks.keys())
        return tuple(self._tracks[k].to_immutable() for k in sorted_keys)
