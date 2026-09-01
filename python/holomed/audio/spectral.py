# -*- coding: utf-8 -*-
"""M02 Audio Subsystem Deterministic Spectral Analysis & PCM Preprocessing.

Provides pure-Python PCM unpacking/canonicalization, deterministic Hann windowing,
Cooley-Tukey Radix-2 real FFT (D197), and acoustic feature extraction.
"""

import cmath
import math
import struct
from typing import Sequence

from holomed.audio.exceptions import AudioFrameValidationError, AudioValidationError
from holomed.audio.models import (
    AcousticFeatures,
    ChannelLayout,
    DEFAULT_FFT_SIZE,
    SampleFormat,
    SUPPORTED_FFT_SIZES,
)


def unpack_pcm_samples(
    raw_pcm: memoryview,
    sample_format: SampleFormat,
    channels: int,
    layout: ChannelLayout,
    frame_count: int,
) -> list[list[float]]:
    """Unpack raw PCM bytes into normalized float32 channel sequences in [-1.0, +1.0]."""
    total_samples = frame_count * channels
    bytes_per_sample = sample_format.bytes_per_sample
    expected_bytes = total_samples * bytes_per_sample

    if len(raw_pcm) != expected_bytes:
        raise AudioFrameValidationError(
            f"PCM byte length mismatch: expected {expected_bytes} bytes for {total_samples} samples, got {len(raw_pcm)}"
        )

    # Decode raw numbers into flat list
    flat_samples: list[float] = []
    if sample_format == SampleFormat.INT16:
        # 16-bit signed integer little-endian: / 32768.0
        fmt = f"<{total_samples}h"
        raw_ints = struct.unpack_from(fmt, raw_pcm)
        flat_samples = [round(max(min(val / 32768.0, 1.0), -1.0), 7) for val in raw_ints]
    elif sample_format == SampleFormat.INT32:
        # 32-bit signed integer little-endian: / 2147483648.0
        fmt = f"<{total_samples}i"
        raw_ints = struct.unpack_from(fmt, raw_pcm)
        flat_samples = [round(max(min(val / 2147483648.0, 1.0), -1.0), 8) for val in raw_ints]
    elif sample_format == SampleFormat.FLOAT32:
        # 32-bit IEEE-754 float little-endian
        fmt = f"<{total_samples}f"
        raw_floats = struct.unpack_from(fmt, raw_pcm)
        for val in raw_floats:
            if math.isnan(val) or math.isinf(val):
                raise AudioFrameValidationError(f"Non-finite sample ({val}) encountered in FLOAT32 PCM stream")
            if not (-1.0001 <= val <= 1.0001):
                raise AudioFrameValidationError(f"FLOAT32 sample {val} outside normalized bound [-1.0, +1.0]")
            norm_val = round(max(min(float(val), 1.0), -1.0), 7)
            if norm_val == -0.0:
                norm_val = 0.0
            flat_samples.append(norm_val)

    # De-interleave or partition by channel
    channel_data: list[list[float]] = [[] for _ in range(channels)]
    if layout == ChannelLayout.INTERLEAVED:
        for f in range(frame_count):
            base = f * channels
            for c in range(channels):
                val = flat_samples[base + c]
                channel_data[c].append(0.0 if val == -0.0 else val)
    elif layout == ChannelLayout.PLANAR:
        for c in range(channels):
            base = c * frame_count
            ch_slice = flat_samples[base : base + frame_count]
            channel_data[c] = [0.0 if v == -0.0 else v for v in ch_slice]

    return channel_data


def generate_hann_window(n: int) -> tuple[float, ...]:
    """Generate deterministic Hann window coefficients of length n."""
    if n <= 1:
        return (1.0,)
    denom = n - 1
    return tuple(round(0.5 * (1.0 - math.cos(2.0 * math.pi * i / denom)), 8) for i in range(n))


def radix2_fft(x: Sequence[complex]) -> list[complex]:
    """Pure-Python Cooley-Tukey Radix-2 Decimation-In-Time FFT."""
    n = len(x)
    if n <= 1:
        return list(x)
    if (n & (n - 1)) != 0:
        raise AudioValidationError(f"FFT size {n} must be a power of two")

    # In-place iterative Cooley-Tukey with bit reversal
    levels = n.bit_length() - 1
    rev_indices = [0] * n
    for i in range(n):
        rev = 0
        temp = i
        for _ in range(levels):
            rev = (rev << 1) | (temp & 1)
            temp >>= 1
        rev_indices[i] = rev

    a = [x[rev_indices[i]] for i in range(n)]

    size = 2
    while size <= n:
        half = size // 2
        angle = -2.0 * math.pi / size
        w_step = cmath.rect(1.0, angle)
        for i in range(0, n, size):
            w = 1.0 + 0j
            for j in range(half):
                u = a[i + j]
                v = a[i + j + half] * w
                a[i + j] = u + v
                a[i + j + half] = u - v
                w *= w_step
        size *= 2

    return a


class AudioSpectralAnalyzer:
    """Deterministic spectral analyzer computing windowed FFT and acoustic features."""

    def __init__(self, fft_size: int = DEFAULT_FFT_SIZE) -> None:
        if fft_size not in SUPPORTED_FFT_SIZES:
            raise AudioValidationError(f"FFT size {fft_size} not in supported set: {sorted(SUPPORTED_FFT_SIZES)}")
        self._fft_size: int = fft_size
        self._hann_window: tuple[float, ...] = generate_hann_window(fft_size)

    @property
    def fft_size(self) -> int:
        return self._fft_size

    def analyze(self, samples: Sequence[float], sample_rate_hz: int) -> tuple[AcousticFeatures, tuple[float, ...]]:
        """Compute spectral transform and acoustic features for a single channel."""
        n_samples = len(samples)
        if n_samples == 0:
            return (
                AcousticFeatures(
                    rms_energy=0.0,
                    zero_crossing_rate=0.0,
                    spectral_centroid_hz=0.0,
                    spectral_bandwidth_hz=0.0,
                    spectral_rolloff_hz=0.0,
                    peak_frequency_hz=0.0,
                ),
                tuple([0.0] * (self._fft_size // 2 + 1)),
            )

        # 1. Zero-padding / truncation to self._fft_size (D194)
        padded_samples: list[float] = [0.0] * self._fft_size
        copy_len = min(n_samples, self._fft_size)
        padded_samples[:copy_len] = samples[:copy_len]

        # 2. Windowing
        windowed: list[complex] = [
            complex(padded_samples[i] * self._hann_window[i], 0.0) for i in range(self._fft_size)
        ]

        # 3. Radix-2 FFT
        fft_out = radix2_fft(windowed)

        # 4. Half-spectrum magnitude and power
        half_n = self._fft_size // 2
        magnitudes: list[float] = [0.0] * (half_n + 1)
        powers: list[float] = [0.0] * (half_n + 1)
        freq_step = sample_rate_hz / float(self._fft_size)

        for k in range(half_n + 1):
            mag = abs(fft_out[k]) / float(self._fft_size)
            magnitudes[k] = round(mag, 7)
            powers[k] = round(mag * mag, 9)

        # 5. RMS Energy on raw input
        sq_sum = sum(s * s for s in samples)
        rms = math.sqrt(sq_sum / float(n_samples))
        rms = max(0.0, min(1.0, round(rms, 6)))

        # 6. Zero Crossing Rate
        crossings = 0
        for i in range(1, n_samples):
            if (samples[i] >= 0.0 and samples[i - 1] < 0.0) or (samples[i] < 0.0 and samples[i - 1] >= 0.0):
                crossings += 1
        zcr = crossings / float(max(1, n_samples - 1))
        zcr = max(0.0, min(1.0, round(zcr, 6)))

        # 7. Spectral Centroid
        total_power = sum(powers)
        if total_power < 1e-12:
            centroid = 0.0
            bandwidth = 0.0
            rolloff = 0.0
            peak_freq = 0.0
        else:
            weighted_sum = sum(k * freq_step * powers[k] for k in range(half_n + 1))
            centroid = round(weighted_sum / total_power, 2)

            # Spectral Bandwidth
            var_sum = sum(((k * freq_step - centroid) ** 2) * powers[k] for k in range(half_n + 1))
            bandwidth = round(math.sqrt(var_sum / total_power), 2)

            # Spectral Rolloff (85% power threshold)
            rolloff_thresh = 0.85 * total_power
            cum_power = 0.0
            rolloff = 0.0
            for k in range(half_n + 1):
                cum_power += powers[k]
                if cum_power >= rolloff_thresh:
                    rolloff = round(k * freq_step, 2)
                    break

            # Peak Frequency (excluding DC bin 0)
            peak_k = 1
            max_p = powers[1] if half_n >= 1 else powers[0]
            for k in range(2, half_n + 1):
                if powers[k] > max_p:
                    max_p = powers[k]
                    peak_k = k
            peak_freq = round(peak_k * freq_step, 2)

        features = AcousticFeatures(
            rms_energy=rms,
            zero_crossing_rate=zcr,
            spectral_centroid_hz=min(centroid, sample_rate_hz / 2.0),
            spectral_bandwidth_hz=min(bandwidth, sample_rate_hz / 2.0),
            spectral_rolloff_hz=min(rolloff, sample_rate_hz / 2.0),
            peak_frequency_hz=min(peak_freq, sample_rate_hz / 2.0),
        )
        return features, tuple(magnitudes)
