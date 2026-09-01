# -*- coding: utf-8 -*-
"""Adversarial and geometry tests for SpatialAudioEstimator (D195, D196, D198)."""

import math
from holomed.audio.models import MicrophonePosition
from holomed.audio.spatial import SpatialAudioEstimator


def test_insufficient_microphones_spatial_fallback() -> None:
    """Assert 1-microphone setup returns None rather than crashing."""
    m0 = MicrophonePosition(microphone_id=0, x_m=0.0, y_m=0.0, z_m=0.0)
    estimator = SpatialAudioEstimator([m0])
    res = estimator.estimate_direction([[0.5] * 100], 16000)
    assert res is None


def test_synthetic_broadside_doa_estimation() -> None:
    """Assert synchronized (broadside) signal across stereo pair yields azimuth = 0.0."""
    m0 = MicrophonePosition(microphone_id=0, x_m=-0.05, y_m=0.0, z_m=0.0)
    m1 = MicrophonePosition(microphone_id=1, x_m=0.05, y_m=0.0, z_m=0.0)
    estimator = SpatialAudioEstimator([m0, m1])

    # Identical phase in both channels
    tone = [0.8 * math.sin(2.0 * math.pi * 500.0 * (i / 16000.0)) for i in range(320)]
    direction = estimator.estimate_direction([tone, tone], 16000)

    assert direction is not None
    assert abs(direction.azimuth_deg) < 1.0
    assert direction.confidence > 0.8


def test_supersonic_tdoa_gating_d198() -> None:
    """Assert non-physical time delay is clamped without throwing math domain error (D198)."""
    # 10 cm baseline: max physical delay is 0.10 / 343 = 0.291 ms (~4.6 samples @ 16 kHz)
    m0 = MicrophonePosition(microphone_id=0, x_m=-0.05, y_m=0.0, z_m=0.0)
    m1 = MicrophonePosition(microphone_id=1, x_m=0.05, y_m=0.0, z_m=0.0)
    estimator = SpatialAudioEstimator([m0, m1])

    # Artificially delay channel 1 by 50 samples (> 10x physical propagation limit)
    tone = [0.8 * math.sin(2.0 * math.pi * 500.0 * (i / 16000.0)) for i in range(320)]
    delayed = [0.0] * 50 + tone[:-50]

    # Must clamp cleanly without math domain error
    direction = estimator.estimate_direction([tone, delayed], 16000)
    assert direction is not None
    assert -180.0 < direction.azimuth_deg <= 180.0
    assert -90.0 <= direction.elevation_deg <= 90.0
