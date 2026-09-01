# -*- coding: utf-8 -*-
"""Adversarial and accuracy tests for pure-Python spectral analysis and FFT (D194, D197)."""

import math
import pytest

from holomed.audio.exceptions import AudioValidationError
from holomed.audio.spectral import (
    AudioSpectralAnalyzer,
    generate_hann_window,
    radix2_fft,
)


def test_radix2_fft_power_of_two_enforcement() -> None:
    """Assert non-power of two input size raises AudioValidationError."""
    with pytest.raises(AudioValidationError, match="power of two"):
        radix2_fft([1.0 + 0j] * 100)


def test_fft_zero_padding_for_short_chunks_d194() -> None:
    """Assert 40-sample chunk (5ms @ 8kHz) successfully zero-pads to 256/1024 without error (D194)."""
    analyzer = AudioSpectralAnalyzer(fft_size=256)
    short_samples = [0.5 * math.sin(2.0 * math.pi * 440.0 * (i / 8000.0)) for i in range(40)]
    features, magnitudes = analyzer.analyze(short_samples, 8000)

    assert len(magnitudes) == 129  # N/2 + 1
    assert features.rms_energy > 0.0
    assert features.spectral_centroid_hz > 0.0


def test_hann_window_determinism() -> None:
    """Verify Hann window symmetry, boundary conditions, and deterministic generation."""
    w = generate_hann_window(512)
    assert len(w) == 512
    assert w[0] == 0.0
    assert w[-1] == 0.0
    assert abs(w[256] - 1.0) < 1e-4  # Peak at midpoint for even length
    # Symmetry check
    for i in range(256):
        assert abs(w[i] - w[511 - i]) < 1e-7

    # Exact peak at midpoint for odd length
    w_odd = generate_hann_window(513)
    assert w_odd[256] == 1.0


def test_rms_energy_bounds() -> None:
    """Verify RMS energy calculation on silence and unit sine wave."""
    analyzer = AudioSpectralAnalyzer(fft_size=512)

    # Silence
    silence = [0.0] * 512
    feat_silence, _ = analyzer.analyze(silence, 16000)
    assert feat_silence.rms_energy == 0.0

    # Sine wave with amplitude 0.8: RMS should be approx 0.8 / sqrt(2) = 0.5657
    sine = [0.8 * math.sin(2.0 * math.pi * 440.0 * (i / 16000.0)) for i in range(512)]
    feat_sine, _ = analyzer.analyze(sine, 16000)
    assert abs(feat_sine.rms_energy - (0.8 / math.sqrt(2))) < 0.02


def test_spectral_peak_frequency_detection() -> None:
    """Verify peak frequency detection for a 1000 Hz pure tone at 16 kHz."""
    analyzer = AudioSpectralAnalyzer(fft_size=1024)
    # 1024 samples @ 16 kHz gives bin resolution of 16000 / 1024 = 15.625 Hz
    tone = [math.sin(2.0 * math.pi * 1000.0 * (i / 16000.0)) for i in range(1024)]
    features, _ = analyzer.analyze(tone, 16000)
    assert abs(features.peak_frequency_hz - 1000.0) <= 20.0


def test_spectral_output_determinism() -> None:
    """Verify repeated runs on identical input produce identical output."""
    analyzer = AudioSpectralAnalyzer(fft_size=512)
    tone = [0.5 * math.sin(i * 0.1) for i in range(512)]
    f1, m1 = analyzer.analyze(tone, 16000)
    f2, m2 = analyzer.analyze(tone, 16000)
    assert f1 == f2
    assert m1 == m2
