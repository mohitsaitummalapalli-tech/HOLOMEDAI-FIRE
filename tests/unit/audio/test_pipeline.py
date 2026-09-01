# -*- coding: utf-8 -*-
"""Adversarial and stage isolation tests for AudioPipeline."""

import time
import pytest

from holomed.audio.models import (
    AudioQuality,
    MicrophonePosition,
)
from holomed.audio.pipeline import AudioPipeline
from holomed.audio.store import AudioBufferStore
from holomed.runtime.logging import SecretFilter
from tests.unit.audio.conftest import make_test_audio_chunk


def test_pipeline_8_stages_execution() -> None:
    """Verify clean 8-stage pipeline run produces valid features and healthy quality."""
    m0 = MicrophonePosition(microphone_id=0, x_m=-0.05, y_m=0.0, z_m=0.0)
    m1 = MicrophonePosition(microphone_id=1, x_m=0.05, y_m=0.0, z_m=0.0)
    pipeline = AudioPipeline(microphones=[m0, m1])
    store = AudioBufferStore()

    chunk, pcm = make_test_audio_chunk(channels=2, duration_ms=20, freq_hz=500.0)
    handle = store.allocate_slot(chunk, pcm)
    view = store.get_readonly_view(handle)

    result, tracks = pipeline.process(chunk, view)
    assert result.chunk_id == chunk.chunk_id
    assert result.quality == AudioQuality.HEALTHY
    assert result.features.rms_energy > 0.0
    assert result.direction is not None


def test_observational_processing_budget_overage_marking(monkeypatch) -> None:
    """Simulate artificial delay exceeding 20ms; assert budget_exceeded and DEGRADED quality."""
    pipeline = AudioPipeline()
    store = AudioBufferStore()

    chunk, pcm = make_test_audio_chunk(channels=1, duration_ms=10)
    handle = store.allocate_slot(chunk, pcm)
    view = store.get_readonly_view(handle)

    # Monkeypatch spectral analyzer to sleep 25ms (> 20ms budget)
    orig_analyze = pipeline._spectral_analyzer.analyze
    def slow_analyze(*args, **kwargs):
        time.sleep(0.025)
        return orig_analyze(*args, **kwargs)

    monkeypatch.setattr(pipeline._spectral_analyzer, "analyze", slow_analyze)

    result, _ = pipeline.process(chunk, view)
    assert result.budget_exceeded is True
    assert result.quality == AudioQuality.DEGRADED


def test_stage_failure_isolation_and_redaction() -> None:
    """Verify that internal stage exception produces FAILED quality and redacted error message."""
    filter_ = SecretFilter(["super_secret_token_123"])
    pipeline = AudioPipeline(secret_filter=filter_)
    store = AudioBufferStore()

    chunk, pcm = make_test_audio_chunk(channels=1)
    handle = store.allocate_slot(chunk, pcm)
    view = store.get_readonly_view(handle)

    # Force an exception with a secret string
    def bad_analyze(*args, **kwargs):
        raise RuntimeError("Failed to compute FFT with token super_secret_token_123")

    pipeline._spectral_analyzer.analyze = bad_analyze

    result, _ = pipeline.process(chunk, view)
    assert result.quality == AudioQuality.FAILED
    assert result.error_code == "ERR_PROCESSING_FAILURE"
    assert "super_secret_token_123" not in result.error_message
    assert "<redacted>" in result.error_message
