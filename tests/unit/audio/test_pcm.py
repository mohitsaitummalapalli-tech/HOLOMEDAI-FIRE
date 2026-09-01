# -*- coding: utf-8 -*-
"""Adversarial tests for PCM unpacking, normalization, and layout canonicalization."""

import struct
import pytest

from holomed.audio.exceptions import AudioFrameValidationError
from holomed.audio.models import ChannelLayout, SampleFormat
from holomed.audio.spectral import unpack_pcm_samples


def test_pcm_int16_normalization() -> None:
    """Verify signed 16-bit PCM maps correctly to [-1.0, +1.0]."""
    raw = struct.pack("<4h", -32768, 0, 16384, 32767)
    channels = unpack_pcm_samples(memoryview(raw), SampleFormat.INT16, 1, ChannelLayout.INTERLEAVED, 4)
    assert len(channels) == 1
    samples = channels[0]
    assert samples[0] == -1.0
    assert samples[1] == 0.0
    assert abs(samples[2] - 0.5) < 1e-4
    assert abs(samples[3] - 1.0) < 1e-4


def test_pcm_int32_normalization() -> None:
    """Verify signed 32-bit PCM maps correctly to [-1.0, +1.0]."""
    raw = struct.pack("<3i", -2147483648, 0, 2147483647)
    channels = unpack_pcm_samples(memoryview(raw), SampleFormat.INT32, 1, ChannelLayout.INTERLEAVED, 3)
    assert channels[0][0] == -1.0
    assert channels[0][1] == 0.0
    assert abs(channels[0][2] - 1.0) < 1e-6


def test_pcm_float32_out_of_bounds_rejected() -> None:
    """Assert FLOAT32 samples outside [-1.0, 1.0] raise AudioFrameValidationError."""
    raw = struct.pack("<2f", 0.5, 1.5)  # 1.5 > 1.0
    with pytest.raises(AudioFrameValidationError, match="outside normalized bound"):
        unpack_pcm_samples(memoryview(raw), SampleFormat.FLOAT32, 1, ChannelLayout.INTERLEAVED, 2)


def test_pcm_nan_and_infinity_rejected() -> None:
    """Assert NaN, +Infinity, and -Infinity are rejected."""
    raw_nan = struct.pack("<1f", float("nan"))
    with pytest.raises(AudioFrameValidationError, match="Non-finite sample"):
        unpack_pcm_samples(memoryview(raw_nan), SampleFormat.FLOAT32, 1, ChannelLayout.INTERLEAVED, 1)

    raw_inf = struct.pack("<1f", float("inf"))
    with pytest.raises(AudioFrameValidationError, match="Non-finite sample"):
        unpack_pcm_samples(memoryview(raw_inf), SampleFormat.FLOAT32, 1, ChannelLayout.INTERLEAVED, 1)


def test_pcm_negative_zero_normalization() -> None:
    """Assert -0.0 maps canonically to +0.0."""
    raw = struct.pack("<1f", -0.0)
    channels = unpack_pcm_samples(memoryview(raw), SampleFormat.FLOAT32, 1, ChannelLayout.INTERLEAVED, 1)
    val = channels[0][0]
    assert val == 0.0
    # Check bitwise sign is positive
    assert math_copysign_is_positive(val)


def math_copysign_is_positive(v: float) -> bool:
    import math
    return math.copysign(1.0, v) == 1.0


def test_pcm_interleaved_vs_planar_layout() -> None:
    """Assert interleaved and planar representations yield matching channel vectors."""
    # 2 channels, 3 frames
    # Channel 0: [0.1, 0.2, 0.3]
    # Channel 1: [0.4, 0.5, 0.6]

    # Interleaved: [0.1, 0.4, 0.2, 0.5, 0.3, 0.6]
    interleaved_bytes = struct.pack("<6f", 0.1, 0.4, 0.2, 0.5, 0.3, 0.6)
    ch_interleaved = unpack_pcm_samples(
        memoryview(interleaved_bytes), SampleFormat.FLOAT32, 2, ChannelLayout.INTERLEAVED, 3
    )

    # Planar: [0.1, 0.2, 0.3, 0.4, 0.5, 0.6]
    planar_bytes = struct.pack("<6f", 0.1, 0.2, 0.3, 0.4, 0.5, 0.6)
    ch_planar = unpack_pcm_samples(
        memoryview(planar_bytes), SampleFormat.FLOAT32, 2, ChannelLayout.PLANAR, 3
    )

    assert ch_interleaved[0] == ch_planar[0]
    assert ch_interleaved[1] == ch_planar[1]
