"""HoloMed AI - Concrete static device discovery provider."""

from __future__ import annotations

from typing import Iterable, Sequence, Tuple

from holomed.devices.interfaces import DeviceDiscoveryProvider
from holomed.devices.models import DeviceDescriptor


class StaticDiscoveryProvider(DeviceDiscoveryProvider):
    """Discovery provider returning a predetermined sequence of device descriptors."""

    def __init__(self, descriptors: Sequence[DeviceDescriptor]) -> None:
        self._descriptors = tuple(descriptors)

    def discover(self) -> Iterable[DeviceDescriptor]:
        """Return the pre-configured descriptors."""
        return self._descriptors
