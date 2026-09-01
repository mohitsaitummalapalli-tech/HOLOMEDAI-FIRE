# -*- coding: utf-8 -*-
"""Adversarial and domain tests for M02 Audio models and geometry."""

import pytest

from holomed.audio.exceptions import AudioCapacityError, AudioValidationError
from holomed.audio.models import (
    AcousticDirection,
    AcousticFeatures,
    AudioChunk,
    ChannelLayout,
    MicrophonePosition,
    SampleFormat,
    serialize_audio_payload,
)
from holomed.audio.spatial import validate_microphone_geometry
from tests.unit.audio.conftest import make_test_audio_chunk


def test_audio_chunk_valid_instantiation() -> None:
    """Verify valid chunk descriptor construction and properties."""
    chunk, pcm_bytes = make_test_audio_chunk(sample_rate_hz=16000, channels=2, duration_ms=20)
    assert chunk.sample_rate_hz == 16000
    assert chunk.channels == 2
    assert chunk.frame_count == 320
    assert chunk.payload_bytes == len(pcm_bytes)


def test_audio_chunk_invalid_sample_rate_rejected() -> None:
    """Assert non-enumerated sample rates (e.g. 12000, 96000) are rejected."""
    chunk, pcm = make_test_audio_chunk()
    with pytest.raises(AudioValidationError, match="not in supported rates"):
        AudioChunk(
            chunk_id=chunk.chunk_id,
            device_id=chunk.device_id,
            physical_id=chunk.physical_id,
            sequence_number=1,
            timestamp_utc=chunk.timestamp_utc,
            epoch_id=1,
            sample_rate_hz=96000,  # Unsupported rate
            channels=2,
            sample_format=SampleFormat.FLOAT32,
            channel_layout=ChannelLayout.INTERLEAVED,
            frame_count=chunk.frame_count,
            payload_bytes=chunk.payload_bytes,
            checksum_crc32=chunk.checksum_crc32,
            buffer_handle_id="",
        )


def test_audio_chunk_channel_bounds_rejected() -> None:
    """Assert channel count < 1 or > 8 is rejected."""
    chunk, pcm = make_test_audio_chunk()
    with pytest.raises(AudioValidationError, match="channels 9 out of range"):
        AudioChunk(
            chunk_id=chunk.chunk_id,
            device_id=chunk.device_id,
            physical_id=chunk.physical_id,
            sequence_number=1,
            timestamp_utc=chunk.timestamp_utc,
            epoch_id=1,
            sample_rate_hz=16000,
            channels=9,  # Exceeds MAX_AUDIO_CHANNELS = 8
            sample_format=SampleFormat.FLOAT32,
            channel_layout=ChannelLayout.INTERLEAVED,
            frame_count=chunk.frame_count,
            payload_bytes=chunk.payload_bytes,
            checksum_crc32=chunk.checksum_crc32,
            buffer_handle_id="",
        )


def test_audio_chunk_duration_boundary_rejection() -> None:
    """Assert duration < 5ms or > 100ms is rejected."""
    chunk, pcm = make_test_audio_chunk(sample_rate_hz=16000)
    # At 16 kHz: 5ms = 80 frames, 100ms = 1600 frames.
    with pytest.raises(AudioValidationError, match="out of duration bounds"):
        AudioChunk(
            chunk_id=chunk.chunk_id,
            device_id=chunk.device_id,
            physical_id=chunk.physical_id,
            sequence_number=1,
            timestamp_utc=chunk.timestamp_utc,
            epoch_id=1,
            sample_rate_hz=16000,
            channels=1,
            sample_format=SampleFormat.FLOAT32,
            channel_layout=ChannelLayout.INTERLEAVED,
            frame_count=40,  # 2.5ms < 5ms
            payload_bytes=160,
            checksum_crc32=chunk.checksum_crc32,
            buffer_handle_id="",
        )


def test_microphone_position_bounds_and_finite_validation() -> None:
    """Assert coordinates outside [-2.0, 2.0] or non-finite are rejected."""
    # Valid
    m = MicrophonePosition(microphone_id=0, x_m=0.05, y_m=0.0, z_m=0.0)
    assert m.x_m == 0.05

    # Out of bounds
    with pytest.raises(AudioValidationError, match="exceeds physical bound"):
        MicrophonePosition(microphone_id=1, x_m=2.5, y_m=0.0, z_m=0.0)

    # Non-finite
    with pytest.raises(AudioValidationError, match="finite float"):
        MicrophonePosition(microphone_id=1, x_m=float("nan"), y_m=0.0, z_m=0.0)


def test_microphone_coincidence_rejection_d195() -> None:
    """Assert pairs closer than 5mm or identical are rejected (D195)."""
    m1 = MicrophonePosition(microphone_id=0, x_m=0.0, y_m=0.0, z_m=0.0)
    m2 = MicrophonePosition(microphone_id=1, x_m=0.002, y_m=0.0, z_m=0.0)  # 2mm < 5mm
    with pytest.raises(AudioValidationError, match="below minimum threshold"):
        validate_microphone_geometry([m1, m2])


def test_acoustic_direction_normalization_d196() -> None:
    """Assert -180.0 azimuth maps canonically to +180.0 and -0.0 maps to 0.0 (D196)."""
    dir1 = AcousticDirection(azimuth_deg=-180.0, elevation_deg=-0.0, confidence=0.95)
    assert dir1.azimuth_deg == 180.0
    assert dir1.elevation_deg == 0.0

    dir2 = AcousticDirection(azimuth_deg=45.0, elevation_deg=15.0, confidence=0.8)
    assert dir2.azimuth_deg == 45.0
    assert dir2.elevation_deg == 15.0


def test_acoustic_features_non_finite_rejection() -> None:
    """Assert non-finite values in features raise AudioValidationError."""
    with pytest.raises(AudioValidationError, match="must be a finite float"):
        AcousticFeatures(
            rms_energy=float("nan"),
            zero_crossing_rate=0.1,
            spectral_centroid_hz=1000.0,
            spectral_bandwidth_hz=500.0,
            spectral_rolloff_hz=2000.0,
            peak_frequency_hz=440.0,
        )


def test_payload_serialization_64k_boundary() -> None:
    """Assert payloads > 64 KiB raise AudioCapacityError."""
    small = {"key": "val", "count": 42}
    clean = serialize_audio_payload(small)
    assert clean["key"] == "val"

    # Enormous payload
    huge = {"large": "x" * 70000}
    with pytest.raises(AudioCapacityError, match="exceeds MAX_AUDIO_PAYLOAD_BYTES"):
        serialize_audio_payload(huge)
