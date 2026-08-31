"""HoloMed AI - Live-object reuse tracking preventing stale instance re-registration."""

from __future__ import annotations

import weakref
from typing import Dict

from holomed.devices.interfaces import IDevice


class DeviceFreshnessTracker:
    """Live-Object Reuse Protection.

    Guarantees that a live IDevice instance already accepted into the registry
    cannot be reused or re-registered within the manager's lifetime.

    Implementation:
    - Uses a dictionary mapping object memory ID `id(device)` to `weakref.ref`.
    - Does NOT use WeakSet or invoke `hash(device)`, fully supporting unhashable devices.
    - Purges dead entries on garbage collection via weakref callback, preventing
      false positive collisions on CPython address reuse.
    """

    def __init__(self) -> None:
        self._active_refs: Dict[int, weakref.ref[IDevice]] = {}

    def track(self, device: IDevice) -> None:
        """Register a newly accepted live device instance for reuse tracking."""
        obj_id = id(device)

        def _cleanup(ref: weakref.ref) -> None:
            self._active_refs.pop(obj_id, None)

        self._active_refs[obj_id] = weakref.ref(device, _cleanup)

    def is_stale(self, device: IDevice) -> bool:
        """Return True if the exact live object is currently active or retired in this manager."""
        obj_id = id(device)
        ref = self._active_refs.get(obj_id)
        if ref is None:
            return False
        live_instance = ref()
        return live_instance is device

    def clear(self) -> None:
        """Clear all tracking upon manager retirement."""
        self._active_refs.clear()
