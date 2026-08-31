"""Unit tests for device event emission, sink capacity, and failure isolation."""

import pytest

from holomed.devices.exceptions import DeviceCapacityError, DeviceLifecycleError
from holomed.devices.interfaces import IDeviceEventSink, RecordingDeviceEventSink
from holomed.devices.manager import DeviceManager
from holomed.devices.models import MAX_RECORDED_EVENTS
from holomed.devices.simulated import SimulatedDevice
from holomed.protocol.models import MessageEnvelope
from tests.unit.devices.conftest import make_test_context


def test_recording_sink_boundary_and_clear() -> None:
    sink = RecordingDeviceEventSink()
    assert len(sink.events) == 0

    from holomed.protocol.builders import create_event
    envelope = create_event(
        message_name="device.registered",
        source="test",
        payload={},
    )

    # 1..1000 events succeed
    for _ in range(MAX_RECORDED_EVENTS):
        sink.emit(envelope)

    assert len(sink.events) == MAX_RECORDED_EVENTS

    # 1001st event raises DeviceCapacityError
    with pytest.raises(DeviceCapacityError, match="capacity exceeded"):
        sink.emit(envelope)

    # Failed insertion leaves buffer untouched at 1000
    assert len(sink.events) == MAX_RECORDED_EVENTS

    # Clear resets to empty
    sink.clear()
    assert len(sink.events) == 0


def test_event_sink_failure_isolation() -> None:
    class CrashingEventSink(IDeviceEventSink):
        def emit(self, envelope):
            raise ConnectionResetError("Broker socket closed")

    sink = CrashingEventSink()
    manager = DeviceManager(event_sink=sink)
    ctx = make_test_context(epoch_id=1)
    manager.initialize(ctx)
    manager.start()

    # Event emission crashes in sink, but device registration MUST NOT fail
    device = SimulatedDevice("cam1", "USB:1")
    manager.register_device(device)

    # Device successfully registered; manager increments sink error counter
    assert manager._registry.contains("cam1")
    assert manager._sink_errors_count == 1


def test_reentrancy_via_event_sink_rejected() -> None:
    reentrant_manager: list = []

    class ReentrantEventSink(IDeviceEventSink):
        def emit(self, envelope):
            # Attempt reentrant mutating call
            reentrant_manager[0].register_device(SimulatedDevice("reentrant_cam", "USB:reentrant"))

    sink = ReentrantEventSink()
    manager = DeviceManager(event_sink=sink)
    reentrant_manager.append(manager)

    ctx = make_test_context(epoch_id=1)
    manager.initialize(ctx)
    manager.start()

    # When register_device emits device.registered, sink attempts to re-enter register_device
    # The internal reentrancy guard catches this and logs the failure; main device registration completes
    manager.register_device(SimulatedDevice("main_cam", "USB:main"))
    assert manager._registry.contains("main_cam")
    assert not manager._registry.contains("reentrant_cam")
    assert manager._sink_errors_count == 1
