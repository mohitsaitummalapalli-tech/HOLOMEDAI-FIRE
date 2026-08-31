"""HoloMed AI - Device resource authority ensuring atomic, audited resource accounting."""

from __future__ import annotations

import re
from typing import Callable, Dict, Optional, Set

from holomed.devices.exceptions import (
    DeviceCapacityError,
    DeviceResourceIntegrityError,
    DeviceResourceOwnershipError,
    DeviceValidationError,
)
from holomed.devices.models import (
    MAX_HANDLES_PER_DEVICE,
    MAX_RESOURCE_ID_LENGTH,
    MAX_RESOURCE_NAME_LENGTH,
    MAX_TOTAL_DEVICE_HANDLES,
    RESOURCE_NAME_REGEX,
)
from holomed.runtime.models import OwnedResourceSet, ResourceHandle, ResourceStatus


class DeviceResourceAuthority:
    """Centralized, transactionally audited authority for device resource accounting.

    Guarantees:
    - Owns and protects the single OwnedResourceSet for devices.
    - Maintains exact mapping of resource_id -> device_id.
    - Enforces per-device capacity (<= 32) and total device capacity (<= 8192).
    - Excludes structural manager.* handles from device capacity.
    - Enforces mandatory audit_consistency() before and after every mutation.
    """

    def __init__(
        self,
        resources: OwnedResourceSet,
        verify_device_exists: Callable[[str], bool],
    ) -> None:
        self._resources = resources
        self._verify_device_exists = verify_device_exists
        self._resource_owners: Dict[str, str] = {}  # resource_id -> device_id
        self._device_handle_counts: Dict[str, int] = {}  # device_id -> count
        self._total_device_handles: int = 0

    def audit_consistency(self) -> None:
        """Mandatory mechanical verification of resource accounting integrity."""
        # Filter outstanding handles to device namespace only (starts with "device.")
        device_handles = [
            h for h in self._resources.outstanding_handles
            if h.resource_id.startswith("device.")
        ]

        # Invariant 1: Total count matches outstanding device handles
        if len(device_handles) != self._total_device_handles:
            raise DeviceResourceIntegrityError(
                f"Total device handle count mismatch: OwnedResourceSet has {len(device_handles)}, authority has {self._total_device_handles}"
            )

        # Invariant 2: len(_resource_owners) == _total_device_handles
        if len(self._resource_owners) != self._total_device_handles:
            raise DeviceResourceIntegrityError(
                f"Owner index length ({len(self._resource_owners)}) does not match total device handles ({self._total_device_handles})"
            )

        # Invariant 3: Every outstanding device handle has an owner
        for h in device_handles:
            if h.resource_id not in self._resource_owners:
                raise DeviceResourceIntegrityError(f"Orphaned device resource handle: '{h.resource_id}' has no registered owner")

        # Invariant 4: Every owner entry corresponds to an outstanding handle
        outstanding_ids = set(h.resource_id for h in device_handles)
        for r_id in self._resource_owners:
            if r_id not in outstanding_ids:
                raise DeviceResourceIntegrityError(f"Stale ownership entry: '{r_id}' is not in outstanding handles")

        # Invariant 5: Sum of per-device handle counts equals total device handles
        sum_counts = sum(self._device_handle_counts.values())
        if sum_counts != self._total_device_handles:
            raise DeviceResourceIntegrityError(
                f"Per-device handle sum ({sum_counts}) does not match total device handles ({self._total_device_handles})"
            )

        # Invariant 6: Every device in _device_handle_counts is currently registered (D133)
        for dev_id in self._device_handle_counts:
            if not self._verify_device_exists(dev_id):
                raise DeviceResourceIntegrityError(f"Resource owner '{dev_id}' is not a currently registered device")

        # Invariant 7: Per-device count matches actual handles owned by that device
        actual_per_device: Dict[str, int] = {}
        for r_id, dev_id in self._resource_owners.items():
            actual_per_device[dev_id] = actual_per_device.get(dev_id, 0) + 1

        for dev_id, count in self._device_handle_counts.items():
            if count <= 0:
                raise DeviceResourceIntegrityError(f"Zero or negative count for device '{dev_id}': {count}")
            if count > MAX_HANDLES_PER_DEVICE:
                raise DeviceResourceIntegrityError(f"Device '{dev_id}' handle count ({count}) exceeds {MAX_HANDLES_PER_DEVICE}")
            if actual_per_device.get(dev_id, 0) != count:
                raise DeviceResourceIntegrityError(
                    f"Count mismatch for '{dev_id}': index has {actual_per_device.get(dev_id, 0)}, counter has {count}"
                )

        # Invariant 8: Global bound enforced
        if self._total_device_handles > MAX_TOTAL_DEVICE_HANDLES:
            raise DeviceResourceIntegrityError(
                f"Total device handles ({self._total_device_handles}) exceeds maximum ({MAX_TOTAL_DEVICE_HANDLES})"
            )

    def acquire(self, device_id: str, resource_name: str) -> ResourceHandle:
        """Transactionally acquire a named resource for a registered device."""
        # 1. Pre-audit
        self.audit_consistency()

        # 2. Validate resource_name syntax
        if type(resource_name) is not str or not RESOURCE_NAME_REGEX.match(resource_name) or not (1 <= len(resource_name) <= MAX_RESOURCE_NAME_LENGTH):
            raise DeviceValidationError(f"Invalid resource name syntax or length: '{resource_name}'")

        # 3. Construct canonical resource_id
        resource_id = f"device.{device_id}.{resource_name}"
        if len(resource_id) > MAX_RESOURCE_ID_LENGTH:
            raise DeviceValidationError(f"Resource ID exceeds maximum length {MAX_RESOURCE_ID_LENGTH}: '{resource_id}'")

        # 4. Verify duplicate ownership invariant (D134)
        if resource_id in self._resource_owners:
            raise DeviceResourceIntegrityError(f"Resource '{resource_id}' is already registered in ownership index")

        # 5. Verify per-device capacity (<= 32)
        current_dev_count = self._device_handle_counts.get(device_id, 0)
        if current_dev_count >= MAX_HANDLES_PER_DEVICE:
            raise DeviceCapacityError(f"Device '{device_id}' exceeded handle capacity ({MAX_HANDLES_PER_DEVICE})")

        # 6. Verify global capacity (<= 8192)
        if self._total_device_handles >= MAX_TOTAL_DEVICE_HANDLES:
            raise DeviceCapacityError(f"DeviceManager total device handle capacity exceeded ({MAX_TOTAL_DEVICE_HANDLES})")

        # 7. Acquire in OwnedResourceSet FIRST (transaction boundary)
        handle = self._resources.acquire(resource_id)

        # 8. Publish ownership and update counters
        try:
            self._resource_owners[resource_id] = device_id
            self._device_handle_counts[device_id] = current_dev_count + 1
            self._total_device_handles += 1
            # 9. Post-audit
            self.audit_consistency()
        except Exception:
            # Internal rollback on unexpected failure
            self._resources.release(resource_id)
            self._resource_owners.pop(resource_id, None)
            if current_dev_count == 0:
                self._device_handle_counts.pop(device_id, None)
            else:
                self._device_handle_counts[device_id] = current_dev_count
            self._total_device_handles = sum(self._device_handle_counts.values())
            raise

        return handle

    def release(self, device_id: str, resource_id: str) -> None:
        """Release an acquired resource owned by a registered device."""
        # 1. Pre-audit
        self.audit_consistency()

        # 2. Verify exact ownership
        owner = self._resource_owners.get(resource_id)
        if owner != device_id:
            raise DeviceResourceOwnershipError(f"Device '{device_id}' does not own resource '{resource_id}'")

        # 3. Release in OwnedResourceSet
        self._resources.release(resource_id)

        # 4. Revoke ownership and update counters
        del self._resource_owners[resource_id]
        self._device_handle_counts[device_id] -= 1
        if self._device_handle_counts[device_id] == 0:
            del self._device_handle_counts[device_id]
        self._total_device_handles -= 1

        # 5. Post-audit
        self.audit_consistency()

    def mark_release_failed(self, device_id: str, resource_id: str, error: str) -> None:
        """Record an unreleased resource failure during teardown."""
        # 1. Pre-audit
        self.audit_consistency()

        # 2. Verify exact ownership
        owner = self._resource_owners.get(resource_id)
        if owner != device_id:
            raise DeviceResourceOwnershipError(f"Device '{device_id}' does not own resource '{resource_id}'")

        # 3. Mark release failure in OwnedResourceSet (ownership & capacity retained!)
        sanitized_error = error[:1024]
        self._resources.mark_release_failed(resource_id, sanitized_error)

        # 4. Post-audit
        self.audit_consistency()

    def get_device_outstanding_handles(self, device_id: str) -> frozenset[ResourceHandle]:
        """Query outstanding handles owned by a specific device."""
        self.audit_consistency()
        return frozenset(
            h for h in self._resources.outstanding_handles
            if self._resource_owners.get(h.resource_id) == device_id
        )

    def is_device_clean(self, device_id: str) -> bool:
        """True if and only if zero active or unreleased handles remain for this device."""
        self.audit_consistency()
        return len(self.get_device_outstanding_handles(device_id)) == 0

    @property
    def total_device_handles(self) -> int:
        """Total number of active and unreleased device handles."""
        return self._total_device_handles
