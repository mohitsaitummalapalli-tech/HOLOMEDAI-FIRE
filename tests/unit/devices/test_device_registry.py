"""Unit tests for device registry and RegistryAuthorityToken."""

import pytest

from holomed.devices.exceptions import (
    DeviceCapacityError,
    DeviceLifecycleError,
    DeviceNotFoundError,
    DeviceSecurityError,
    DuplicateDeviceError,
)
from holomed.devices.interfaces import RegistryAuthorityToken
from holomed.devices.models import DeviceState, MAX_REGISTERED_DEVICES
from holomed.devices.registry import DeviceRegistry
from holomed.devices.simulated import SimulatedDevice


def test_registry_requires_valid_authority_token() -> None:
    valid_token = RegistryAuthorityToken()
    forged_token = RegistryAuthorityToken()
    registry = DeviceRegistry(valid_token)
    device = SimulatedDevice("cam1", "USB:1")

    # Mutation with forged token raises DeviceSecurityError
    with pytest.raises(DeviceSecurityError, match="Unauthorized mutation"):
        registry.register(device, forged_token)

    # Mutation with valid token succeeds
    registry.register(device, valid_token)
    assert registry.contains("cam1")

    # Deregister with forged token raises DeviceSecurityError
    with pytest.raises(DeviceSecurityError, match="Unauthorized mutation"):
        registry.deregister("cam1", forged_token)

    # Clear with forged token raises DeviceSecurityError
    with pytest.raises(DeviceSecurityError, match="Unauthorized mutation"):
        registry.clear(forged_token)


def test_registry_enforces_unregistered_state_on_register() -> None:
    token = RegistryAuthorityToken()
    registry = DeviceRegistry(token)
    device = SimulatedDevice("cam1", "USB:1")
    device._state = DeviceState.ACTIVE

    with pytest.raises(DeviceLifecycleError, match="expected UNREGISTERED"):
        registry.register(device, token)


def test_registry_enforces_stopped_or_registered_state_on_deregister() -> None:
    token = RegistryAuthorityToken()
    registry = DeviceRegistry(token)
    device = SimulatedDevice("cam1", "USB:1")
    registry.register(device, token)

    device._state = DeviceState.ACTIVE
    with pytest.raises(DeviceLifecycleError, match="must be STOPPED, REGISTERED, or FAILED"):
        registry.deregister("cam1", token)

    device._state = DeviceState.STOPPED
    registry.deregister("cam1", token)
    assert not registry.contains("cam1")


def test_registry_rejects_duplicate_device_id_and_physical_id() -> None:
    token = RegistryAuthorityToken()
    registry = DeviceRegistry(token)
    dev1 = SimulatedDevice("cam1", "USB:1")
    dev2 = SimulatedDevice("cam1", "USB:2")
    dev3 = SimulatedDevice("cam2", "USB:1")

    registry.register(dev1, token)

    # Duplicate device_id
    with pytest.raises(DuplicateDeviceError, match="already registered"):
        registry.register(dev2, token)

    # Duplicate physical_id
    with pytest.raises(DuplicateDeviceError, match="Physical ID 'USB:1' already in use"):
        registry.register(dev3, token)


def test_registry_capacity_limit() -> None:
    token = RegistryAuthorityToken()
    registry = DeviceRegistry(token)

    for i in range(MAX_REGISTERED_DEVICES):
        dev = SimulatedDevice(f"dev_{i}", f"PHYS:{i}")
        registry.register(dev, token)

    assert len(registry) == MAX_REGISTERED_DEVICES

    # 257th device raises DeviceCapacityError
    dev_overflow = SimulatedDevice("dev_overflow", "PHYS:overflow")
    with pytest.raises(DeviceCapacityError, match="DeviceRegistry capacity exceeded"):
        registry.register(dev_overflow, token)


def test_registry_lookup_queries() -> None:
    token = RegistryAuthorityToken()
    registry = DeviceRegistry(token)
    dev = SimulatedDevice("cam_front", "USB:front")
    registry.register(dev, token)

    assert registry.get("cam_front") is dev
    assert registry.get_by_physical_id("USB:front") is dev
    assert registry.get_by_physical_id("USB:nonexistent") is None
    assert registry.contains("cam_front")
    assert not registry.contains("cam_rear")

    with pytest.raises(DeviceNotFoundError, match="not found in registry"):
        registry.get("nonexistent")
