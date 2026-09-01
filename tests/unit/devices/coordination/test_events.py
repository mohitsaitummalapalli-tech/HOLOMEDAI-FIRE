"""Tests for M00.8 coordination event sinks and capacity bounds."""

import pytest

from holomed.devices.coordination.events import (
    NullDeviceCoordinationEventSink,
    RecordingDeviceCoordinationEventSink,
)
from holomed.devices.coordination.exceptions import DeviceCoordinationCapacityError
from holomed.devices.coordination.models import MAX_RECORDED_COORDINATION_EVENTS
from holomed.protocol.builders import create_event


def test_null_event_sink():
    """Verify NullDeviceCoordinationEventSink discards events without error."""
    sink = NullDeviceCoordinationEventSink()
    envelope = create_event("test.event", "source")
    sink.emit(envelope)


def test_recording_event_sink_capacity_bounds():
    """Verify ATK-19 & ATK-20: 1000 events succeed, 1001st fails without mutation."""
    sink = RecordingDeviceCoordinationEventSink()

    # 1..1000 succeed
    for i in range(MAX_RECORDED_COORDINATION_EVENTS):
        sink.emit(create_event(f"test.event.{i}", "source"))

    assert len(sink) == 1000
    assert len(sink.events) == 1000

    # 1001st fails
    with pytest.raises(DeviceCoordinationCapacityError):
        sink.emit(create_event("test.overflow", "source"))

    # Buffer remains unmutated at 1000
    assert len(sink) == 1000
    assert len(sink.events) == 1000


def test_recording_event_sink_clear():
    """Verify clear() resets the event sink."""
    sink = RecordingDeviceCoordinationEventSink()
    sink.emit(create_event("test.event", "source"))
    assert len(sink) == 1
    sink.clear()
    assert len(sink) == 0


def test_recording_event_sink_immutability():
    """Verify ATK-16: events property returns an immutable tuple."""
    sink = RecordingDeviceCoordinationEventSink()
    sink.emit(create_event("test.event", "source"))
    events = sink.events
    assert isinstance(events, tuple)
    with pytest.raises(AttributeError):
        events.append(create_event("test.append", "source"))  # type: ignore
