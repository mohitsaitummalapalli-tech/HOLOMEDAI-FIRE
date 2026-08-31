"""HoloMed AI - Device data processor registry."""

from __future__ import annotations

from typing import Dict, Optional

from holomed.devices.data.exceptions import DataProcessorCapacityError
from holomed.devices.data.models import (
    DataProcessor,
    DeviceDataKind,
    MAX_REGISTERED_PROCESSORS,
)


class DeviceDataProcessorRegistry:
    """Registry mapping DeviceDataKind to synchronous DataProcessor callables.

    Guarantees:
    - Bounded to MAX_REGISTERED_PROCESSORS (256).
    - Exactly one processor per DeviceDataKind.
    - Replacement within an active epoch is strictly forbidden.
    """

    def __init__(self, max_processors: int = MAX_REGISTERED_PROCESSORS) -> None:
        self._max_processors = max_processors
        self._processors: Dict[DeviceDataKind, DataProcessor] = {}

    def register(self, kind: DeviceDataKind, processor: DataProcessor) -> None:
        """Register a processor for a data kind."""
        if not isinstance(kind, DeviceDataKind):
            raise TypeError(f"Expected DeviceDataKind, got {type(kind).__name__}")
        if not callable(processor):
            raise TypeError("DataProcessor must be callable")

        if kind in self._processors:
            raise DataProcessorCapacityError(f"Processor for kind '{kind.value}' is already registered; replacement is forbidden")

        if len(self._processors) >= self._max_processors:
            raise DataProcessorCapacityError(f"Maximum registered processors exceeded ({self._max_processors})")

        self._processors[kind] = processor

    def get(self, kind: DeviceDataKind) -> Optional[DataProcessor]:
        """Look up processor by data kind."""
        return self._processors.get(kind)

    def contains(self, kind: DeviceDataKind) -> bool:
        """Check if processor is registered for kind."""
        return kind in self._processors

    def clear(self) -> None:
        """Clear registry."""
        self._processors.clear()

    def __len__(self) -> int:
        return len(self._processors)
