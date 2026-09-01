# -*- coding: utf-8 -*-
"""Unit tests for RecordingXREventSink and Capacity Boundaries."""

from __future__ import annotations

import pytest

from holomed.protocol.builders import create_event
from holomed.xr.events import RecordingXREventSink
from holomed.xr.exceptions import XRCapacityError
from holomed.xr.models import MAX_RECORDED_XR_EVENTS


def test_xr_event_sink_capacity_boundary() -> None:
    """Verify RecordingXREventSink accepts 1-1000 events and rejects event 1001."""
    sink = RecordingXREventSink(capacity=MAX_RECORDED_XR_EVENTS)

    for i in range(MAX_RECORDED_XR_EVENTS):
        env = create_event(
            message_name="xr.presentation.frame",
            source="xr_service",
            payload={"frame": i},
        )
        sink.record_event(env)

    assert sink.count == 1000
    assert sink.is_full is True

    # Event 1001 must raise XRCapacityError
    overflow_env = create_event(
        message_name="xr.presentation.frame",
        source="xr_service",
        payload={"frame": 1001},
    )
    with pytest.raises(XRCapacityError):
        sink.record_event(overflow_env)

    assert sink.count == 1000
    assert isinstance(sink.export_events(), tuple)
