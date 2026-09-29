"""HoloMed AI - Cryptographically secure admission authority."""

from __future__ import annotations

import weakref
from typing import Callable, Optional

from holomed.devices.models import (
    AdmittedCommandCapability,
    PhysicalCommand,
    EndpointLease,
    DeviceValidationError,
)
from typing import TYPE_CHECKING
if TYPE_CHECKING:
    from holomed.devices.control.manager import DeviceControlManager

_verifier_ref: Optional[weakref.ReferenceType] = None


def register_authoritative_verifier(manager: 'DeviceControlManager') -> None:
    """Registered exclusively by the active DeviceControlManager."""
    from holomed.devices.control.manager import DeviceControlManager
    if manager.__class__ is not DeviceControlManager:
        raise RuntimeError("Authoritative admission verifier must be tied to the legitimate DeviceControlManager lifecycle.")
        
    from holomed.runtime.service import ServiceState
    if manager._state != ServiceState.STARTED:
        raise RuntimeError("Authoritative admission verifier must be STARTED and hold durable authority before registration.")
        
    global _verifier_ref
    if _verifier_ref is not None:
        active_m = _verifier_ref()
        if active_m is not None and active_m is not manager:
            raise RuntimeError("Authoritative admission verifier is already registered and active.")
    _verifier_ref = weakref.ref(manager)


def unregister_authoritative_verifier(manager: 'DeviceControlManager') -> None:
    """Unregisters verifier for clean teardown."""
    global _verifier_ref
    if _verifier_ref is not None:
        if _verifier_ref() is manager:
            _verifier_ref = None


def verify_admitted_capability(capability: AdmittedCommandCapability, physical_command: PhysicalCommand, endpoint_lease: EndpointLease) -> None:
    """Public verification endpoint."""
    if _verifier_ref is None:
        raise RuntimeError("No active authoritative admission verifier registered.")
    manager = _verifier_ref()
    if manager is None:
        raise RuntimeError("Authoritative admission verifier has been destroyed.")
    manager._verify_capability(capability, physical_command, endpoint_lease)

