# -*- coding: utf-8 -*-
"""M02 Audio Subsystem Bounded Temporal Acoustic Tracker.

Maintains at most 16 simultaneous sound source tracks with up to 32 historical observations,
enforcing TENTATIVE -> CONFIRMED (3 observations) -> COASTING (<= 5 frames) -> LOST -> RETIRED.
"""

import math
from typing import Optional

from holomed.audio.models import (
    AcousticDirection,
    AudioTrack,
    MAX_AUDIO_COASTING_FRAMES,
    MAX_AUDIO_TRACK_HISTORY,
    MAX_AUDIO_TRACKS,
    TRACK_CONFIRMATION_THRESHOLD,
    TrackState,
)


class TemporalAcousticTracker:
    """Deterministic, bounded temporal tracker for localized acoustic sources."""

    def __init__(self) -> None:
        self._tracks: dict[int, AudioTrack] = {}  # track_id -> AudioTrack
        self._next_track_id: int = 0

    @property
    def active_track_count(self) -> int:
        return len(self._tracks)

    def update(
        self,
        direction: Optional[AcousticDirection],
        timestamp_utc: str,
    ) -> tuple[AudioTrack, ...]:
        """Update tracker state with a new acoustic observation."""
        if direction is None:
            # All existing active tracks enter or advance coasting
            updated_tracks: dict[int, AudioTrack] = {}
            for t_id, track in self._tracks.items():
                new_coasting = track.coasting_frames + 1
                if new_coasting > MAX_AUDIO_COASTING_FRAMES:
                    # Expired: transition to RETIRED
                    continue
                new_state = TrackState.COASTING
                updated_tracks[t_id] = AudioTrack(
                    track_id=track.track_id,
                    state=new_state,
                    direction=track.direction,
                    observations_count=track.observations_count,
                    coasting_frames=new_coasting,
                    history=track.history,
                )
            self._tracks = updated_tracks
            return self._export_sorted_tracks()

        # Match observation against existing non-retired tracks
        best_track_id: Optional[int] = None
        min_angular_dist = 30.0  # 30 degree association gate

        # Sort candidate track IDs for deterministic evaluation order
        sorted_track_ids = sorted(self._tracks.keys())
        for t_id in sorted_track_ids:
            track = self._tracks[t_id]
            d_az = abs(track.direction.azimuth_deg - direction.azimuth_deg)
            if d_az > 180.0:
                d_az = 360.0 - d_az
            d_el = abs(track.direction.elevation_deg - direction.elevation_deg)
            dist = math.sqrt(d_az * d_az + d_el * d_el)

            if dist <= min_angular_dist:
                min_angular_dist = dist
                best_track_id = t_id

        updated_tracks = {}
        matched_track: Optional[AudioTrack] = None

        if best_track_id is not None:
            existing = self._tracks[best_track_id]
            new_obs_count = existing.observations_count + 1
            if existing.state == TrackState.TENTATIVE and new_obs_count >= TRACK_CONFIRMATION_THRESHOLD:
                new_state = TrackState.CONFIRMED
            elif existing.state == TrackState.COASTING:
                new_state = TrackState.CONFIRMED
            else:
                new_state = existing.state

            new_history_entry = (direction.azimuth_deg, direction.elevation_deg, timestamp_utc)
            new_history = (existing.history + (new_history_entry,))[-MAX_AUDIO_TRACK_HISTORY:]

            matched_track = AudioTrack(
                track_id=existing.track_id,
                state=new_state,
                direction=direction,
                observations_count=new_obs_count,
                coasting_frames=0,
                history=new_history,
            )
            updated_tracks[best_track_id] = matched_track
        else:
            # Unmatched observation: allocate new track if capacity allows
            if len(self._tracks) < MAX_AUDIO_TRACKS:
                new_id = self._allocate_track_id()
                new_history_entry = (direction.azimuth_deg, direction.elevation_deg, timestamp_utc)
                new_track = AudioTrack(
                    track_id=new_id,
                    state=TrackState.TENTATIVE,
                    direction=direction,
                    observations_count=1,
                    coasting_frames=0,
                    history=(new_history_entry,),
                )
                updated_tracks[new_id] = new_track
                matched_track = new_track

        # Handle unmatched existing tracks
        for t_id, track in self._tracks.items():
            if t_id == best_track_id:
                continue
            new_coasting = track.coasting_frames + 1
            if new_coasting > MAX_AUDIO_COASTING_FRAMES:
                # Expire track
                continue
            updated_tracks[t_id] = AudioTrack(
                track_id=track.track_id,
                state=TrackState.COASTING,
                direction=track.direction,
                observations_count=track.observations_count,
                coasting_frames=new_coasting,
                history=track.history,
            )

        self._tracks = updated_tracks
        return self._export_sorted_tracks()

    def reset(self) -> None:
        """Reset all tracks and ID generator."""
        self._tracks.clear()
        self._next_track_id = 0

    def _allocate_track_id(self) -> int:
        # Find lowest unused ID in range 0..MAX_AUDIO_TRACKS-1
        used = set(self._tracks.keys())
        for i in range(MAX_AUDIO_TRACKS):
            candidate = (self._next_track_id + i) % MAX_AUDIO_TRACKS
            if candidate not in used:
                self._next_track_id = (candidate + 1) % MAX_AUDIO_TRACKS
                return candidate
        return 0

    def _export_sorted_tracks(self) -> tuple[AudioTrack, ...]:
        return tuple(sorted(self._tracks.values(), key=lambda t: t.track_id))
