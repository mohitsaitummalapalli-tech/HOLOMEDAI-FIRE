"""Bounded in-memory frame buffer storage and lifecycle management for M01."""

from __future__ import annotations

from typing import Optional, Sequence
import zlib

from holomed.vision.exceptions import (
    VisionCapacityError,
    VisionFrameValidationError,
    VisionResourceIntegrityError,
    VisionValidationError,
)
from holomed.vision.models import (
    MAX_BUFFERED_FRAMES,
    MAX_FRAME_BYTES,
    FrameDescriptor,
    SlotState,
)


class _BufferSlot:
    """Internal slot holding pre-allocated memory buffer and state."""

    __slots__ = ("slot_id", "state", "buffer", "descriptor", "allocated_bytes")

    def __init__(self, slot_id: int) -> None:
        self.slot_id: int = slot_id
        self.state: SlotState = SlotState.FREE
        self.buffer: bytearray = bytearray(MAX_FRAME_BYTES)
        self.descriptor: Optional[FrameDescriptor] = None
        self.allocated_bytes: int = 0

    def reset(self) -> None:
        self.state = SlotState.FREE
        self.descriptor = None
        self.allocated_bytes = 0


class VisionFrameStore:
    """Authoritative owner of in-memory raw frame buffers for the vision subsystem.
    
    Enforces:
    - Pre-allocated, bounded memory (8 slots x 3 MiB).
    - Strict slot lifecycle: FREE -> HELD -> PROCESSING -> FREE.
    - Single ownership per slot; descriptors cannot outlive backing buffers.
    - Zero-leakage: callers receive strictly read-only memoryviews.
    """

    def __init__(self) -> None:
        self._slots: list[_BufferSlot] = [_BufferSlot(i) for i in range(MAX_BUFFERED_FRAMES)]
        self._in_transaction: bool = False

    def allocate_slot(self, descriptor: FrameDescriptor, data_bytes: bytes) -> str:
        """Copy incoming frame bytes into an available slot and mark HELD."""
        if not isinstance(descriptor, FrameDescriptor):
            raise VisionValidationError(f"Expected FrameDescriptor, got {type(descriptor).__name__}")
        if not isinstance(data_bytes, (bytes, bytearray, memoryview)):
            raise VisionFrameValidationError(f"data_bytes must be bytes-like, got {type(data_bytes).__name__}")

        if len(data_bytes) != descriptor.total_bytes:
            raise VisionFrameValidationError(
                f"Payload size ({len(data_bytes)}) does not match descriptor total_bytes ({descriptor.total_bytes})"
            )

        # Verify CRC32 checksum
        computed_crc = zlib.crc32(data_bytes)
        if computed_crc != descriptor.checksum_crc32:
            raise VisionFrameValidationError(
                f"CRC32 mismatch: descriptor={descriptor.checksum_crc32}, computed={computed_crc}"
            )

        if self._in_transaction:
            raise VisionResourceIntegrityError("Reentrant call to allocate_slot prohibited")
        self._in_transaction = True
        try:
            # Find first available FREE slot
            target_slot: Optional[_BufferSlot] = None
            for slot in self._slots:
                if slot.state == SlotState.FREE:
                    target_slot = slot
                    break

            if target_slot is None:
                raise VisionCapacityError(
                    f"Frame buffer pool exhausted ({MAX_BUFFERED_FRAMES}/{MAX_BUFFERED_FRAMES} slots allocated)"
                )

            # Copy data into slot pre-allocated buffer
            target_slot.buffer[: len(data_bytes)] = data_bytes
            target_slot.allocated_bytes = len(data_bytes)
            target_slot.descriptor = descriptor
            target_slot.state = SlotState.HELD
            return f"vision.slot.{target_slot.slot_id}"
        finally:
            self._in_transaction = False

    def get_readonly_view(self, buffer_handle_id: str) -> memoryview:
        """Obtain a read-only memoryview of the slot's allocated payload."""
        slot = self._resolve_slot(buffer_handle_id)
        if slot.state not in (SlotState.HELD, SlotState.PROCESSING):
            raise VisionResourceIntegrityError(
                f"Cannot access slot {buffer_handle_id} in state {slot.state.name} (must be HELD or PROCESSING)"
            )
        slot.state = SlotState.PROCESSING
        return memoryview(slot.buffer)[: slot.allocated_bytes].toreadonly()

    def release_slot(self, buffer_handle_id: str) -> None:
        """Release slot memory back to the pool, transitioning state to FREE."""
        slot = self._resolve_slot(buffer_handle_id)
        slot.reset()

    def clear(self) -> None:
        """Reset all slots back to FREE."""
        for slot in self._slots:
            slot.reset()

    def get_slot_states(self) -> tuple[SlotState, ...]:
        """Return immutable snapshot of all 8 slot lifecycle states."""
        return tuple(slot.state for slot in self._slots)

    @property
    def free_count(self) -> int:
        return sum(1 for slot in self._slots if slot.state == SlotState.FREE)

    def _resolve_slot(self, buffer_handle_id: str) -> _BufferSlot:
        if not isinstance(buffer_handle_id, str) or not buffer_handle_id.startswith("vision.slot."):
            raise VisionValidationError(f"Invalid buffer handle syntax: {buffer_handle_id!r}")
        try:
            slot_id = int(buffer_handle_id.split(".")[-1])
            if not (0 <= slot_id < MAX_BUFFERED_FRAMES):
                raise ValueError()
            return self._slots[slot_id]
        except Exception as e:
            raise VisionValidationError(f"Cannot resolve buffer handle {buffer_handle_id!r}: {e}") from e
