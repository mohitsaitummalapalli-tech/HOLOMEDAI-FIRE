# -*- coding: utf-8 -*-
"""Unit Tests for RecordingPersistenceEventSink."""

from __future__ import annotations

import pytest

from holomed.persistence.events import RecordingPersistenceEventSink
from holomed.persistence.exceptions import PersistenceCapacityError
from holomed.persistence.models import MAX_RECORDED_PERSISTENCE_EVENTS
from holomed.protocol.builders import create_event


def test_event_sink_capacity_boundary() -> None:
    """Verify sink accepts 1000 events and raises PersistenceCapacityError on 1001."""
    sink = RecordingPersistenceEventSink(capacity=MAX_RECORDED_PERSISTENCE_EVENTS)

    for i in range(MAX_RECORDED_PERSISTENCE_EVENTS):
        env = create_event("persistence.cycle.recorded", "persistence_service", payload={"idx": i})
        sink.record_event(env)

    assert sink.count == 1000
    assert sink.is_full is True

    overflow = create_event("persistence.cycle.recorded", "persistence_service", payload={"idx": 1001})
    with pytest.raises(PersistenceCapacityError):
        sink.record_event(overflow)

    assert sink.count == 1000
    assert isinstance(sink.export_events(), tuple)
