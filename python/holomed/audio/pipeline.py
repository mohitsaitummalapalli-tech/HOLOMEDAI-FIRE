# -*- coding: utf-8 -*-
"""M02 Audio Subsystem Synchronous 8-Stage AudioPipeline.

Executes deterministic 8-stage processing:
1. Input Validation
2. PCM Canonicalization
3. Windowing & Zero-Padding
4. Spectral Analysis
5. Acoustic Feature Extraction
6. Spatial Estimation
7. Temporal Tracking
8. Quality Evaluation

With observational budget monitoring against MAX_AUDIO_PROCESSING_BUDGET_MS = 20.0ms.
"""

import time
from typing import Optional, Sequence

from holomed.audio.exceptions import (
    AudioLifecycleError,
    AudioPipelineError,
)
from holomed.audio.models import (
    AcousticDirection,
    AcousticFeatures,
    AudioChunk,
    AudioProcessingResult,
    AudioQuality,
    AudioTrack,
    MAX_AUDIO_PROCESSING_BUDGET_MS,
    MicrophonePosition,
)
from holomed.audio.spatial import SpatialAudioEstimator
from holomed.audio.spectral import AudioSpectralAnalyzer, unpack_pcm_samples
from holomed.audio.tracker import TemporalAcousticTracker
from holomed.runtime.logging import SecretFilter


class AudioPipeline:
    """Synchronous, deterministic 8-stage audio pipeline."""

    def __init__(
        self,
        microphones: Optional[Sequence[MicrophonePosition]] = None,
        fft_size: int = 1024,
        secret_filter: Optional[SecretFilter] = None,
    ) -> None:
        self._spectral_analyzer: AudioSpectralAnalyzer = AudioSpectralAnalyzer(fft_size=fft_size)
        self._spatial_estimator: Optional[SpatialAudioEstimator] = (
            SpatialAudioEstimator(microphones) if microphones and len(microphones) >= 2 else None
        )
        self._tracker: TemporalAcousticTracker = TemporalAcousticTracker()
        self._secret_filter: Optional[SecretFilter] = secret_filter
        self._in_process: bool = False

    @property
    def tracker(self) -> TemporalAcousticTracker:
        return self._tracker

    def configure_microphones(self, microphones: Sequence[MicrophonePosition]) -> None:
        """Update or initialize microphone array geometry for spatial estimation."""
        if len(microphones) >= 2:
            self._spatial_estimator = SpatialAudioEstimator(microphones)
        else:
            self._spatial_estimator = None

    def process(
        self,
        chunk: AudioChunk,
        pcm_view: memoryview,
    ) -> tuple[AudioProcessingResult, tuple[AudioTrack, ...]]:
        """Execute the 8-stage audio processing pipeline."""
        if self._in_process:
            raise AudioLifecycleError("Reentrant pipeline invocation detected in process()")

        self._in_process = True
        t_start = time.perf_counter()

        direction: Optional[AcousticDirection] = None
        features = AcousticFeatures(
            rms_energy=0.0,
            zero_crossing_rate=0.0,
            spectral_centroid_hz=0.0,
            spectral_bandwidth_hz=0.0,
            spectral_rolloff_hz=0.0,
            peak_frequency_hz=0.0,
        )
        tracks: tuple[AudioTrack, ...] = ()
        quality = AudioQuality.HEALTHY
        error_code: Optional[str] = None
        error_msg: Optional[str] = None

        try:
            # Stage 1 & 2: Input Validation & PCM Canonicalization
            channel_samples = unpack_pcm_samples(
                pcm_view,
                chunk.sample_format,
                chunk.channels,
                chunk.channel_layout,
                chunk.frame_count,
            )

            # Stage 3, 4, 5: Windowing, Spectral Analysis, Feature Extraction (Channel 0 Reference)
            ref_samples = channel_samples[0]
            features, _ = self._spectral_analyzer.analyze(ref_samples, chunk.sample_rate_hz)

            # Stage 6: Spatial Estimation (if multi-channel array configured)
            if self._spatial_estimator is not None and chunk.channels >= 2:
                direction = self._spatial_estimator.estimate_direction(
                    channel_samples, chunk.sample_rate_hz
                )

            # Stage 7: Temporal Tracking
            tracks = self._tracker.update(direction, chunk.timestamp_utc)

            # Stage 8: Quality Evaluation
            if features.rms_energy < 1e-4:
                quality = AudioQuality.DEGRADED
            else:
                quality = AudioQuality.HEALTHY

        except Exception as e:
            raw_msg = f"{type(e).__name__}: {e}"
            redacted_msg = self._secret_filter.redact(raw_msg) if self._secret_filter else raw_msg
            quality = AudioQuality.FAILED
            error_code = "ERR_PROCESSING_FAILURE"
            error_msg = redacted_msg

        finally:
            self._in_process = False

        # Measure elapsed time and evaluate observational budget
        elapsed_ms = (time.perf_counter() - t_start) * 1000.0
        budget_exceeded = elapsed_ms > MAX_AUDIO_PROCESSING_BUDGET_MS
        if budget_exceeded and quality == AudioQuality.HEALTHY:
            quality = AudioQuality.DEGRADED

        result = AudioProcessingResult(
            chunk_id=chunk.chunk_id,
            device_id=chunk.device_id,
            sequence_number=chunk.sequence_number,
            timestamp_utc=chunk.timestamp_utc,
            epoch_id=chunk.epoch_id,
            features=features,
            direction=direction,
            processing_latency_ms=round(elapsed_ms, 3),
            budget_exceeded=budget_exceeded,
            quality=quality,
            error_code=error_code,
            error_message=error_msg,
        )
        return result, tracks
