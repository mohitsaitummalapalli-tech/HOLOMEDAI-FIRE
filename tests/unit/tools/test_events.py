# -*- coding: utf-8 -*-
"""Unit tests for RecordingToolEventSink Capacity and Bounding."""

from __future__ import annotations

import pytest

from holomed.protocol.builders import create_event
from holomed.tools.events import RecordingToolEventSink
from holomed.tools.exceptions import ToolCapacityError
from holomed.tools.models import MAX_RECORDED_TOOL_EVENTS


def test_tool_event_sink_capacity_boundary() -> None:
    """Verify RecordingToolEventSink accepts 1-1000 events and rejects event 1001."""
    sink = RecordingToolEventSink(capacity=MAX_RECORDED_TOOL_EVENTS)

    for i in range(MAX_RECORDED_TOOL_EVENTS):
        env = create_event(
            message_name="tools.invocation.completed",
            source="tool_service",
            payload={"inv": i},
        )
        sink.record_event(env)

    assert sink.count == 1000
    assert sink.is_full is True

    # Event 1001 must raise ToolCapacityError
    overflow_env = create_event(
        message_name="tools.invocation.completed",
        source="tool_service",
        payload={"inv": 1001},
    )
    with pytest.raises(ToolCapacityError):
        sink.record_event(overflow_env)

    assert sink.count == 1000
    assert isinstance(sink.export_events(), tuple)
