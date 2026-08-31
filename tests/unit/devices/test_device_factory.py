"""Unit tests for device factory registration, validation, and live-object freshness."""

import pytest
import weakref

from holomed.devices.exceptions import (
    DeviceFactoryError,
    DeviceFactoryMissingError,
    DeviceLifecycleError,
    DeviceValidationError,
)
from holomed.devices.freshness import DeviceFreshnessTracker
from holomed.devices.manager import DeviceManager
from holomed.devices.models import (
    CapabilityCategory,
    DeviceCapability,
    DeviceDescriptor,
    DeviceState,
    DeviceType,
)
from holomed.devices.simulated import SimulatedDevice
from tests.unit.devices.conftest import make_test_context


def test_freshness_tracker_detects_live_object_reuse() -> None:
    tracker = DeviceFreshnessTracker()
    device = SimulatedDevice("cam1", "USB:1")

    assert not tracker.is_stale(device)
    tracker.track(device)
    assert tracker.is_stale(device)

    # Different device instance with same IDs is not stale
    other_device = SimulatedDevice("cam1", "USB:1")
    assert not tracker.is_stale(other_device)


def test_freshness_tracker_purges_on_gc() -> None:
    tracker = DeviceFreshnessTracker()

    def make_and_track():
        dev = SimulatedDevice("temp", "USB:temp")
        obj_id = id(dev)
        tracker.track(dev)
        return obj_id

    dev_obj_id = make_and_track()
    import gc
    gc.collect()

    # Once object is collected, tracker dictionary purges the id
    assert dev_obj_id not in tracker._active_refs


def test_factory_registration_and_immutability() -> None:
    manager = DeviceManager()
    ctx = make_test_context(epoch_id=1)
    manager.initialize(ctx)

    # Calling before STARTED raises DeviceLifecycleError
    with pytest.raises(DeviceLifecycleError, match="expected STARTED"):
        manager.register_factory(DeviceType.RGB_CAMERA, lambda desc: SimulatedDevice(desc.device_id, desc.physical_id))

    manager.start()

    # Successful registration
    manager.register_factory(DeviceType.RGB_CAMERA, lambda desc: SimulatedDevice(desc.device_id, desc.physical_id))

    # Duplicate registration strictly forbidden
    with pytest.raises(DeviceFactoryError, match="replacement is strictly forbidden"):
        manager.register_factory(DeviceType.RGB_CAMERA, lambda desc: SimulatedDevice(desc.device_id, desc.physical_id))


def test_factory_validation_mismatch_and_stale_rejection() -> None:
    manager = DeviceManager()
    ctx = make_test_context(epoch_id=1)
    manager.initialize(ctx)
    manager.start()

    desc = DeviceDescriptor(
        device_id="cam_front",
        physical_id="USB:1",
        device_type=DeviceType.RGB_CAMERA,
        capabilities=(DeviceCapability("cap1", CapabilityCategory.STREAMING, {}),),
        metadata={},
    )

    # 1. Factory returns wrong device_id
    manager.register_factory(DeviceType.RGB_CAMERA, lambda d: SimulatedDevice("wrong_id", d.physical_id, d.device_type, d.capabilities))
    from holomed.devices.discovery import StaticDiscoveryProvider
    report = manager.refresh_devices(StaticDiscoveryProvider([desc]))
    assert "cam_front" in report.failed
    assert "Factory returned device_id" in report.failed["cam_front"]

    # Reset manager
    manager.stop()
    manager = DeviceManager()
    manager.initialize(ctx)
    manager.start()

    # 2. Factory returns stale live instance on subsequent calls
    reused_instance = SimulatedDevice(desc.device_id, desc.physical_id, desc.device_type, desc.capabilities)
    manager.register_factory(DeviceType.RGB_CAMERA, lambda d: reused_instance)

    # First refresh succeeds
    report1 = manager.refresh_devices(StaticDiscoveryProvider([desc]))
    assert "cam_front" in report1.added

    # If factory attempts to return the exact same live instance when registering again, it fails
    with pytest.raises(DeviceFactoryError, match="reused live object"):
        manager.register_device(reused_instance)
