"""Tests for M00.8 DeviceCoordinationService lifecycle, snapshots, and dispatcher routing."""

import pytest

from holomed.core.dispatcher import MessageDispatcher
from holomed.devices.coordination.coordinator import DeviceCoordinationService
from holomed.devices.coordination.events import RecordingDeviceCoordinationEventSink
from holomed.devices.coordination.exceptions import (
    DeviceCoordinationDeviceNotFoundError,
    DeviceCoordinationLifecycleError,
    DeviceCoordinationShutdownError,
)
from holomed.devices.interfaces import RegistryAuthorityToken
from holomed.devices.models import DeviceState, DeviceType
from holomed.devices.registry import DeviceRegistry
from holomed.protocol.builders import create_command, create_query
from holomed.protocol.models import MessageType
from holomed.runtime.models import HealthStatus
from holomed.runtime.service import ServiceState
from tests.unit.devices.coordination.conftest import (
    DummyCoordinationDevice,
    make_test_context,
    register_device,
)


def test_coordinator_lifecycle_and_resources(device_registry: DeviceRegistry):
    """Verify IService contract, 3 structural resources, and dependencies."""
    coordinator = DeviceCoordinationService(device_registry)
    assert coordinator.name == "device_coordination"
    assert coordinator.dependencies == ("device_manager",)  # D157

    context = make_test_context(epoch_id=1)
    coordinator.initialize(context)

    # Verify exactly 3 structural handles acquired
    res = coordinator.resources
    assert len(res.outstanding_handles) == 3
    handle_ids = {h.resource_id for h in res.outstanding_handles}
    assert handle_ids == {"coordination.snapshot", "coordination.telemetry", "coordination.events"}

    # Start
    coordinator.start()
    assert coordinator._state == ServiceState.STARTED

    # D156 & ATK-23: Double start raises DeviceCoordinationLifecycleError
    with pytest.raises(DeviceCoordinationLifecycleError):
        coordinator.start()

    # Stop
    coordinator.stop()
    assert coordinator._state == ServiceState.STOPPED
    assert res.is_empty  # All resources released cleanly

    # ATK-24: Double stop is idempotent
    coordinator.stop()
    assert coordinator._state == ServiceState.STOPPED


def test_coordinator_stop_uninitialized_idempotent(device_registry: DeviceRegistry):
    """Verify D165: stop() in UNINITIALIZED is a harmless no-op."""
    coordinator = DeviceCoordinationService(device_registry)
    coordinator.stop()
    assert coordinator._state in (ServiceState.STOPPED, ServiceState.UNINITIALIZED)


def test_capture_snapshot_and_fault_isolation(device_registry: DeviceRegistry, registry_token: RegistryAuthorityToken):
    """Verify ATK-08, ATK-09, ATK-34: Snapshot isolates failing device health checks."""
    dev_good = DummyCoordinationDevice("cam_01", "p1", DeviceType.RGB_CAMERA, health_status=HealthStatus.HEALTHY)
    dev_raising = DummyCoordinationDevice("cam_02", "p2", DeviceType.RGB_CAMERA, health_raise_exc=RuntimeError("Bus crash"))
    dev_malformed = DummyCoordinationDevice("cam_03", "p3", DeviceType.RGB_CAMERA, health_malformed_return="not_a_health_object")

    register_device(device_registry, registry_token, dev_good)
    register_device(device_registry, registry_token, dev_raising)
    register_device(device_registry, registry_token, dev_malformed)

    sink = RecordingDeviceCoordinationEventSink()
    coordinator = DeviceCoordinationService(device_registry, event_sink=sink)
    coordinator.initialize(make_test_context(1))
    coordinator.start()

    snapshot = coordinator.capture_snapshot()
    assert len(snapshot.observations) == 3

    obs_map = {o.device_id: o for o in snapshot.observations}
    assert obs_map["cam_01"].health_status == HealthStatus.HEALTHY
    assert obs_map["cam_02"].health_status == HealthStatus.FAILED
    assert "Bus crash" not in obs_map["cam_02"].health_message  # Exception type recorded
    assert obs_map["cam_03"].health_status == HealthStatus.FAILED

    # Event emitted
    assert len(sink.events) == 1
    assert sink.events[0].message_name == "device.coordination.snapshot_captured"


def test_disappeared_device_excluded_from_snapshot(device_registry: DeviceRegistry, registry_token: RegistryAuthorityToken):
    """Verify ATK-28: Removed device is excluded from subsequent snapshot."""
    dev = DummyCoordinationDevice("cam_01", "p1")
    register_device(device_registry, registry_token, dev)

    coordinator = DeviceCoordinationService(device_registry)
    coordinator.initialize(make_test_context(1))
    coordinator.start()

    s1 = coordinator.capture_snapshot()
    assert len(s1.observations) == 1

    # Deregister device
    dev._state = DeviceState.STOPPED
    device_registry.deregister("cam_01", registry_token)

    s2 = coordinator.capture_snapshot()
    assert len(s2.observations) == 0


def test_event_sink_error_isolation(device_registry: DeviceRegistry, registry_token: RegistryAuthorityToken):
    """Verify ATK-21: Failing event sink does not crash coordinator operations."""
    class FailingSink(RecordingDeviceCoordinationEventSink):
        def emit(self, envelope):
            raise IOError("Disk write failure")

    dev = DummyCoordinationDevice("cam_01", "p1")
    register_device(device_registry, registry_token, dev)

    coordinator = DeviceCoordinationService(device_registry, event_sink=FailingSink())
    coordinator.initialize(make_test_context(1))
    coordinator.start()

    # Operations succeed despite event sink raising
    coordinator.capture_snapshot()
    coordinator.update_telemetry("cam_01", sequence_number=0, timestamp_utc="ts", status="SUBMITTED")
    assert coordinator._sink_errors_count == 2


def test_dispatcher_snapshot_command_routing(device_registry: DeviceRegistry):
    """Verify ATK-33, D161: Dispatcher handle_snapshot_command routing and error paths."""
    coordinator = DeviceCoordinationService(device_registry)
    coordinator.initialize(make_test_context(epoch_id=1))
    coordinator.start()

    # 1. Valid snapshot command
    cmd_valid = create_command("device.coordination.snapshot", "client", payload={"epoch_id": 1})
    resp_valid = coordinator.handle_snapshot_command(cmd_valid)
    assert resp_valid.message_type == MessageType.RESPONSE
    assert "observations" in resp_valid.payload

    # 2. ATK-33: Epoch mismatch
    cmd_mismatch = create_command("device.coordination.snapshot", "client", payload={"epoch_id": 2})
    resp_mismatch = coordinator.handle_snapshot_command(cmd_mismatch)
    assert resp_mismatch.message_type == MessageType.ERROR
    assert resp_mismatch.payload["error_code"] == "ERR_EPOCH_MISMATCH"

    # 3. Missing / invalid epoch_id
    cmd_invalid = create_command("device.coordination.snapshot", "client", payload={"epoch_id": "one"})
    resp_invalid = coordinator.handle_snapshot_command(cmd_invalid)
    assert resp_invalid.message_type == MessageType.ERROR
    assert resp_invalid.payload["error_code"] == "ERR_VALIDATION_ERROR"


def test_dispatcher_health_query_routing(device_registry: DeviceRegistry, registry_token: RegistryAuthorityToken):
    """Verify ATK-32, D161: Dispatcher handle_health_query actions and error paths."""
    dev = DummyCoordinationDevice("cam_01", "p1")
    register_device(device_registry, registry_token, dev)

    coordinator = DeviceCoordinationService(device_registry)
    coordinator.initialize(make_test_context(epoch_id=1))
    coordinator.start()
    coordinator.update_telemetry("cam_01", sequence_number=0, timestamp_utc="ts", status="SUBMITTED")

    # 1. get_aggregate_health
    q_agg = create_query("device.coordination.health", "client", payload={"action": "get_aggregate_health"})
    r_agg = coordinator.handle_health_query(q_agg)
    assert r_agg.message_type == MessageType.RESPONSE
    assert r_agg.payload["status"] == HealthStatus.HEALTHY.name

    # 2. get_device_health
    q_dev = create_query("device.coordination.health", "client", payload={"action": "get_device_health", "device_id": "cam_01"})
    r_dev = coordinator.handle_health_query(q_dev)
    assert r_dev.message_type == MessageType.RESPONSE
    assert r_dev.payload["device_id"] == "cam_01"

    # 3. get_device_health unknown device
    q_dev_unk = create_query("device.coordination.health", "client", payload={"action": "get_device_health", "device_id": "cam_unknown"})
    r_dev_unk = coordinator.handle_health_query(q_dev_unk)
    assert r_dev_unk.message_type == MessageType.ERROR
    assert r_dev_unk.payload["error_code"] == "ERR_DEVICE_NOT_FOUND"

    # 4. get_telemetry
    q_tel = create_query("device.coordination.health", "client", payload={"action": "get_telemetry", "device_id": "cam_01"})
    r_tel = coordinator.handle_health_query(q_tel)
    assert r_tel.message_type == MessageType.RESPONSE
    assert r_tel.payload["telemetry"]["submitted_items"] == 1

    # 5. ATK-32: Unknown query action
    q_unk = create_query("device.coordination.health", "client", payload={"action": "reboot_all"})
    r_unk = coordinator.handle_health_query(q_unk)
    assert r_unk.message_type == MessageType.ERROR
    assert r_unk.payload["error_code"] == "ERR_INVALID_QUERY"


def test_coordinator_reset_epoch_and_clear(device_registry: DeviceRegistry, registry_token: RegistryAuthorityToken):
    """Verify §17 & ATK-26: reset_epoch() updates epoch and purges prior telemetry state."""
    dev = DummyCoordinationDevice("cam_01", "p1")
    register_device(device_registry, registry_token, dev)

    coordinator = DeviceCoordinationService(device_registry)
    coordinator.initialize(make_test_context(epoch_id=1))
    coordinator.start()

    coordinator.update_telemetry("cam_01", sequence_number=0, timestamp_utc="ts", status="SUBMITTED")
    assert coordinator.get_telemetry("cam_01").submitted_items == 1

    # Reset epoch to 2
    coordinator.reset_epoch(new_epoch_id=2)
    assert coordinator._epoch_id == 2
    with pytest.raises(DeviceCoordinationDeviceNotFoundError):
        coordinator.get_telemetry("cam_01")

    # Re-ingest in new epoch 2
    coordinator.update_telemetry("cam_01", sequence_number=0, timestamp_utc="ts2", status="SUBMITTED")
    assert coordinator.get_telemetry("cam_01").epoch_id == 2

    # Clear
    coordinator.clear()
    with pytest.raises(DeviceCoordinationDeviceNotFoundError):
        coordinator.get_telemetry("cam_01")


def test_reentrancy_protection(device_registry: DeviceRegistry):
    """Verify ATK-22 & §20: Reentrant calls are blocked by _in_transaction guard."""
    coordinator = DeviceCoordinationService(device_registry)
    coordinator.initialize(make_test_context(epoch_id=1))
    coordinator.start()

    # Simulate an active transaction
    coordinator._in_transaction = True
    try:
        with pytest.raises(DeviceCoordinationLifecycleError):
            coordinator.capture_snapshot()
        with pytest.raises(DeviceCoordinationLifecycleError):
            coordinator.update_telemetry("dev", 1, "ts", "SUBMITTED")
        with pytest.raises(DeviceCoordinationLifecycleError):
            coordinator.stop()
    finally:
        coordinator._in_transaction = False
