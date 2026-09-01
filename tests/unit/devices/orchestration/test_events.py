"""Tests for orchestration event sinks and capacity limits."""

import pytest

from holomed.devices.orchestration.events import (
    NullDeviceOrchestrationEventSink,
    RecordingDeviceOrchestrationEventSink,
)
from holomed.devices.orchestration.exceptions import DeviceOrchestrationCapacityError
from holomed.protocol.builders import create_event


def test_null_event_sink_behavior() -> None:
    sink = NullDeviceOrchestrationEventSink()
    assert bool(sink) is True
    env = create_event("device.orchestration.test", "src", payload={})
    sink.emit(env)  # safe no-op


def test_orchestrator_event_sink_capacity_boundary_at_1000() -> None:
    sink = RecordingDeviceOrchestrationEventSink(max_capacity=1000)
    assert len(sink) == 0
    assert bool(sink) is True

    # Emit exactly 1,000 events
    for i in range(1000):
        sink.emit(create_event("device.orchestration.event", "test", payload={"index": i}))

    assert len(sink) == 1000

    # Event 1001 must raise DeviceOrchestrationCapacityError (D172)
    with pytest.raises(DeviceOrchestrationCapacityError):
        sink.emit(create_event("device.orchestration.overflow", "test", payload={}))

    # Existing history must not be mutated
    assert len(sink) == 1000
    assert len(sink.events) == 1000
    assert isinstance(sink.events, tuple)


def test_recording_sink_clear() -> None:
    sink = RecordingDeviceOrchestrationEventSink()
    sink.emit(create_event("test.event", "src", payload={}))
    assert len(sink) == 1
    sink.clear()
    assert len(sink) == 0
