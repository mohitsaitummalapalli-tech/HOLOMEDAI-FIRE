# -*- coding: utf-8 -*-
"""Adversarial memory and lifecycle tests for AudioBufferStore."""

import pytest

from holomed.audio.exceptions import (
    AudioCapacityError,
    AudioFrameValidationError,
    AudioLifecycleError,
    AudioResourceIntegrityError,
)
from holomed.audio.models import SlotState
from holomed.audio.store import AudioBufferStore
from tests.unit.audio.conftest import make_test_audio_chunk


def test_buffer_store_lifecycle_free_held_processing_free() -> None:
    """Verify clean slot transitions FREE -> HELD -> PROCESSING -> FREE."""
    store = AudioBufferStore()
    assert store.allocated_count == 0
    assert all(s == SlotState.FREE for s in store.get_slot_states())

    chunk, pcm = make_test_audio_chunk()
    handle = store.allocate_slot(chunk, pcm)
    assert store.allocated_count == 1

    states_after_alloc = store.get_slot_states()
    assert states_after_alloc[0] == SlotState.HELD

    # Transition to PROCESSING
    store.mark_processing(handle)
    states_after_proc = store.get_slot_states()
    assert states_after_proc[0] == SlotState.PROCESSING

    # Release back to FREE
    store.release_slot(handle)
    assert store.allocated_count == 0
    assert store.get_slot_states()[0] == SlotState.FREE


def test_buffer_store_readonly_view_mutation_prevention_d199() -> None:
    """Assert get_readonly_view prevents external caller in-place mutation (D199)."""
    store = AudioBufferStore()
    chunk, pcm = make_test_audio_chunk()
    handle = store.allocate_slot(chunk, pcm)

    view = store.get_readonly_view(handle)
    assert view.readonly is True

    with pytest.raises(TypeError, match="cannot modify read-only memory"):
        view[0] = 0xFF


def test_buffer_store_pool_exhaustion_capacity_error() -> None:
    """Assert slot 17 raises AudioCapacityError while preserving slots 1-16."""
    store = AudioBufferStore()
    handles = []

    # Allocate 16 slots
    for i in range(16):
        chunk, pcm = make_test_audio_chunk(sequence_number=i + 1)
        h = store.allocate_slot(chunk, pcm)
        handles.append(h)

    assert store.allocated_count == 16

    # 17th allocation MUST raise AudioCapacityError
    chunk_17, pcm_17 = make_test_audio_chunk(sequence_number=17)
    with pytest.raises(AudioCapacityError, match="capacity exceeded"):
        store.allocate_slot(chunk_17, pcm_17)

    # Verify existing 16 slots remain intact (non-mutating capacity rejection)
    assert store.allocated_count == 16
    for h in handles:
        view = store.get_readonly_view(h)
        assert len(view) > 0


def test_stale_buffer_handle_access_rejected() -> None:
    """Assert released or bogus handle access raises AudioResourceIntegrityError."""
    store = AudioBufferStore()
    chunk, pcm = make_test_audio_chunk()
    handle = store.allocate_slot(chunk, pcm)
    store.release_slot(handle)

    with pytest.raises(AudioResourceIntegrityError, match="Invalid or expired buffer handle"):
        store.get_readonly_view(handle)

    with pytest.raises(AudioResourceIntegrityError, match="Invalid or expired buffer handle"):
        store.mark_processing(handle)


def test_crc32_checksum_verification() -> None:
    """Assert corrupted raw PCM data raises AudioFrameValidationError."""
    store = AudioBufferStore()
    chunk, pcm = make_test_audio_chunk()

    # Corrupt one byte of raw PCM
    corrupted_pcm = bytearray(pcm)
    corrupted_pcm[0] ^= 0xFF

    with pytest.raises(AudioFrameValidationError, match="CRC32 mismatch"):
        store.allocate_slot(chunk, corrupted_pcm)
