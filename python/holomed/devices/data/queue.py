"""HoloMed AI - Bounded in-memory device data queue."""

from __future__ import annotations

import collections
from typing import Optional

from holomed.devices.data.exceptions import DeviceDataCapacityError
from holomed.devices.data.models import DeviceData, MAX_QUEUE_ITEMS


class BoundedDeviceDataQueue:
    """Strictly bounded FIFO queue for device data items.

    Guarantees:
    - Exactly MAX_QUEUE_ITEMS capacity (256).
    - Rejection of 257th item is atomic and non-mutating (D149).
    - No sleep, no threads, no blocking, no dynamic resizing.
    """

    def __init__(self, max_items: int = MAX_QUEUE_ITEMS) -> None:
        self._max_items = max_items
        self._items: collections.deque[DeviceData] = collections.deque()

    def put(self, item: DeviceData) -> None:
        """Enqueue an item or raise DeviceDataCapacityError if full."""
        if not isinstance(item, DeviceData):
            raise TypeError(f"Expected DeviceData instance, got {type(item).__name__}")
        if len(self._items) >= self._max_items:
            raise DeviceDataCapacityError(f"Data queue capacity ({self._max_items}) exceeded")
        self._items.append(item)

    def get(self) -> Optional[DeviceData]:
        """Pop the oldest item from the queue, or return None if empty."""
        if not self._items:
            return None
        return self._items.popleft()

    def peek(self) -> Optional[DeviceData]:
        """View the oldest item without dequeuing."""
        if not self._items:
            return None
        return self._items[0]

    def clear(self) -> None:
        """Clear all contents."""
        self._items.clear()

    def __len__(self) -> int:
        return len(self._items)

    @property
    def is_empty(self) -> bool:
        return len(self._items) == 0

    @property
    def is_full(self) -> bool:
        return len(self._items) >= self._max_items
