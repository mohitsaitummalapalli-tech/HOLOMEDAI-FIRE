"""Unit and adversarial tests for VisionFrameStore memory pool and lifecycle."""

from __future__ import annotations

import pytest

from holomed.vision.exceptions import (
    VisionCapacityError,
    VisionFrameValidationError,
    VisionResourceIntegrityError,
)
from holomed.vision.models import MAX_BUFFERED_FRAMES, SlotState
from holomed.vision.store import VisionFrameStore
from tests.unit.vision.conftest import make_test_frame


def test_frame_buffer_lifecycle_and_single_ownership() -> None:
    """Verifies FREE -> HELD -> PROCESSING -> FREE lifecycle and single slot ownership."""
    store = VisionFrameStore()
    assert store.free_count == MAX_BUFFERED_FRAMES
    assert all(s == SlotState.FREE for s in store.get_slot_states())

    desc, data = make_test_frame(width=100, height=100)

    # 1. Allocate: FREE -> HELD
    handle_id = store.allocate_slot(desc, data)
    assert handle_id == "vision.slot.0"
    assert store.get_slot_states()[0] == SlotState.HELD
    assert store.free_count == MAX_BUFFERED_FRAMES - 1

    # 2. Access view: HELD -> PROCESSING
    view = store.get_readonly_view(handle_id)
    assert len(view) == len(data)
    assert store.get_slot_states()[0] == SlotState.PROCESSING

    # 3. Release: PROCESSING -> FREE
    store.release_slot(handle_id)
    assert store.get_slot_states()[0] == SlotState.FREE
    assert store.free_count == MAX_BUFFERED_FRAMES


def test_frame_store_readonly_memoryview() -> None:
    """Assert callers receive read-only memoryview and cannot mutate backing buffer."""
    store = VisionFrameStore()
    desc, data = make_test_frame(width=80, height=60)
    handle_id = store.allocate_slot(desc, data)
    view = store.get_readonly_view(handle_id)

    assert view.readonly is True
    with pytest.raises((TypeError, ValueError)):
        view[0] = 255


def test_frame_pool_exhaustion_capacity_error() -> None:
    """Allocating 8 slots exhausts pool; 9th allocation raises VisionCapacityError."""
    store = VisionFrameStore()
    handles = []
    for i in range(MAX_BUFFERED_FRAMES):
        desc, data = make_test_frame(width=64, height=64, sequence_number=i + 1)
        h = store.allocate_slot(desc, data)
        handles.append(h)

    assert store.free_count == 0

    # 9th allocation fails
    desc_extra, data_extra = make_test_frame(width=64, height=64, sequence_number=999)
    with pytest.raises(VisionCapacityError):
        store.allocate_slot(desc_extra, data_extra)

    # Release one slot, allocation succeeds
    store.release_slot(handles[0])
    assert store.free_count == 1
    h_new = store.allocate_slot(desc_extra, data_extra)
    assert h_new == handles[0]


def test_crc32_checksum_mismatch_rejection() -> None:
    """Corrupted data bytes fail CRC32 verification and raise VisionFrameValidationError."""
    store = VisionFrameStore()
    desc, data = make_test_frame(width=64, height=64)
    corrupted_data = bytearray(data)
    corrupted_data[0] ^= 0xFF

    with pytest.raises(VisionFrameValidationError):
        store.allocate_slot(desc, bytes(corrupted_data))
