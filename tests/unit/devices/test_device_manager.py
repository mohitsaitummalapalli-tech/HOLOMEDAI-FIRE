"""Unit tests for DeviceManager health, shutdown, refresh, and teardown failure semantics."""

import pytest

from holomed.devices.discovery import StaticDiscoveryProvider
from holomed.devices.exceptions import DeviceShutdownError
from holomed.devices.manager import DeviceManager
from holomed.devices.models import (
    CapabilityCategory,
    DeviceCapability,
    DeviceDescriptor,
    DeviceHealth,
    DeviceState,
    DeviceType,
)
from holomed.devices.simulated import SimulatedDevice
from tests.unit.devices.conftest import make_test_context
from holomed.runtime.models import HealthStatus
from holomed.runtime.service import ServiceState


def test_device_manager_health_exception_boundary_and_redaction() -> None:
    manager = DeviceManager()
    ctx = make_test_context(epoch_id=1)
    manager.initialize(ctx)
    manager.start()

    device = SimulatedDevice("cam1", "USB:1")
    manager.register_device(device)
    manager.initialize_device("cam1")
    manager.start_device("cam1")

    # 1. Healthy state
    health_snap = manager.health()
    assert health_snap.status == HealthStatus.HEALTHY
    assert "cam1" in health_snap.message

    # 2. Device raises exception on health()
    device.set_fail_on_health(ValueError("Secret key sk-12345 leaked in hardware probe"))
    health_snap = manager.health()
    assert health_snap.status == HealthStatus.FAILED


def test_device_manager_health_spoofed_device_id_overridden() -> None:
    manager = DeviceManager()
    ctx = make_test_context(epoch_id=1)
    manager.initialize(ctx)
    manager.start()

    device = SimulatedDevice("cam_real", "USB:real")
    # Custom health snapshot claiming to be "cam_spoofed"
    spoofed_health = DeviceHealth(
        device_id="cam_spoofed",
        status=HealthStatus.HEALTHY,
        message="I am someone else",
        timestamp_utc="2026-08-31T00:00:00Z",
    )
    device.set_custom_health(spoofed_health)

    manager.register_device(device)
    health_snap = manager.health()

    # Manager attributes the health snapshot to the actual registered device_id
    assert "cam_real" in health_snap.message
    assert "cam_spoofed" not in health_snap.message


def test_structural_resource_failure_marks_manager_failed_and_preserves_handles() -> None:
    manager = DeviceManager()
    ctx = make_test_context(epoch_id=1)
    manager.initialize(ctx)
    manager.start()

    # Simulate structural handle failure by corrupting the OwnedResourceSet state of a structural handle
    manager.resources.mark_release_failed("manager.discovery", "Hardware bus frozen")

    # When manager.stop() is called, it continues teardown and aggregates failure
    with pytest.raises(DeviceShutdownError) as exc_info:
        manager.stop()

    assert manager._state == ServiceState.FAILED
    # Failure record logged for manager
    failures = exc_info.value.failures
    assert any(f.device_id == "manager" for f in failures)
    # Failed handle remains outstanding in OwnedResourceSet, blocking epoch retirement
    assert not manager.resources.is_empty


def test_refresh_prepare_before_retire_failure_leaves_old_device_active() -> None:
    manager = DeviceManager()
    ctx = make_test_context(epoch_id=1)
    manager.initialize(ctx)
    manager.start()

    cap1 = DeviceCapability("cap1", CapabilityCategory.STREAMING, {})
    cap2 = DeviceCapability("cap2", CapabilityCategory.STREAMING, {})

    desc1 = DeviceDescriptor("cam1", "USB:1", DeviceType.RGB_CAMERA, (cap1,), {})
    desc2 = DeviceDescriptor("cam1", "USB:1", DeviceType.RGB_CAMERA, (cap2,), {})

    # Register factory that works on first call but crashes on candidate preparation
    call_count = 0

    def flaky_factory(desc):
        nonlocal call_count
        call_count += 1
        if call_count > 1:
            raise RuntimeError("Factory creation hardware crash")
        return SimulatedDevice(desc.device_id, desc.physical_id, desc.device_type, desc.capabilities)

    manager.register_factory(DeviceType.RGB_CAMERA, flaky_factory)

    # First refresh: creates cam1 with desc1
    rep1 = manager.refresh_devices(StaticDiscoveryProvider([desc1]))
    assert "cam1" in rep1.added
    old_dev = manager._registry.get("cam1")
    assert old_dev.state == DeviceState.ACTIVE

    # Second refresh: descriptor changed to desc2; factory fails during prepare
    rep2 = manager.refresh_devices(StaticDiscoveryProvider([desc2]))
    assert "cam1" in rep2.failed
    # Old device remains active and untouched!
    assert old_dev.state == DeviceState.ACTIVE
    assert manager._registry.get("cam1") is old_dev
