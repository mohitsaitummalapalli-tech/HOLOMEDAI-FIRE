# -*- coding: utf-8 -*-
"""Adversarial lifecycle, identity, and dispatcher routing tests for AudioService."""

import pytest

from holomed.audio.exceptions import (
    AudioDeviceIdentityError,
    AudioEpochMismatchError,
    AudioLifecycleError,
    AudioSequenceError,
)
from holomed.audio.models import AudioQuality
from holomed.audio.service import AudioService
from holomed.core.dispatcher import MessageDispatcher
from holomed.protocol.builders import create_command, create_query
from tests.unit.audio.conftest import make_test_audio_chunk


def test_service_lifecycle_and_double_start_rejection(
    test_runtime_context, device_registry_with_microphone
) -> None:
    """Verify clean state transitions and rejection of invalid state transitions."""
    _, dm = device_registry_with_microphone
    service = AudioService(device_manager=dm)
    assert service.state.name == "UNINITIALIZED"

    # start before initialize rejected
    with pytest.raises(AudioLifecycleError, match="must be in INITIALIZED state"):
        service.start()

    service.initialize(test_runtime_context)
    assert service.state.name == "INITIALIZED"

    # initialize again rejected
    with pytest.raises(AudioLifecycleError, match="Cannot initialize"):
        service.initialize(test_runtime_context)

    service.start()
    assert service.state.name == "STARTED"

    # double start rejected
    with pytest.raises(AudioLifecycleError, match="already STARTED"):
        service.start()

    service.stop()
    assert service.state.name == "STOPPED"

    # idempotent stop
    service.stop()
    assert service.state.name == "STOPPED"


def test_structural_resources_exactly_four(
    test_runtime_context, device_registry_with_microphone
) -> None:
    """Assert service owns exactly 4 structural resources under OwnedResourceSet."""
    _, dm = device_registry_with_microphone
    service = AudioService(device_manager=dm)
    service.initialize(test_runtime_context)

    assert service._resources is not None
    assert len(service._resources.outstanding_handles) == 4
    handle_ids = {h.resource_id for h in service._resources.outstanding_handles}
    assert handle_ids == {"audio.store", "audio.pipeline", "audio.tracker", "audio.metrics"}

    service.start()
    service.stop()
    assert service._resources.is_empty is True


def test_physical_id_validation_against_registry(
    test_runtime_context, device_registry_with_microphone
) -> None:
    """Assert device physical_id mismatch raises AudioDeviceIdentityError."""
    _, dm = device_registry_with_microphone
    service = AudioService(device_manager=dm)
    service.initialize(test_runtime_context)
    service.start()

    # Valid physical_id succeeds
    chunk_valid, pcm_valid = make_test_audio_chunk(device_id="mic_01", physical_id="usb://mic_array_1")
    res = service.ingest_chunk(chunk_valid, pcm_valid)
    assert res.quality == AudioQuality.HEALTHY

    # Mismatched physical_id raises AudioDeviceIdentityError
    chunk_invalid, pcm_invalid = make_test_audio_chunk(device_id="mic_01", physical_id="usb://tampered_id")
    with pytest.raises(AudioDeviceIdentityError, match="Physical ID mismatch"):
        service.ingest_chunk(chunk_invalid, pcm_invalid)

    # Unknown device raises AudioDeviceIdentityError
    chunk_unknown, pcm_unknown = make_test_audio_chunk(device_id="ghost_mic", physical_id="usb://mic_array_1")
    with pytest.raises(AudioDeviceIdentityError, match="not registered"):
        service.ingest_chunk(chunk_unknown, pcm_unknown)


def test_session_sequence_monotonic_enforcement_d200(
    test_runtime_context, device_registry_with_microphone
) -> None:
    """Assert duplicate or non-monotonic sequence numbers raise AudioSequenceError (D200)."""
    _, dm = device_registry_with_microphone
    service = AudioService(device_manager=dm)
    service.initialize(test_runtime_context)
    service.start()

    chunk1, pcm1 = make_test_audio_chunk(sequence_number=10)
    service.ingest_chunk(chunk1, pcm1)

    chunk2, pcm2 = make_test_audio_chunk(sequence_number=11)
    service.ingest_chunk(chunk2, pcm2)

    # Duplicate sequence number (11 <= 11)
    chunk_dup, pcm_dup = make_test_audio_chunk(sequence_number=11)
    with pytest.raises(AudioSequenceError, match="Non-monotonic sequence number"):
        service.ingest_chunk(chunk_dup, pcm_dup)

    # Regressing sequence number (9 <= 11)
    chunk_old, pcm_old = make_test_audio_chunk(sequence_number=9)
    with pytest.raises(AudioSequenceError, match="Non-monotonic sequence number"):
        service.ingest_chunk(chunk_old, pcm_old)


def test_epoch_mismatch_rejected(
    test_runtime_context, device_registry_with_microphone
) -> None:
    """Assert chunk with mismatched epoch_id is rejected with AudioEpochMismatchError."""
    _, dm = device_registry_with_microphone
    service = AudioService(device_manager=dm)
    service.initialize(test_runtime_context)  # epoch_id = 1
    service.start()

    chunk_wrong_epoch, pcm = make_test_audio_chunk(epoch_id=2)
    with pytest.raises(AudioEpochMismatchError, match="does not match active epoch"):
        service.ingest_chunk(chunk_wrong_epoch, pcm)


def test_dispatcher_queries_and_commands(
    test_runtime_context, device_registry_with_microphone
) -> None:
    """Assert all 4 static dispatcher query and command routes respond correctly."""
    _, dm = device_registry_with_microphone
    dispatcher = MessageDispatcher()
    dispatcher.initialize(test_runtime_context)

    service = AudioService(device_manager=dm, dispatcher=dispatcher)
    service.initialize(test_runtime_context)
    service.start()
    dispatcher.start()

    # 1. audio.pipeline.status
    q_status = create_query("audio.pipeline.status", "test_client", payload={})
    res_status = service.handle_status_query(q_status)
    assert res_status.payload["service_name"] == "audio_service"
    assert res_status.payload["state"] == "STARTED"

    # 2. audio.pipeline.audit
    q_audit = create_query("audio.pipeline.audit", "test_client", payload={})
    res_audit = service.handle_audit_query(q_audit)
    assert res_audit.payload["is_consistent"] is True

    # 3. audio.tracker.tracks
    q_tracks = create_query("audio.tracker.tracks", "test_client", payload={})
    res_tracks = service.handle_tracks_query(q_tracks)
    assert "active_tracks_count" in res_tracks.payload

    # 4. audio.pipeline.reset
    cmd_reset = create_command("audio.pipeline.reset", "test_client", payload={"epoch_id": 1})
    res_reset = service.handle_reset_command(cmd_reset)
    assert res_reset.payload["reset_completed"] is True
