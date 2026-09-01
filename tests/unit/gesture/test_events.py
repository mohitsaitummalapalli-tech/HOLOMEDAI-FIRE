# -*- coding: utf-8 -*-
"""Unit tests for RecordingGestureEventSink bounded storage and capacity guarantees."""

from __future__ import annotations

import pytest

from holomed.gesture.events import RecordingGestureEventSink
from holomed.gesture.exceptions import GestureCapacityError
from holomed.gesture.models import MAX_RECORDED_GESTURE_EVENTS
from holomed.protocol.builders import create_event


def test_event_sink_capacity_boundary() -> None:
    """Verify events 1-1000 are recorded and event 1001 raises GestureCapacityError non-mutating."""
    sink = RecordingGestureEventSink(capacity=MAX_RECORDED_GESTURE_EVENTS)

    for i in range(MAX_RECORDED_GESTURE_EVENTS):
        env = create_event(
            message_name="gesture.activated",
            source="gesture_service",
            payload={"index": i},
        )
        sink.record_event(env)

    assert sink.count == MAX_RECORDED_GESTURE_EVENTS

    # 1001st event must raise GestureCapacityError
    over_env = create_event(
        message_name="gesture.activated",
        source="gesture_service",
        payload={"index": 1000},
    )
    with pytest.raises(GestureCapacityError, match="capacity exceeded"):
        sink.record_event(over_env)

    # Prior state remains unaffected
    assert sink.count == MAX_RECORDED_GESTURE_EVENTS


def test_event_sink_immutable_export_and_clear() -> None:
    """Verify get_events returns an immutable tuple and clear empties the sink."""
    sink = RecordingGestureEventSink()
    env = create_event(message_name="gesture.test", source="test", payload={})
    sink.record_event(env)

    exported = sink.get_events()
    assert isinstance(exported, tuple)
    assert len(exported) == 1

    sink.clear()
    assert sink.count == 0
    assert len(sink.get_events()) == 0
