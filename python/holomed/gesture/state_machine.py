# -*- coding: utf-8 -*-
"""Deterministic 5-state gesture state machine with event deduplication."""

from __future__ import annotations

from collections import OrderedDict
from typing import Callable, Optional

from holomed.gesture.models import (
    GESTURE_CONFIRMATION_FRAMES,
    GESTURE_RELEASE_FRAMES,
    GestureResult,
    GestureState,
    GestureType,
    MAX_GESTURE_EVENT_KEYS,
)


class _TrackGestureState:
    """Internal mutable tracking for an individual hand track's gesture."""

    __slots__ = (
        "active_type",
        "state",
        "candidate_frames",
        "release_frames",
        "last_confidence",
    )

    def __init__(self) -> None:
        self.active_type: GestureType = GestureType.UNKNOWN
        self.state: GestureState = GestureState.IDLE
        self.candidate_frames: int = 0
        self.release_frames: int = 0
        self.last_confidence: float = 0.0


class GestureStateMachine:
    """Authoritative gesture state machine with FIFO deduplication and 5-state transitions."""

    def __init__(
        self,
        event_emitter: Optional[Callable[[str, dict], None]] = None,
    ) -> None:
        self._states: dict[str, _TrackGestureState] = {}
        self._event_emitter: Optional[Callable[[str, dict], None]] = event_emitter
        self._event_identity_cache: OrderedDict[tuple[int, str, str, int], bool] = OrderedDict()

    def update(
        self,
        hand_track_id: str,
        detected_gesture: GestureType,
        confidence: float,
        sequence_number: int,
        epoch_id: int,
        timestamp_utc: str,
        interaction_point: Optional[tuple[float, float, float]] = None,
        interaction_direction: Optional[tuple[float, float, float]] = None,
    ) -> Optional[GestureResult]:
        """Update state machine for hand track and return active GestureResult if applicable."""
        if hand_track_id not in self._states:
            self._states[hand_track_id] = _TrackGestureState()

        gs = self._states[hand_track_id]
        prev_state = gs.state
        event_name_to_emit: Optional[str] = None

        if gs.state in (GestureState.RELEASED, GestureState.CANCELLED):
            # Terminal states exit to IDLE on the following frame before evaluation
            gs.state = GestureState.IDLE
            gs.candidate_frames = 0
            gs.release_frames = 0
            gs.active_type = GestureType.UNKNOWN

        if gs.state == GestureState.IDLE:
            if detected_gesture not in (GestureType.UNKNOWN, GestureType.CLOSED_HAND):
                gs.state = GestureState.CANDIDATE
                gs.active_type = detected_gesture
                gs.candidate_frames = 1
                gs.last_confidence = confidence
            elif detected_gesture == GestureType.CLOSED_HAND:
                # CLOSED_HAND transitions to candidate
                gs.state = GestureState.CANDIDATE
                gs.active_type = detected_gesture
                gs.candidate_frames = 1
                gs.last_confidence = confidence

        elif gs.state == GestureState.CANDIDATE:
            if detected_gesture == gs.active_type:
                gs.candidate_frames += 1
                gs.last_confidence = max(gs.last_confidence, confidence)
                if gs.candidate_frames >= GESTURE_CONFIRMATION_FRAMES:
                    gs.state = GestureState.ACTIVE
                    event_name_to_emit = "gesture.activated"
            else:
                gs.state = GestureState.CANCELLED
                gs.candidate_frames = 0
                event_name_to_emit = "gesture.cancelled"

        elif gs.state == GestureState.ACTIVE:
            if detected_gesture == gs.active_type:
                gs.release_frames = 0
                gs.last_confidence = confidence
            else:
                gs.release_frames += 1
                if gs.release_frames >= GESTURE_RELEASE_FRAMES:
                    gs.state = GestureState.RELEASED
                    event_name_to_emit = "gesture.released"

        # Event emission with deduplication
        if event_name_to_emit and self._event_emitter is not None:
            dedup_key = (epoch_id, hand_track_id, gs.active_type.value, sequence_number)
            if dedup_key not in self._event_identity_cache:
                self._record_event_key(dedup_key)
                self._event_emitter(
                    event_name_to_emit,
                    {
                        "hand_track_id": hand_track_id,
                        "gesture_type": gs.active_type.value,
                        "state": gs.state.value,
                        "sequence_number": sequence_number,
                        "epoch_id": epoch_id,
                    },
                )

        if gs.state in (GestureState.ACTIVE, GestureState.CANDIDATE, GestureState.RELEASED):
            return GestureResult(
                hand_track_id=hand_track_id,
                gesture_type=gs.active_type,
                state=gs.state.value,
                confidence=gs.last_confidence,
                timestamp_utc=timestamp_utc,
                sequence_number=sequence_number,
                epoch_id=epoch_id,
                interaction_point=interaction_point,
                interaction_direction=interaction_direction,
            )

        return None

    def cancel_track_gestures(
        self,
        hand_track_id: str,
        sequence_number: int,
        epoch_id: int,
    ) -> None:
        """Abort any active or candidate gesture when track is lost or retired."""
        if hand_track_id in self._states:
            gs = self._states[hand_track_id]
            if gs.state in (GestureState.ACTIVE, GestureState.CANDIDATE):
                gs.state = GestureState.CANCELLED
                if self._event_emitter is not None:
                    dedup_key = (epoch_id, hand_track_id, gs.active_type.value, sequence_number)
                    if dedup_key not in self._event_identity_cache:
                        self._record_event_key(dedup_key)
                        self._event_emitter(
                            "gesture.cancelled",
                            {
                                "hand_track_id": hand_track_id,
                                "gesture_type": gs.active_type.value,
                                "state": GestureState.CANCELLED.value,
                                "reason": "TRACK_DROPOUT",
                                "sequence_number": sequence_number,
                                "epoch_id": epoch_id,
                            },
                        )

    def prune_untracked(self, active_track_ids: set[str]) -> None:
        """Remove state entries for tracks that no longer exist."""
        stale = [tid for tid in self._states if tid not in active_track_ids]
        for tid in stale:
            del self._states[tid]

    def reset(self) -> None:
        """Reset all gesture states and clear deduplication cache."""
        self._states.clear()
        self._event_identity_cache.clear()

    def _record_event_key(self, key: tuple[int, str, str, int]) -> None:
        """Record key with bounded FIFO eviction at MAX_GESTURE_EVENT_KEYS."""
        if len(self._event_identity_cache) >= MAX_GESTURE_EVENT_KEYS:
            self._event_identity_cache.popitem(last=False)  # FIFO eviction
        self._event_identity_cache[key] = True
