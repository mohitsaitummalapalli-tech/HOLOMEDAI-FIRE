# -*- coding: utf-8 -*-
"""M02 Audio Subsystem Canonical Data Models & Limits."""

from dataclasses import dataclass
import enum
import json
import math
from types import MappingProxyType
from typing import Any, Mapping, Optional, Sequence
import unicodedata

from holomed.audio.exceptions import AudioCapacityError, AudioValidationError

# Numerical Constants (§52)
MAX_AUDIO_CHANNELS: int = 8
MAX_BUFFERED_AUDIO_CHUNKS: int = 16
MAX_AUDIO_CHUNK_BYTES: int = 262144  # 256 KiB
MAX_AUDIO_TRACKS: int = 16
MAX_AUDIO_TRACK_HISTORY: int = 32
MAX_AUDIO_PROCESSING_BUDGET_MS: float = 20.0
MAX_AUDIO_PAYLOAD_BYTES: int = 65536
MAX_AUDIO_DESCRIPTOR_BYTES: int = 1024
MAX_AUDIO_EVENTS: int = 1000
MAX_SAFE_INTEGER: int = 9223372036854775807

MIN_CHUNK_DURATION_MS: int = 5
MAX_CHUNK_DURATION_MS: int = 100
SUPPORTED_SAMPLE_RATES: frozenset[int] = frozenset({8000, 16000, 22050, 44100, 48000})
SUPPORTED_FFT_SIZES: frozenset[int] = frozenset({256, 512, 1024, 2048, 4096})
DEFAULT_FFT_SIZE: int = 1024
OVERLAP_RATIO: float = 0.5
SPEED_OF_SOUND_MPS: float = 343.0
MIN_MICROPHONE_BASELINE_M: float = 0.005  # 5 mm (D195)
MAX_AUDIO_COASTING_FRAMES: int = 5
TRACK_CONFIRMATION_THRESHOLD: int = 3


class SampleFormat(str, enum.Enum):
    """Supported PCM sample encodings."""
    INT16 = "INT16"
    INT32 = "INT32"
    FLOAT32 = "FLOAT32"

    @property
    def bytes_per_sample(self) -> int:
        if self == SampleFormat.INT16:
            return 2
        elif self == SampleFormat.INT32:
            return 4
        elif self == SampleFormat.FLOAT32:
            return 4
        raise ValueError(f"Unknown sample format: {self}")


class ChannelLayout(str, enum.Enum):
    """PCM channel arrangement."""
    INTERLEAVED = "INTERLEAVED"
    PLANAR = "PLANAR"


class AudioChannelRole(str, enum.Enum):
    """Acoustic channel role designation."""
    MONO = "MONO"
    FRONT_LEFT = "FRONT_LEFT"
    FRONT_RIGHT = "FRONT_RIGHT"
    CENTER = "CENTER"
    LFE = "LFE"
    REAR_LEFT = "REAR_LEFT"
    REAR_RIGHT = "REAR_RIGHT"
    UNKNOWN = "UNKNOWN"


class SlotState(str, enum.Enum):
    """State machine for AudioBufferStore slots."""
    FREE = "FREE"
    HELD = "HELD"
    PROCESSING = "PROCESSING"


class TrackState(str, enum.Enum):
    """State machine for temporal acoustic tracks."""
    TENTATIVE = "TENTATIVE"
    CONFIRMED = "CONFIRMED"
    COASTING = "COASTING"
    LOST = "LOST"
    RETIRED = "RETIRED"


class AudioQuality(str, enum.Enum):
    """Aggregate health and quality evaluation of audio processing."""
    HEALTHY = "HEALTHY"
    DEGRADED = "DEGRADED"
    UNHEALTHY = "UNHEALTHY"
    FAILED = "FAILED"


@dataclass(frozen=True)
class AudioChunk:
    """Immutable wire descriptor for an ingested audio chunk."""
    chunk_id: str
    device_id: str
    physical_id: str
    sequence_number: int
    timestamp_utc: str
    epoch_id: int
    sample_rate_hz: int
    channels: int
    sample_format: SampleFormat
    channel_layout: ChannelLayout
    frame_count: int
    payload_bytes: int
    checksum_crc32: int
    buffer_handle_id: str

    def __post_init__(self) -> None:
        if not self.chunk_id or not isinstance(self.chunk_id, str):
            raise AudioValidationError("chunk_id must be a non-empty string")
        if not self.device_id or not isinstance(self.device_id, str):
            raise AudioValidationError("device_id must be a non-empty string")
        if not self.physical_id or not isinstance(self.physical_id, str):
            raise AudioValidationError("physical_id must be a non-empty string")
        if not (0 <= self.sequence_number <= MAX_SAFE_INTEGER):
            raise AudioValidationError(f"sequence_number {self.sequence_number} out of valid bounds [0, {MAX_SAFE_INTEGER}]")
        if self.sample_rate_hz not in SUPPORTED_SAMPLE_RATES:
            raise AudioValidationError(f"sample_rate_hz {self.sample_rate_hz} not in supported rates: {sorted(SUPPORTED_SAMPLE_RATES)}")
        if not (1 <= self.channels <= MAX_AUDIO_CHANNELS):
            raise AudioValidationError(f"channels {self.channels} out of range [1, {MAX_AUDIO_CHANNELS}]")
        if not isinstance(self.sample_format, SampleFormat):
            raise AudioValidationError(f"Invalid sample_format: {self.sample_format}")
        if not isinstance(self.channel_layout, ChannelLayout):
            raise AudioValidationError(f"Invalid channel_layout: {self.channel_layout}")
        if self.payload_bytes > MAX_AUDIO_CHUNK_BYTES:
            raise AudioCapacityError(f"payload_bytes {self.payload_bytes} exceeds MAX_AUDIO_CHUNK_BYTES ({MAX_AUDIO_CHUNK_BYTES})")
        expected_bytes = self.frame_count * self.channels * self.sample_format.bytes_per_sample
        if self.payload_bytes != expected_bytes:
            raise AudioValidationError(f"payload_bytes {self.payload_bytes} does not match expected {expected_bytes}")
        min_frames = math.ceil(self.sample_rate_hz * (MIN_CHUNK_DURATION_MS / 1000.0))
        max_frames = math.floor(self.sample_rate_hz * (MAX_CHUNK_DURATION_MS / 1000.0))
        if not (min_frames <= self.frame_count <= max_frames):
            raise AudioValidationError(
                f"frame_count {self.frame_count} out of duration bounds [{min_frames}, {max_frames}] "
                f"for {self.sample_rate_hz} Hz ({MIN_CHUNK_DURATION_MS}ms - {MAX_CHUNK_DURATION_MS}ms)"
            )


@dataclass(frozen=True)
class MicrophonePosition:
    """Spatial 3D coordinate of an individual microphone element."""
    microphone_id: int
    x_m: float
    y_m: float
    z_m: float

    def __post_init__(self) -> None:
        if not (0 <= self.microphone_id < MAX_AUDIO_CHANNELS):
            raise AudioValidationError(f"microphone_id {self.microphone_id} out of bounds [0, {MAX_AUDIO_CHANNELS - 1}]")
        for axis, val in (("x_m", self.x_m), ("y_m", self.y_m), ("z_m", self.z_m)):
            if not isinstance(val, (int, float)) or math.isnan(val) or math.isinf(val):
                raise AudioValidationError(f"{axis} coordinate must be a finite float, got {val}")
            if not (-2.0 <= val <= 2.0):
                raise AudioValidationError(f"{axis} coordinate {val} exceeds physical bound [-2.0m, +2.0m]")


@dataclass(frozen=True)
class AcousticFeatures:
    """Extracted acoustic and spectral features."""
    rms_energy: float
    zero_crossing_rate: float
    spectral_centroid_hz: float
    spectral_bandwidth_hz: float
    spectral_rolloff_hz: float
    peak_frequency_hz: float

    def __post_init__(self) -> None:
        for field, val in (
            ("rms_energy", self.rms_energy),
            ("zero_crossing_rate", self.zero_crossing_rate),
            ("spectral_centroid_hz", self.spectral_centroid_hz),
            ("spectral_bandwidth_hz", self.spectral_bandwidth_hz),
            ("spectral_rolloff_hz", self.spectral_rolloff_hz),
            ("peak_frequency_hz", self.peak_frequency_hz),
        ):
            if not isinstance(val, (int, float)) or math.isnan(val) or math.isinf(val):
                raise AudioValidationError(f"{field} must be a finite float, got {val}")
        if not (0.0 <= self.rms_energy <= 1.0):
            raise AudioValidationError(f"rms_energy {self.rms_energy} out of range [0.0, 1.0]")
        if not (0.0 <= self.zero_crossing_rate <= 1.0):
            raise AudioValidationError(f"zero_crossing_rate {self.zero_crossing_rate} out of range [0.0, 1.0]")


@dataclass(frozen=True)
class AcousticDirection:
    """Estimated 3D direction of arrival (DOA) angles and confidence."""
    azimuth_deg: float
    elevation_deg: float
    confidence: float

    def __post_init__(self) -> None:
        for field, val in (
            ("azimuth_deg", self.azimuth_deg),
            ("elevation_deg", self.elevation_deg),
            ("confidence", self.confidence),
        ):
            if not isinstance(val, (int, float)) or math.isnan(val) or math.isinf(val):
                raise AudioValidationError(f"{field} must be a finite float, got {val}")
        if not (-180.0 <= self.azimuth_deg <= 180.0):
            raise AudioValidationError(f"azimuth_deg {self.azimuth_deg} out of range [-180.0, 180.0]")
        if not (-90.0 <= self.elevation_deg <= 90.0):
            raise AudioValidationError(f"elevation_deg {self.elevation_deg} out of range [-90.0, 90.0]")
        if not (0.0 <= self.confidence <= 1.0):
            raise AudioValidationError(f"confidence {self.confidence} out of range [0.0, 1.0]")

        # Canonical normalization: map -180.0 to +180.0 to eliminate dual representation (D196)
        norm_azimuth = 180.0 if abs(self.azimuth_deg - (-180.0)) < 1e-9 else round(float(self.azimuth_deg), 6)
        if norm_azimuth == -0.0:
            norm_azimuth = 0.0
        object.__setattr__(self, "azimuth_deg", norm_azimuth)

        norm_elev = round(float(self.elevation_deg), 6)
        if norm_elev == -0.0:
            norm_elev = 0.0
        object.__setattr__(self, "elevation_deg", norm_elev)
        object.__setattr__(self, "confidence", round(float(self.confidence), 4))


@dataclass(frozen=True)
class AudioTrack:
    """Temporal acoustic track for a localized sound source."""
    track_id: int
    state: TrackState
    direction: AcousticDirection
    observations_count: int
    coasting_frames: int
    history: tuple[tuple[float, float, str], ...] = ()


@dataclass(frozen=True)
class AudioProcessingResult:
    """Comprehensive result of single audio chunk processing."""
    chunk_id: str
    device_id: str
    sequence_number: int
    timestamp_utc: str
    epoch_id: int
    features: AcousticFeatures
    direction: Optional[AcousticDirection]
    processing_latency_ms: float
    budget_exceeded: bool
    quality: AudioQuality
    error_code: Optional[str]
    error_message: Optional[str]


def convert_for_json_serialization(obj: Any) -> Any:
    """Recursively convert nested types into standard JSON types for protocol validation."""
    if isinstance(obj, MappingProxyType):
        return {str(k): convert_for_json_serialization(v) for k, v in obj.items()}
    if isinstance(obj, dict):
        return {str(k): convert_for_json_serialization(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [convert_for_json_serialization(item) for item in obj]
    if isinstance(obj, (set, frozenset)):
        return sorted([convert_for_json_serialization(item) for item in obj], key=str)
    if isinstance(obj, str):
        return unicodedata.normalize("NFC", obj)
    if isinstance(obj, float):
        if math.isnan(obj) or math.isinf(obj):
            raise AudioValidationError(f"Non-finite float {obj} cannot be serialized to JSON")
        if obj == 0.0:
            return 0.0
        return obj
    return obj


def serialize_audio_payload(payload_dict: Mapping[str, Any]) -> dict[str, Any]:
    """Canonicalize, freeze, and validate wire size of audio payload (<= 64 KiB)."""
    clean_dict = convert_for_json_serialization(payload_dict)
    serialized = json.dumps(clean_dict, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    if len(serialized.encode("utf-8")) > MAX_AUDIO_PAYLOAD_BYTES:
        raise AudioCapacityError(
            f"Audio payload ({len(serialized.encode('utf-8'))} bytes) exceeds MAX_AUDIO_PAYLOAD_BYTES ({MAX_AUDIO_PAYLOAD_BYTES})"
        )
    return clean_dict
