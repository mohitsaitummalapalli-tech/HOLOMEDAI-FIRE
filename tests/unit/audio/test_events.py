# -*- coding: utf-8 -*-
"""Adversarial and capacity tests for RecordingAudioEventSink (§45, §46)."""

import pytest

from holomed.audio.events import RecordingAudioEventSink
from holomed.audio.exceptions import AudioCapacityError
from holomed.protocol.builders import create_event


def test_event_sink_capacity_1000() -> None:
    """Assert sink holds up to 1000 events and rejects the 1001st with AudioCapacityError."""
    sink = RecordingAudioEventSink(capacity=1000)
    assert sink.count == 0

    for i in range(1000):
        ev = create_event("audio.chunk.accepted", "audio_service", payload={"seq": i})
        sink.record_event(ev)

    assert sink.count == 1000

    # 1001st event must raise AudioCapacityError
    ev_overflow = create_event("audio.chunk.accepted", "audio_service", payload={"seq": 1000})
    with pytest.raises(AudioCapacityError, match="capacity exceeded"):
        sink.record_event(ev_overflow)

    # Verify non-mutating rejection: count remains 1000
    assert sink.count == 1000
    events = sink.get_events()
    assert len(events) == 1000
    assert events[0].payload["seq"] == 0
    assert events[-1].payload["seq"] == 999


def test_event_sink_clear() -> None:
    """Assert clear() flushes recorded events."""
    sink = RecordingAudioEventSink()
    ev = create_event("audio.chunk.accepted", "audio_service", payload={"test": 1})
    sink.record_event(ev)
    assert sink.count == 1
    sink.clear()
    assert sink.count == 0
