# -*- coding: utf-8 -*-
"""Pytest fixtures and synthetic PCM generators for M02 Audio subsystem tests."""

import binascii
from datetime import datetime, timezone
import math
import struct
from typing import Optional, Sequence
import uuid

import pytest

from holomed.audio.models import (
    AudioChunk,
    ChannelLayout,
    MicrophonePosition,
    SampleFormat,
)
from holomed.configuration.models import AppConfig, EnvironmentProfile, LogLevel
from holomed.devices.interfaces import IDevice
from holomed.devices.manager import DeviceManager
from holomed.devices.models import DeviceCapability, DeviceHealth, DeviceState, DeviceType
from holomed.devices.registry import DeviceRegistry
from holomed.runtime.context import RuntimeContext
from holomed.runtime.models import HealthStatus


class DummyMicrophoneDevice(IDevice):
    """Synthetic IDevice implementation representing a microphone hardware array."""

    def __init__(
        self,
        device_id: str = "mic_01",
        device_type: DeviceType = DeviceType.AUDIO_MICROPHONE,
        physical_id: str = "usb://mic_array_1",
        state: DeviceState = DeviceState.UNREGISTERED,
    ) -> None:
        self._device_id = device_id
        self._device_type = device_type
        self._physical_id = physical_id
        self._state = state

    @property
    def device_id(self) -> str:
        return self._device_id

    @property
    def device_type(self) -> DeviceType:
        return self._device_type

    @property
    def physical_id(self) -> str:
        return self._physical_id

    @property
    def state(self) -> DeviceState:
        return self._state

    @property
    def capabilities(self) -> frozenset[DeviceCapability]:
        return frozenset({DeviceCapability.DATA_STREAM})

    def initialize(self) -> None:
        self._state = DeviceState.INITIALIZING
        self._state = DeviceState.READY

    def start(self) -> None:
        self._state = DeviceState.ACTIVE

    def stop(self) -> None:
        self._state = DeviceState.STOPPED

    def teardown(self) -> None:
        self._state = DeviceState.STOPPED

    def health(self) -> DeviceHealth:
        return DeviceHealth(
            device_id=self._device_id,
            status=HealthStatus.HEALTHY,
            message="Microphone array healthy",
            timestamp_utc=datetime.now(timezone.utc).isoformat(),
        )


@pytest.fixture
def test_runtime_context() -> RuntimeContext:
    """Provides standard testing RuntimeContext with epoch_id=1."""
    config = AppConfig(
        app_name="HoloMed AI",
        environment=EnvironmentProfile.TESTING,
        host="127.0.0.1",
        port=8080,
        log_level=LogLevel.DEBUG,
        gemini_api_key="test_key",
        protocol_version="1.0",
    )
    return RuntimeContext(app_config=config, epoch_id=1)


@pytest.fixture
def device_registry_with_microphone(test_runtime_context: RuntimeContext) -> tuple[DeviceRegistry, DeviceManager]:
    """Provides populated registry and manager with an active microphone array."""
    dm = DeviceManager()
    dm.initialize(test_runtime_context)
    dm.start()

    reg = getattr(dm, "registry", None) or getattr(dm, "_registry", None)
    token = getattr(dm, "registry_token", None) or getattr(dm, "_registry_token", None)

    mic = DummyMicrophoneDevice(device_id="mic_01", physical_id="usb://mic_array_1")
    reg.register(mic, token)
    mic._state = DeviceState.ACTIVE
    return reg, dm


def make_synthetic_pcm(
    sample_rate_hz: int = 16000,
    channels: int = 2,
    duration_ms: int = 20,
    sample_format: SampleFormat = SampleFormat.FLOAT32,
    channel_layout: ChannelLayout = ChannelLayout.INTERLEAVED,
    freq_hz: float = 440.0,
    phase_shift_deg: float = 0.0,
) -> tuple[int, bytes]:
    """Generate deterministic synthetic multi-channel PCM bytes and frame count."""
    frame_count = int(sample_rate_hz * (duration_ms / 1000.0))
    flat_samples: list[float] = []

    if channel_layout == ChannelLayout.INTERLEAVED:
        for f in range(frame_count):
            t = f / float(sample_rate_hz)
            for c in range(channels):
                phase_rad = math.radians(c * phase_shift_deg)
                val = 0.8 * math.sin(2.0 * math.pi * freq_hz * t + phase_rad)
                flat_samples.append(val)
    elif channel_layout == ChannelLayout.PLANAR:
        for c in range(channels):
            phase_rad = math.radians(c * phase_shift_deg)
            for f in range(frame_count):
                t = f / float(sample_rate_hz)
                val = 0.8 * math.sin(2.0 * math.pi * freq_hz * t + phase_rad)
                flat_samples.append(val)

    total_samples = len(flat_samples)
    if sample_format == SampleFormat.INT16:
        int_vals = [int(max(-32768, min(32767, round(s * 32767.0)))) for s in flat_samples]
        pcm_bytes = struct.pack(f"<{total_samples}h", *int_vals)
    elif sample_format == SampleFormat.INT32:
        int_vals = [int(max(-2147483648, min(2147483647, round(s * 2147483647.0)))) for s in flat_samples]
        pcm_bytes = struct.pack(f"<{total_samples}i", *int_vals)
    elif sample_format == SampleFormat.FLOAT32:
        pcm_bytes = struct.pack(f"<{total_samples}f", *flat_samples)
    else:
        raise ValueError(f"Unsupported format {sample_format}")

    return frame_count, pcm_bytes


def make_test_audio_chunk(
    device_id: str = "mic_01",
    physical_id: str = "usb://mic_array_1",
    sequence_number: int = 1,
    epoch_id: int = 1,
    sample_rate_hz: int = 16000,
    channels: int = 2,
    duration_ms: int = 20,
    sample_format: SampleFormat = SampleFormat.FLOAT32,
    channel_layout: ChannelLayout = ChannelLayout.INTERLEAVED,
    freq_hz: float = 440.0,
    phase_shift_deg: float = 0.0,
) -> tuple[AudioChunk, bytes]:
    """Create matched AudioChunk descriptor and synthetic PCM bytes."""
    frame_count, pcm_bytes = make_synthetic_pcm(
        sample_rate_hz=sample_rate_hz,
        channels=channels,
        duration_ms=duration_ms,
        sample_format=sample_format,
        channel_layout=channel_layout,
        freq_hz=freq_hz,
        phase_shift_deg=phase_shift_deg,
    )
    payload_bytes = len(pcm_bytes)
    crc = binascii.crc32(pcm_bytes)

    chunk = AudioChunk(
        chunk_id=f"chk_{uuid.uuid4().hex[:12]}",
        device_id=device_id,
        physical_id=physical_id,
        sequence_number=sequence_number,
        timestamp_utc=datetime.now(timezone.utc).isoformat(),
        epoch_id=epoch_id,
        sample_rate_hz=sample_rate_hz,
        channels=channels,
        sample_format=sample_format,
        channel_layout=channel_layout,
        frame_count=frame_count,
        payload_bytes=payload_bytes,
        checksum_crc32=crc,
        buffer_handle_id="",
    )
    return chunk, pcm_bytes
