"""HoloMed AI - Device registry enforcing token-authorized registration and lifecycle invariants."""

from __future__ import annotations

from typing import Dict, List, Optional, Tuple

from holomed.devices.exceptions import (
    DeviceCapacityError,
    DeviceLifecycleError,
    DeviceNotFoundError,
    DeviceSecurityError,
    DuplicateDeviceError,
)
from holomed.devices.interfaces import IDevice, RegistryAuthorityToken
from holomed.devices.models import DeviceState, MAX_REGISTERED_DEVICES


class DeviceRegistry:
    """Registry maintaining active registered devices.

    Guarantees:
    - Mutation methods require passing a private RegistryAuthorityToken held only by DeviceManager.
    - Read-only queries (get, all_devices, contains) are public and non-mutating.
    - Capacity bounded to MAX_REGISTERED_DEVICES (256).
    - Prevents duplicate device_id or physical_id.
    - Preserves deterministic registration order for teardown sequencing.
    """

    def __init__(self, token: RegistryAuthorityToken) -> None:
        self._token = token
        self._devices: Dict[str, IDevice] = {}
        self._physical_map: Dict[str, str] = {}  # physical_id -> device_id
        self._registration_order: List[str] = []

    def _verify_token(self, token: RegistryAuthorityToken) -> None:
        if token is not self._token:
            raise DeviceSecurityError("Unauthorized mutation of DeviceRegistry without valid authority token")

    def register(self, device: IDevice, token: RegistryAuthorityToken) -> None:
        """Register a new device instance. Requires authority token and UNREGISTERED state."""
        self._verify_token(token)

        if len(self._devices) >= MAX_REGISTERED_DEVICES:
            raise DeviceCapacityError(f"DeviceRegistry capacity exceeded ({MAX_REGISTERED_DEVICES})")

        if device.device_id in self._devices:
            raise DuplicateDeviceError(f"Device with ID '{device.device_id}' already registered")

        if device.physical_id in self._physical_map:
            raise DuplicateDeviceError(f"Physical ID '{device.physical_id}' already in use by device '{self._physical_map[device.physical_id]}'")

        if device.state != DeviceState.UNREGISTERED:
            raise DeviceLifecycleError(f"Cannot register device '{device.device_id}' in state {device.state.name}, expected UNREGISTERED")

        self._devices[device.device_id] = device
        self._physical_map[device.physical_id] = device.device_id
        self._registration_order.append(device.device_id)

    def deregister(self, device_id: str, token: RegistryAuthorityToken) -> None:
        """Remove a registered device. Requires authority token and STOPPED, REGISTERED, or clean FAILED state."""
        self._verify_token(token)

        if device_id not in self._devices:
            raise DeviceNotFoundError(f"Device '{device_id}' not found in registry")

        device = self._devices[device_id]
        if device.state not in (DeviceState.STOPPED, DeviceState.REGISTERED, DeviceState.FAILED):
            raise DeviceLifecycleError(
                f"Cannot deregister device '{device_id}' in state {device.state.name}; must be STOPPED, REGISTERED, or FAILED"
            )

        del self._devices[device_id]
        del self._physical_map[device.physical_id]
        self._registration_order.remove(device_id)

    def clear(self, token: RegistryAuthorityToken) -> None:
        """Clear all registered devices. Requires authority token."""
        self._verify_token(token)
        self._devices.clear()
        self._physical_map.clear()
        self._registration_order.clear()

    # Public Read-Only Query Methods
    def get(self, device_id: str) -> IDevice:
        """Look up a device by identifier."""
        if device_id not in self._devices:
            raise DeviceNotFoundError(f"Device '{device_id}' not found in registry")
        return self._devices[device_id]

    def get_by_physical_id(self, physical_id: str) -> Optional[IDevice]:
        """Look up a device by physical identifier if registered."""
        dev_id = self._physical_map.get(physical_id)
        if dev_id is None:
            return None
        return self._devices.get(dev_id)

    def contains(self, device_id: str) -> bool:
        """Check if a device identifier is registered."""
        return device_id in self._devices

    @property
    def all_devices(self) -> Tuple[IDevice, ...]:
        """Return all registered devices in forward registration order."""
        return tuple(self._devices[dev_id] for dev_id in self._registration_order)

    def __len__(self) -> int:
        return len(self._devices)
