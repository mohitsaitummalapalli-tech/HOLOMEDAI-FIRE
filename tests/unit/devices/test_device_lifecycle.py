"""Unit tests for device lifecycle state transitions and execution."""

import pytest

from holomed.devices.exceptions import DeviceLifecycleError
from holomed.devices.manager import DeviceManager
from holomed.devices.models import DeviceState
from holomed.devices.simulated import SimulatedDevice
from tests.unit.devices.conftest import make_test_context


def test_full_device_lifecycle_success_path() -> None:
    manager = DeviceManager()
    ctx = make_test_context(epoch_id=1)
    manager.initialize(ctx)
    manager.start()

    device = SimulatedDevice("cam1", "USB:1")
    assert device.state == DeviceState.UNREGISTERED

    # 1. Register -> REGISTERED
    manager.register_device(device)
    assert device.state == DeviceState.REGISTERED

    # 2. Initialize -> READY
    manager.initialize_device("cam1")
    assert device.state == DeviceState.READY
    assert len(device._acquired_resource_ids) == 1

    # 3. Start -> ACTIVE
    manager.start_device("cam1")
    assert device.state == DeviceState.ACTIVE

    # 4. Stop -> STOPPED
    manager.stop_device("cam1")
    assert device.state == DeviceState.STOPPED
    assert len(device._acquired_resource_ids) == 0

    # 5. Deregister -> UNREGISTERED
    manager.deregister_device("cam1")
    assert device.state == DeviceState.UNREGISTERED


def test_ready_to_stopping_graceful_teardown() -> None:
    manager = DeviceManager()
    ctx = make_test_context(epoch_id=1)
    manager.initialize(ctx)
    manager.start()

    device = SimulatedDevice("cam1", "USB:1")
    manager.register_device(device)
    manager.initialize_device("cam1")
    assert device.state == DeviceState.READY

    # Direct stop from READY is allowed (pre-start teardown)
    manager.stop_device("cam1")
    assert device.state == DeviceState.STOPPED


def test_failed_device_cleanup_retry() -> None:
    manager = DeviceManager()
    ctx = make_test_context(epoch_id=1)
    manager.initialize(ctx)
    manager.start()

    device = SimulatedDevice("cam1", "USB:1")
    manager.register_device(device)
    manager.initialize_device("cam1")
    manager.start_device("cam1")

    # Configure stop failure
    device.set_fail_on_stop(RuntimeError("Hardware disconnect failure"))

    with pytest.raises(RuntimeError, match="Hardware disconnect failure"):
        manager.stop_device("cam1")

    assert device.state == DeviceState.FAILED

    # Deregister is forbidden on dirty FAILED device
    with pytest.raises(DeviceLifecycleError, match="dirty outstanding resources"):
        manager.deregister_device("cam1")

    # Fix device stop and retry cleanup
    device.set_fail_on_stop(None)
    success = manager.retry_device_cleanup("cam1")
    assert success is True
    assert device.state == DeviceState.STOPPED

    # Now deregistration succeeds
    manager.deregister_device("cam1")
    assert device.state == DeviceState.UNREGISTERED
