# -*- coding: utf-8 -*-
"""Unit tests for RecordingPlatformEventSink Bounding and Capacity."""

from __future__ import annotations

import pytest

from holomed.platform.events import RecordingPlatformEventSink
from holomed.platform.exceptions import PlatformCapacityError
from holomed.platform.models import MAX_RECORDED_PLATFORM_EVENTS
from holomed.protocol.builders import create_event


def test_platform_event_sink_capacity_boundary() -> None:
    """Verify event sink accepts 1000 events and rejects event 1001 with PlatformCapacityError."""
    sink = RecordingPlatformEventSink(capacity=MAX_RECORDED_PLATFORM_EVENTS)

    for i in range(MAX_RECORDED_PLATFORM_EVENTS):
        env = create_event(
            message_name="platform.cycle.completed",
            source="platform_service",
            payload={"cycle_idx": i},
        )
        sink.record_event(env)

    assert sink.count == 1000
    assert sink.is_full is True

    overflow = create_event(
        message_name="platform.cycle.completed",
        source="platform_service",
        payload={"cycle_idx": 1001},
    )
    with pytest.raises(PlatformCapacityError):
        sink.record_event(overflow)

    assert sink.count == 1000
    assert isinstance(sink.export_events(), tuple)
