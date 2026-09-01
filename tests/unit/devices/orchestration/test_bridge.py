"""Tests for DeviceCrossPlaneBridge."""

from types import MappingProxyType
import pytest

from holomed.devices.coordination.coordinator import DeviceCoordinationService
from holomed.devices.coordination.models import TelemetryStatus
from holomed.devices.orchestration.bridge import DeviceCrossPlaneBridge
from holomed.devices.orchestration.exceptions import DeviceOrchestrationCapacityError
from holomed.devices.orchestration.models import MAX_SAFE_INTEGER
from holomed.protocol.builders import create_event
from tests.unit.devices.orchestration.conftest import (
    DummyOrchestrationDevice,
    register_device,
)


def test_bridge_reentrancy_and_event_loop_prevention(device_subsystem_stack) -> None:
    bridge = DeviceCrossPlaneBridge(
        registry=device_subsystem_stack["registry"],
        coordination=device_subsystem_stack["coordination"],
        current_epoch_id=1,
    )

    # Simulate nested invocation by forcing _in_bridge_dispatch flag
    bridge._in_bridge_dispatch = True
    env = create_event("device.data.processed", "test_source", payload={"device_id": "d1", "sequence_number": 1})
    res = bridge.handle_event(env)
    assert res is False

    # Also verify rejection of device.coordination.* events (D166)
    bridge._in_bridge_dispatch = False
    coord_env = create_event("device.coordination.telemetry_updated", "device_coordination", payload={"device_id": "d1"})
    assert bridge.handle_event(coord_env) is False


def test_bridge_stale_event_rejected_on_physical_id_mismatch(device_subsystem_stack) -> None:
    dev = DummyOrchestrationDevice("cam_01", "usb://port_1")
    register_device(device_subsystem_stack["registry"], device_subsystem_stack["token"], dev)

    bridge = DeviceCrossPlaneBridge(
        registry=device_subsystem_stack["registry"],
        coordination=device_subsystem_stack["coordination"],
        current_epoch_id=1,
    )

    # Event carrying mismatched physical_id (D169)
    mismatched_env = create_event(
        "device.data.processed",
        "data_plane",
        payload={"device_id": "cam_01", "sequence_number": 1, "physical_id": "usb://stale_port_9"},
    )
    result = bridge.handle_event(mismatched_env)
    assert result is False
    stats = bridge.get_statistics()
    assert stats.identity_mismatches_rejected == 1
    assert stats.telemetry_updates_emitted == 0


def test_bridge_m007_payload_compatibility_and_resolution(device_subsystem_stack) -> None:
    dev = DummyOrchestrationDevice("cam_02", "usb://port_2")
    register_device(device_subsystem_stack["registry"], device_subsystem_stack["token"], dev)

    bridge = DeviceCrossPlaneBridge(
        registry=device_subsystem_stack["registry"],
        coordination=device_subsystem_stack["coordination"],
        current_epoch_id=1,
    )

    # M00.7 data event without physical_id (D176 fallback)
    valid_env = create_event(
        "device.data.processed",
        "data_plane",
        payload={"device_id": "cam_02", "sequence_number": 1, "kind": "observation"},
    )
    result = bridge.handle_event(valid_env)
    assert result is True
    stats = bridge.get_statistics()
    assert stats.telemetry_updates_emitted == 1
    assert stats.events_bridged == 1

    # Verify telemetry was updated in coordination service
    rec = device_subsystem_stack["coordination"].get_telemetry("cam_02")
    assert rec.processed_items == 1
    assert rec.last_sequence_number == 1
    assert rec.physical_id == "usb://port_2"


def test_bridge_counter_safe_integer_bounds(device_subsystem_stack) -> None:
    bridge = DeviceCrossPlaneBridge(
        registry=device_subsystem_stack["registry"],
        coordination=device_subsystem_stack["coordination"],
        current_epoch_id=1,
    )

    # Set counter to MAX_SAFE_INTEGER
    bridge._events_bridged = MAX_SAFE_INTEGER
    with pytest.raises(DeviceOrchestrationCapacityError):
        bridge._increment_counter("events_bridged")


def test_bridge_deregistered_event_retires_session(device_subsystem_stack) -> None:
    dev = DummyOrchestrationDevice("cam_03", "usb://port_3")
    register_device(device_subsystem_stack["registry"], device_subsystem_stack["token"], dev)

    bridge = DeviceCrossPlaneBridge(
        registry=device_subsystem_stack["registry"],
        coordination=device_subsystem_stack["coordination"],
        current_epoch_id=1,
    )

    # First update telemetry
    valid_env = create_event(
        "device.data.processed",
        "data_plane",
        payload={"device_id": "cam_03", "sequence_number": 1},
    )
    bridge.handle_event(valid_env)
    assert device_subsystem_stack["coordination"].get_telemetry("cam_03") is not None

    # Now simulate deregistration
    dereg_env = create_event("device.deregistered", "device_manager", payload={"device_id": "cam_03"})
    bridge.handle_event(dereg_env)

    # Querying retired telemetry should raise DeviceCoordinationDeviceNotFoundError
    with pytest.raises(Exception):
        device_subsystem_stack["coordination"].get_telemetry("cam_03")
