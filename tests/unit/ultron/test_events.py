# -*- coding: utf-8 -*-
"""Unit tests for Ultron Event Sink and Protocol Emitted Events."""

from __future__ import annotations

import pytest

from holomed.protocol.builders import create_event
from holomed.ultron.events import RecordingUltronEventSink
from holomed.ultron.exceptions import UltronCapacityError
from holomed.ultron.models import MAX_RECORDED_ULTRON_EVENTS


def test_event_sink_capacity_boundary() -> None:
    """Verify event sink accepts 1-1000 events and strictly rejects event 1001 with UltronCapacityError."""
    sink = RecordingUltronEventSink(capacity=MAX_RECORDED_ULTRON_EVENTS)

    for i in range(MAX_RECORDED_ULTRON_EVENTS):
        env = create_event(
            message_name="ultron.observation.accepted",
            source="ultron_service",
            payload={"index": i},
        )
        sink.record_event(env)

    assert sink.count == 1000
    assert sink.is_full is True

    # 1001st event must raise UltronCapacityError
    overflow_env = create_event(
        message_name="ultron.observation.accepted",
        source="ultron_service",
        payload={"index": 1001},
    )
    with pytest.raises(UltronCapacityError):
        sink.record_event(overflow_env)

    # History remains exactly 1000
    assert sink.count == 1000
    exported = sink.export_events()
    assert isinstance(exported, tuple)
    assert len(exported) == 1000
