"""Unit tests for DeviceResourceAuthority, consistency auditing, and capacity mathematics."""

import pytest

from holomed.devices.authority import DeviceResourceAuthority
from holomed.devices.exceptions import (
    DeviceCapacityError,
    DeviceResourceIntegrityError,
    DeviceResourceOwnershipError,
    DeviceValidationError,
)
from holomed.devices.models import (
    MAX_HANDLES_PER_DEVICE,
    MAX_TOTAL_DEVICE_HANDLES,
)
from holomed.runtime.models import OwnedResourceSet


def test_resource_authority_acquire_release_and_consistency() -> None:
    resources = OwnedResourceSet("test_manager", 1)
    registered_devices = {"dev1", "dev2"}
    authority = DeviceResourceAuthority(resources, lambda dev_id: dev_id in registered_devices)

    # Initial consistency
    authority.audit_consistency()
    assert authority.total_device_handles == 0
    assert authority.is_device_clean("dev1")

    # Acquire resource
    handle = authority.acquire("dev1", "buffer")
    assert handle.resource_id == "device.dev1.buffer"
    assert authority.total_device_handles == 1
    assert not authority.is_device_clean("dev1")
    assert len(authority.get_device_outstanding_handles("dev1")) == 1

    # Release resource
    authority.release("dev1", "device.dev1.buffer")
    assert authority.total_device_handles == 0
    assert authority.is_device_clean("dev1")
    authority.audit_consistency()


def test_resource_authority_rejects_foreign_release() -> None:
    resources = OwnedResourceSet("test_manager", 1)
    registered = {"dev1", "dev2"}
    authority = DeviceResourceAuthority(resources, lambda dev_id: dev_id in registered)

    authority.acquire("dev1", "stream")

    # dev2 attempts to release dev1's resource
    with pytest.raises(DeviceResourceOwnershipError, match="does not own resource"):
        authority.release("dev2", "device.dev1.stream")

    # dev2 attempts to mark release failed on dev1's resource
    with pytest.raises(DeviceResourceOwnershipError, match="does not own resource"):
        authority.mark_release_failed("dev2", "device.dev1.stream", "error")


def test_resource_authority_detects_unregistered_owner(monkeypatch: pytest.MonkeyPatch) -> None:
    resources = OwnedResourceSet("test_manager", 1)
    registered = {"dev1"}
    authority = DeviceResourceAuthority(resources, lambda dev_id: dev_id in registered)

    authority.acquire("dev1", "buf")

    # Now dev1 disappears from registered devices without cleanup
    registered.clear()
    with pytest.raises(DeviceResourceIntegrityError, match="not a currently registered device"):
        authority.audit_consistency()


def test_resource_authority_per_device_and_total_capacity_limits() -> None:
    resources = OwnedResourceSet("test_manager", 1)
    registered = {f"dev_{i}" for i in range(300)}
    authority = DeviceResourceAuthority(resources, lambda dev_id: dev_id in registered)

    # Acquire up to MAX_HANDLES_PER_DEVICE (32) for dev_0
    for i in range(MAX_HANDLES_PER_DEVICE):
        authority.acquire("dev_0", f"handle_{i}")

    # 33rd handle for dev_0 raises DeviceCapacityError
    with pytest.raises(DeviceCapacityError, match="exceeded handle capacity"):
        authority.acquire("dev_0", "overflow_handle")

    # Other devices can still acquire handles
    h = authority.acquire("dev_1", "handle_0")
    assert h.resource_id == "device.dev_1.handle_0"


def test_resource_authority_mark_release_failed_retains_ownership_and_capacity() -> None:
    resources = OwnedResourceSet("test_manager", 1)
    registered = {"dev1"}
    authority = DeviceResourceAuthority(resources, lambda dev_id: dev_id in registered)

    authority.acquire("dev1", "sensor_stream")
    assert authority.total_device_handles == 1

    authority.mark_release_failed("dev1", "device.dev1.sensor_stream", "Hardware disconnect error")

    # Ownership and capacity retained
    assert authority.total_device_handles == 1
    assert not authority.is_device_clean("dev1")
    authority.audit_consistency()

    # Still counts against device limit
    for i in range(MAX_HANDLES_PER_DEVICE - 1):
        authority.acquire("dev1", f"h_{i}")

    with pytest.raises(DeviceCapacityError, match="exceeded handle capacity"):
        authority.acquire("dev1", "overflow")


def test_duplicate_resource_owner_detected() -> None:
    resources = OwnedResourceSet("test_manager", 1)
    registered = {"dev1"}
    authority = DeviceResourceAuthority(resources, lambda dev_id: dev_id in registered)

    authority.acquire("dev1", "primary")

    # Second acquire of same resource name for same device
    with pytest.raises(DeviceResourceIntegrityError, match="already registered in ownership index"):
        authority.acquire("dev1", "primary")


def test_structural_handles_excluded_from_device_capacity() -> None:
    resources = OwnedResourceSet("test_manager", 1)
    # Manager acquires its 3 structural handles directly
    h1 = resources.acquire("manager.registry")
    h2 = resources.acquire("manager.discovery")
    h3 = resources.acquire("manager.event_emitter")

    registered = {"dev1"}
    authority = DeviceResourceAuthority(resources, lambda dev_id: dev_id in registered)

    # Authority audit ignores manager.* handles and succeeds
    authority.audit_consistency()
    assert authority.total_device_handles == 0

    # Device acquires a handle
    authority.acquire("dev1", "channel")
    authority.audit_consistency()
    assert authority.total_device_handles == 1
    # Total outstanding in OwnedResourceSet is 4 (3 structural + 1 device)
    assert len(resources.outstanding_handles) == 4
