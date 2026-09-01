# -*- coding: utf-8 -*-
"""Unit tests for gesture state machine transitions, cancellation, and deduplication."""

from __future__ import annotations

import pytest

from holomed.gesture.models import (
    GestureState,
    GestureType,
    MAX_GESTURE_EVENT_KEYS,
)
from holomed.gesture.state_machine import GestureStateMachine


def test_state_machine_confirmation_and_release_lifecycle() -> None:
    """Verify D205: IDLE -> CANDIDATE (2 frames) -> ACTIVE -> RELEASED (2 frames) -> IDLE."""
    events_emitted: list[tuple[str, dict]] = []

    def record_event(name: str, payload: dict) -> None:
        events_emitted.append((name, payload))

    sm = GestureStateMachine(event_emitter=record_event)
    tid = "hand_0001"

    # Frame 1: Detected POINT -> transitions to CANDIDATE (c=1)
    res = sm.update(tid, GestureType.POINT, 0.9, 1, 1, "2026-09-01T12:00:00.000000Z")
    assert res is not None
    assert res.state == GestureState.CANDIDATE.value
    assert len(events_emitted) == 0

    # Frame 2: Detected POINT -> confirms to ACTIVE (c=2)
    res = sm.update(tid, GestureType.POINT, 0.9, 2, 1, "2026-09-01T12:00:00.033000Z")
    assert res is not None
    assert res.state == GestureState.ACTIVE.value
    assert len(events_emitted) == 1
    assert events_emitted[0][0] == "gesture.activated"

    # Frame 3: Condition remains True -> remains ACTIVE
    res = sm.update(tid, GestureType.POINT, 0.9, 3, 1, "2026-09-01T12:00:00.066000Z")
    assert res.state == GestureState.ACTIVE.value
    assert len(events_emitted) == 1  # No duplicate event

    # Frame 4: Condition false (UNKNOWN) -> release frame 1 (still ACTIVE)
    res = sm.update(tid, GestureType.UNKNOWN, 0.0, 4, 1, "2026-09-01T12:00:00.100000Z")
    assert res.state == GestureState.ACTIVE.value
    assert len(events_emitted) == 1

    # Frame 5: Condition false (UNKNOWN) -> release frame 2 -> RELEASED
    res = sm.update(tid, GestureType.UNKNOWN, 0.0, 5, 1, "2026-09-01T12:00:00.133000Z")
    assert res.state == GestureState.RELEASED.value
    assert len(events_emitted) == 2
    assert events_emitted[1][0] == "gesture.released"

    # Frame 6: Next frame -> transitions back to IDLE
    res = sm.update(tid, GestureType.UNKNOWN, 0.0, 6, 1, "2026-09-01T12:00:00.166000Z")
    assert res is None  # IDLE returns None


def test_state_machine_candidate_cancellation() -> None:
    """Verify D205: CANDIDATE aborts to CANCELLED if condition fails before confirmation."""
    events_emitted: list[tuple[str, dict]] = []

    def record_event(name: str, payload: dict) -> None:
        events_emitted.append((name, payload))

    sm = GestureStateMachine(event_emitter=record_event)
    tid = "hand_0001"

    # Frame 1: Candidate
    res = sm.update(tid, GestureType.PINCH, 0.85, 1, 1, "2026-09-01T12:00:00.000000Z")
    assert res.state == GestureState.CANDIDATE.value

    # Frame 2: Abort
    res = sm.update(tid, GestureType.OPEN_PALM, 0.9, 2, 1, "2026-09-01T12:00:00.033000Z")
    assert len(events_emitted) == 1
    assert events_emitted[0][0] == "gesture.cancelled"


def test_state_machine_track_dropout_cancellation() -> None:
    """Verify track loss immediately cancels active gesture."""
    events_emitted: list[tuple[str, dict]] = []

    def record_event(name: str, payload: dict) -> None:
        events_emitted.append((name, payload))

    sm = GestureStateMachine(event_emitter=record_event)
    tid = "hand_0001"

    # Confirm active gesture
    sm.update(tid, GestureType.POINT, 0.9, 1, 1, "2026-09-01T12:00:00.000000Z")
    sm.update(tid, GestureType.POINT, 0.9, 2, 1, "2026-09-01T12:00:00.033000Z")
    assert len(events_emitted) == 1
    assert events_emitted[0][0] == "gesture.activated"

    # Hand track dropped
    sm.cancel_track_gestures(tid, sequence_number=3, epoch_id=1)
    assert len(events_emitted) == 2
    assert events_emitted[1][0] == "gesture.cancelled"
    assert events_emitted[1][1]["reason"] == "TRACK_DROPOUT"


def test_state_machine_event_deduplication_and_cache_cap() -> None:
    """Verify identical events are not re-emitted and identity cache bounded at 2048."""
    events_emitted: list[tuple[str, dict]] = []

    def record_event(name: str, payload: dict) -> None:
        events_emitted.append((name, payload))

    sm = GestureStateMachine(event_emitter=record_event)

    # Fill cache up to 2100 items
    for i in range(2100):
        key = (1, f"hand_{i % 5}", "POINT", i)
        sm._record_event_key(key)

    assert len(sm._event_identity_cache) == MAX_GESTURE_EVENT_KEYS
