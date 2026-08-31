"""Unit tests for M00.7 BoundedDeviceDataQueue operations and limits."""

import pytest

from holomed.devices.data.exceptions import DeviceDataCapacityError
from holomed.devices.data.models import DeviceData, DeviceDataKind
from holomed.devices.data.queue import BoundedDeviceDataQueue
from holomed.devices.models import DeviceType


def make_dummy_item(seq: int) -> DeviceData:
    return DeviceData(
        device_id="cam1",
        physical_id="USB:1",
        device_type=DeviceType.RGB_CAMERA,
        kind=DeviceDataKind.OBSERVATION,
        sequence_number=seq,
        timestamp_utc="2026-08-31T12:00:00Z",
        payload={"index": seq},
    )


def test_queue_fifo_order() -> None:
    queue = BoundedDeviceDataQueue(max_items=10)
    item1 = make_dummy_item(1)
    item2 = make_dummy_item(2)

    assert queue.is_empty
    queue.put(item1)
    queue.put(item2)

    assert len(queue) == 2
    assert queue.peek() is item1

    popped1 = queue.get()
    assert popped1 is item1
    popped2 = queue.get()
    assert popped2 is item2
    assert queue.get() is None


def test_queue_capacity_overflow_rejection_is_non_mutating() -> None:
    queue = BoundedDeviceDataQueue(max_items=3)
    for i in range(1, 4):
        queue.put(make_dummy_item(i))

    assert len(queue) == 3
    assert queue.is_full

    # 4th item rejected with DeviceDataCapacityError
    with pytest.raises(DeviceDataCapacityError, match="capacity"):
        queue.put(make_dummy_item(4))

    # Existing queue contents remain untouched (D149)
    assert len(queue) == 3
    assert queue.peek().sequence_number == 1
