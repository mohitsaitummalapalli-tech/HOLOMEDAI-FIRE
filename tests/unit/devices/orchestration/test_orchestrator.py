"""Tests for DevicePlaneOrchestrator service."""

import pytest

from holomed.core.dispatcher import MessageDispatcher
from holomed.devices.data.models import DeviceData, DeviceDataKind
from holomed.devices.orchestration.exceptions import (
    DeviceOrchestrationLifecycleError,
    DeviceOrchestrationShutdownError,
    DeviceOrchestrationValidationError,
)
from holomed.devices.orchestration.orchestrator import DevicePlaneOrchestrator
from holomed.protocol.builders import create_command, create_query
from holomed.runtime.service import ServiceState
from tests.unit.devices.orchestration.conftest import (
    DummyOrchestrationDevice,
    make_test_context,
    register_device,
)


def test_orchestrator_lifecycle_transitions(device_subsystem_stack) -> None:
    ctx = make_test_context(epoch_id=1)
    orch = DevicePlaneOrchestrator(
        device_manager=device_subsystem_stack["manager"],
        control_manager=device_subsystem_stack["control"],
        data_processor=device_subsystem_stack["data"],
        coordination=device_subsystem_stack["coordination"],
    )

    assert orch._state == ServiceState.UNINITIALIZED

    # Initialize
    orch.initialize(ctx)
    assert orch._state == ServiceState.INITIALIZED
    assert len(orch.resources.outstanding_handles) == 3
    assert orch.resources.is_empty is False

    # Start
    orch.start()
    assert orch._state == ServiceState.STARTED

    # Double start raises DeviceOrchestrationLifecycleError
    with pytest.raises(DeviceOrchestrationLifecycleError):
        orch.start()

    # Stop
    orch.stop()
    assert orch._state == ServiceState.STOPPED
    assert orch.resources.is_empty is True

    # Idempotent stop (D175)
    orch.stop()
    assert orch._state == ServiceState.STOPPED


def test_orchestrator_stop_in_uninitialized_is_idempotent(device_subsystem_stack) -> None:
    orch = DevicePlaneOrchestrator(
        device_manager=device_subsystem_stack["manager"],
        control_manager=device_subsystem_stack["control"],
        data_processor=device_subsystem_stack["data"],
        coordination=device_subsystem_stack["coordination"],
    )
    assert orch._state == ServiceState.UNINITIALIZED
    # Calling stop in UNINITIALIZED is safe no-op
    orch.stop()
    assert orch._state == ServiceState.UNINITIALIZED


def test_orchestrator_structural_resources_count(device_subsystem_stack) -> None:
    ctx = make_test_context(epoch_id=5)
    orch = DevicePlaneOrchestrator(
        device_manager=device_subsystem_stack["manager"],
        control_manager=device_subsystem_stack["control"],
        data_processor=device_subsystem_stack["data"],
        coordination=device_subsystem_stack["coordination"],
    )
    orch.initialize(ctx)

    handles = {h.resource_id for h in orch.resources.outstanding_handles}
    assert handles == {
        "orchestration.bridge",
        "orchestration.supervisor",
        "orchestration.audit",
    }
    orch.start()
    orch.stop()


def test_orchestrator_submit_data_increments_telemetry_exactly_once(device_subsystem_stack) -> None:
    ctx = make_test_context(epoch_id=1)
    dev = DummyOrchestrationDevice("sensor_01", "usb://s1")
    register_device(device_subsystem_stack["registry"], device_subsystem_stack["token"], dev)

    orch = DevicePlaneOrchestrator(
        device_manager=device_subsystem_stack["manager"],
        control_manager=device_subsystem_stack["control"],
        data_processor=device_subsystem_stack["data"],
        coordination=device_subsystem_stack["coordination"],
    )
    orch.initialize(ctx)
    orch.start()

    # Submit data through orchestrator
    data = DeviceData(
        device_id="sensor_01",
        physical_id="usb://s1",
        device_type=dev.device_type,
        kind=DeviceDataKind.OBSERVATION,
        sequence_number=1,
        timestamp_utc="2026-09-01T07:00:00.000000Z",
        payload={"temp": 37.0},
    )
    receipt = orch.submit_data(data)
    assert receipt.device_id == "sensor_01"
    assert receipt.sequence_number == 1

    # Manually pass event from data processor to bridge (simulating dispatcher routing)
    from holomed.protocol.builders import create_event
    event_env = create_event(
        "device.data.processed",
        "device_data_processor",
        payload={"device_id": "sensor_01", "sequence_number": 1},
    )
    orch.bridge.handle_event(event_env)

    # Verify telemetry incremented exactly once (D168)
    rec = device_subsystem_stack["coordination"].get_telemetry("sensor_01")
    assert rec.processed_items == 1
    assert rec.last_sequence_number == 1
    orch.stop()


def test_dispatcher_registration_timing_conforms_to_sealing_contract(device_subsystem_stack) -> None:
    ctx = make_test_context(epoch_id=1)
    dispatcher = MessageDispatcher()
    dispatcher.initialize(ctx)

    orch = DevicePlaneOrchestrator(
        device_manager=device_subsystem_stack["manager"],
        control_manager=device_subsystem_stack["control"],
        data_processor=device_subsystem_stack["data"],
        coordination=device_subsystem_stack["coordination"],
        dispatcher=dispatcher,
    )

    # Registered during INITIALIZED (D170)
    orch.initialize(ctx)
    orch.start()

    # Now seal dispatcher
    dispatcher.start()

    # Test status query via dispatcher handler
    q_status = create_query("device.orchestration.status", "client", payload={})
    res_status = orch.handle_status_query(q_status)
    assert res_status.payload["service_name"] == "device_orchestration"
    assert res_status.payload["state"] == "STARTED"

    # Test audit query via dispatcher handler
    q_audit = create_query("device.orchestration.audit", "client", payload={})
    res_audit = orch.handle_audit_query(q_audit)
    assert res_audit.payload["is_consistent"] is True

    # Test sync command via dispatcher handler
    c_sync = create_command("device.orchestration.sync", "client", payload={"epoch_id": 1})
    res_sync = orch.handle_sync_command(c_sync)
    assert res_sync.payload["synchronized"] is True

    orch.stop()
    dispatcher.stop()


def test_orchestrator_reentrancy_protection(device_subsystem_stack) -> None:
    ctx = make_test_context(epoch_id=1)
    orch = DevicePlaneOrchestrator(
        device_manager=device_subsystem_stack["manager"],
        control_manager=device_subsystem_stack["control"],
        data_processor=device_subsystem_stack["data"],
        coordination=device_subsystem_stack["coordination"],
    )
    orch.initialize(ctx)
    orch.start()

    orch._in_transaction = True
    with pytest.raises(DeviceOrchestrationLifecycleError):
        orch.audit()

    with pytest.raises(DeviceOrchestrationLifecycleError):
        orch.sync()

    with pytest.raises(DeviceOrchestrationLifecycleError):
        orch.stop()

    orch._in_transaction = False
    orch.stop()
