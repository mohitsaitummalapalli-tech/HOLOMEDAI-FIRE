# -*- coding: utf-8 -*-
"""Unit tests for RecordingAnatomyEventSink Capacity and Bounding."""

from __future__ import annotations

import pytest

from holomed.anatomy.events import RecordingAnatomyEventSink
from holomed.anatomy.exceptions import AnatomyCapacityError
from holomed.anatomy.models import MAX_RECORDED_ANATOMY_EVENTS
from holomed.protocol.builders import create_event


def test_anatomy_event_sink_capacity_boundary() -> None:
    """Verify RecordingAnatomyEventSink accepts 1-1000 events and strictly rejects event 1001."""
    sink = RecordingAnatomyEventSink(capacity=MAX_RECORDED_ANATOMY_EVENTS)

    for i in range(MAX_RECORDED_ANATOMY_EVENTS):
        env = create_event(
            message_name="anatomy.simulation.stepped",
            source="anatomy_service",
            payload={"step": i},
        )
        sink.record_event(env)

    assert sink.count == 1000
    assert sink.is_full is True

    # Event 1001 must raise AnatomyCapacityError
    overflow_env = create_event(
        message_name="anatomy.simulation.stepped",
        source="anatomy_service",
        payload={"step": 1001},
    )
    with pytest.raises(AnatomyCapacityError):
        sink.record_event(overflow_env)

    # History remains exactly 1000
    assert sink.count == 1000
    exported = sink.export_events()
    assert isinstance(exported, tuple)
    assert len(exported) == 1000
