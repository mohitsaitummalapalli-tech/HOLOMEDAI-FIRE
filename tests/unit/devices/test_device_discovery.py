"""Unit tests for bounded discovery ingestion, snapshotting, and exception containment."""

import pytest

from holomed.devices.discovery import StaticDiscoveryProvider
from holomed.devices.exceptions import DeviceDiscoveryError
from holomed.devices.interfaces import DeviceDiscoveryProvider
from holomed.devices.manager import DeviceManager
from holomed.devices.models import (
    DeviceDescriptor,
    DeviceType,
    MAX_DISCOVERY_BATCH_SIZE,
)
from holomed.devices.simulated import SimulatedDevice
from tests.unit.devices.conftest import make_test_context


def test_discovery_infinite_iterable_is_bounded() -> None:
    manager = DeviceManager()
    ctx = make_test_context(epoch_id=1)
    manager.initialize(ctx)
    manager.start()

    # Generator producing infinite descriptors
    def infinite_descriptors():
        i = 0
        while True:
            yield DeviceDescriptor(f"dev_{i}", f"PHYS:{i}", DeviceType.RGB_CAMERA, (), {})
            i += 1

    class InfiniteProvider(DeviceDiscoveryProvider):
        def discover(self):
            return infinite_descriptors()

    # Must abort at 257 without consuming infinite items
    with pytest.raises(DeviceDiscoveryError, match="Discovery batch size exceeds maximum limit"):
        manager.discover_devices(InfiniteProvider())


def test_discovery_exact_256_items_accepted() -> None:
    manager = DeviceManager()
    ctx = make_test_context(epoch_id=1)
    manager.initialize(ctx)
    manager.start()

    descriptors = [
        DeviceDescriptor(f"dev_{i:03d}", f"PHYS:{i:03d}", DeviceType.RGB_CAMERA, (), {})
        for i in range(MAX_DISCOVERY_BATCH_SIZE)
    ]
    provider = StaticDiscoveryProvider(descriptors)

    snapshot = manager.discover_devices(provider)
    assert len(snapshot) == MAX_DISCOVERY_BATCH_SIZE


def test_discovery_provider_exception_wrapped() -> None:
    manager = DeviceManager()
    ctx = make_test_context(epoch_id=1)
    manager.initialize(ctx)
    manager.start()

    class BrokenProvider(DeviceDiscoveryProvider):
        def discover(self):
            raise OSError("USB Bus enumeration failed")

    with pytest.raises(DeviceDiscoveryError, match="Failed to initialize discovery provider iterator"):
        manager.discover_devices(BrokenProvider())


def test_hostile_descriptor_subclass_rejected() -> None:
    manager = DeviceManager()
    ctx = make_test_context(epoch_id=1)
    manager.initialize(ctx)
    manager.start()

    class HostileDescriptor(DeviceDescriptor):
        pass

    hostile_item = HostileDescriptor("cam1", "USB:1", DeviceType.RGB_CAMERA, (), {})

    class HostileProvider(DeviceDiscoveryProvider):
        def discover(self):
            return [hostile_item]

    with pytest.raises(DeviceDiscoveryError, match="expected exact 'DeviceDescriptor'"):
        manager.discover_devices(HostileProvider())


def test_hostile_metadata_container_rejected() -> None:
    manager = DeviceManager()
    ctx = make_test_context(epoch_id=1)
    manager.initialize(ctx)
    manager.start()

    class HostileDict(dict):
        pass

    desc = DeviceDescriptor("cam1", "USB:1", DeviceType.RGB_CAMERA, (), {})
    object.__setattr__(desc, "metadata", HostileDict({"k": "v"}))

    with pytest.raises(DeviceDiscoveryError, match="metadata must be exact dict"):
        manager.discover_devices(StaticDiscoveryProvider([desc]))
