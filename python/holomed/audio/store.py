# -*- coding: utf-8 -*-
"""M02 Audio Subsystem Bounded AudioBufferStore.

Manages exactly 16 pre-allocated buffer slots (each 256 KiB, totaling 4 MiB),
implementing strict FREE -> HELD -> PROCESSING -> FREE lifecycle transitions
and exposing immutable read-only memory views (D199).
"""

import binascii
from typing import Optional, Union
import uuid

from holomed.audio.exceptions import (
    AudioCapacityError,
    AudioFrameValidationError,
    AudioLifecycleError,
    AudioResourceIntegrityError,
)
from holomed.audio.models import (
    AudioChunk,
    MAX_AUDIO_CHUNK_BYTES,
    MAX_BUFFERED_AUDIO_CHUNKS,
    SlotState,
)


class _BufferSlot:
    """Internal representation of a pre-allocated raw PCM slot."""

    def __init__(self, slot_id: int) -> None:
        self.slot_id: int = slot_id
        self.buffer: bytearray = bytearray(MAX_AUDIO_CHUNK_BYTES)
        self.state: SlotState = SlotState.FREE
        self.active_handle_id: Optional[str] = None
        self.payload_bytes: int = 0
        self.checksum_crc32: int = 0
        self.chunk_id: Optional[str] = None


class AudioBufferStore:
    """Deterministic, bounded memory-plane buffer storage for raw PCM audio."""

    def __init__(self) -> None:
        self._slots: list[_BufferSlot] = [_BufferSlot(i) for i in range(MAX_BUFFERED_AUDIO_CHUNKS)]
        self._handles: dict[str, int] = {}  # handle_id -> slot_id

    @property
    def capacity(self) -> int:
        return MAX_BUFFERED_AUDIO_CHUNKS

    @property
    def allocated_count(self) -> int:
        return sum(1 for s in self._slots if s.state != SlotState.FREE)

    def allocate_slot(self, chunk: AudioChunk, raw_pcm: Union[bytes, bytearray, memoryview]) -> str:
        """Store raw PCM data in an available slot, validating payload size and CRC32."""
        pcm_len = len(raw_pcm)
        if pcm_len != chunk.payload_bytes:
            raise AudioFrameValidationError(
                f"Supplied raw PCM length ({pcm_len}) does not match chunk.payload_bytes ({chunk.payload_bytes})"
            )
        if pcm_len > MAX_AUDIO_CHUNK_BYTES:
            raise AudioCapacityError(
                f"Supplied PCM length ({pcm_len} bytes) exceeds MAX_AUDIO_CHUNK_BYTES ({MAX_AUDIO_CHUNK_BYTES})"
            )

        # Validate CRC32
        actual_crc = binascii.crc32(raw_pcm)
        if actual_crc != chunk.checksum_crc32:
            raise AudioFrameValidationError(
                f"CRC32 mismatch for chunk {chunk.chunk_id}: expected {chunk.checksum_crc32:#010x}, calculated {actual_crc:#010x}"
            )

        # Find first FREE slot
        target_slot: Optional[_BufferSlot] = None
        for slot in self._slots:
            if slot.state == SlotState.FREE:
                target_slot = slot
                break

        if target_slot is None:
            raise AudioCapacityError(
                f"AudioBufferStore capacity exceeded ({MAX_BUFFERED_AUDIO_CHUNKS} chunks). "
                f"No FREE slots available."
            )

        # Copy data into pre-allocated memory
        target_slot.buffer[:pcm_len] = raw_pcm
        target_slot.payload_bytes = pcm_len
        target_slot.checksum_crc32 = actual_crc
        target_slot.chunk_id = chunk.chunk_id

        # Generate unique handle
        handle_id = f"aud_buf_{uuid.uuid4().hex[:12]}_s{target_slot.slot_id}"
        target_slot.active_handle_id = handle_id
        target_slot.state = SlotState.HELD
        self._handles[handle_id] = target_slot.slot_id
        return handle_id

    def mark_processing(self, handle_id: str) -> None:
        """Transition slot from HELD to PROCESSING."""
        slot = self._get_slot_by_handle(handle_id)
        if slot.state != SlotState.HELD:
            raise AudioLifecycleError(
                f"Cannot mark slot {slot.slot_id} as PROCESSING: current state is {slot.state.name}"
            )
        slot.state = SlotState.PROCESSING

    def release_slot(self, handle_id: str) -> None:
        """Release slot back to FREE, invalidating the handle and zeroing memory."""
        slot = self._get_slot_by_handle(handle_id)
        if slot.state == SlotState.FREE:
            raise AudioLifecycleError(f"Slot {slot.slot_id} is already FREE")

        # Zero out payload slice to maintain hygiene
        slot.buffer[: slot.payload_bytes] = b"\x00" * slot.payload_bytes
        slot.payload_bytes = 0
        slot.checksum_crc32 = 0
        slot.chunk_id = None
        slot.active_handle_id = None
        slot.state = SlotState.FREE

        if handle_id in self._handles:
            del self._handles[handle_id]

    def get_readonly_view(self, handle_id: str) -> memoryview:
        """Return a read-only memoryview slice of the occupied slot (D199)."""
        slot = self._get_slot_by_handle(handle_id)
        if slot.state == SlotState.FREE:
            raise AudioResourceIntegrityError(f"Cannot read from FREE slot {slot.slot_id}")
        return memoryview(slot.buffer[: slot.payload_bytes]).toreadonly()

    def get_slot_states(self) -> tuple[SlotState, ...]:
        """Return current states of all 16 slots."""
        return tuple(s.state for s in self._slots)

    def clear(self) -> None:
        """Force-release all slots to FREE state."""
        for slot in self._slots:
            if slot.state != SlotState.FREE:
                slot.buffer[: slot.payload_bytes] = b"\x00" * slot.payload_bytes
                slot.payload_bytes = 0
                slot.checksum_crc32 = 0
                slot.chunk_id = None
                slot.active_handle_id = None
                slot.state = SlotState.FREE
        self._handles.clear()

    def _get_slot_by_handle(self, handle_id: str) -> _BufferSlot:
        slot_idx = self._handles.get(handle_id)
        if slot_idx is None:
            raise AudioResourceIntegrityError(f"Invalid or expired buffer handle: {handle_id}")
        slot = self._slots[slot_idx]
        if slot.active_handle_id != handle_id:
            raise AudioResourceIntegrityError(
                f"Handle mismatch: handle {handle_id} does not match active slot handle {slot.active_handle_id}"
            )
        return slot
