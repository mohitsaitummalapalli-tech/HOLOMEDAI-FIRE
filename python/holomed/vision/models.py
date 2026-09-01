"""Data models, mathematical representations, and limits for M01 Spatial Vision."""

from __future__ import annotations

from dataclasses import dataclass
import enum
import json
import math
from types import MappingProxyType
from typing import Any, Mapping, Optional, Sequence
import unicodedata
import uuid

from holomed.devices.models import deep_freeze_parameter
from holomed.vision.exceptions import (
    VisionCapacityError,
    VisionFrameValidationError,
    VisionValidationError,
)

# Hard Architectural Bounds
MAX_FRAME_WIDTH: int = 1920
MAX_FRAME_HEIGHT: int = 1080
MAX_FRAME_BYTES: int = 3145728  # 3.0 MiB
MAX_BUFFERED_FRAMES: int = 8
MAX_TRACKED_LANDMARKS: int = 128
MIN_DEPTH_METERS: float = 0.05
MAX_DEPTH_METERS: float = 10.0
MAX_TRACKED_ENTITIES: int = 32
MAX_TRACK_HISTORY_LENGTH: int = 64
MAX_PROCESSING_BUDGET_MS: float = 25.0
NORMALIZED_QUANTIZATION: float = 1e-6
MAX_VISION_PAYLOAD_BYTES: int = 65536  # 64 KiB
MAX_DEVICE_ID_LENGTH: int = 128
MAX_COASTING_FRAMES: int = 5
CONFIRMATION_DETECTION_THRESHOLD: int = 3


class PixelFormat(str, enum.Enum):
    """Supported deterministic pixel formats with known byte layouts."""

    GRAY8 = "GRAY8"
    RGB8 = "RGB8"
    RGBA8 = "RGBA8"
    DEPTH16 = "DEPTH16"
    FLOAT32_DEPTH = "FLOAT32_DEPTH"

    @property
    def bytes_per_pixel(self) -> int:
        match self:
            case PixelFormat.GRAY8:
                return 1
            case PixelFormat.DEPTH16:
                return 2
            case PixelFormat.RGB8:
                return 3
            case PixelFormat.RGBA8 | PixelFormat.FLOAT32_DEPTH:
                return 4


class SlotState(str, enum.Enum):
    """Lifecycle states of an in-memory frame buffer slot in VisionFrameStore."""

    FREE = "FREE"
    HELD = "HELD"
    PROCESSING = "PROCESSING"


class TrackState(str, enum.Enum):
    """State of an entity spatial track across temporal frame sequences."""

    TENTATIVE = "TENTATIVE"
    CONFIRMED = "CONFIRMED"
    COASTING = "COASTING"
    LOST = "LOST"
    RETIRED = "RETIRED"


class VisionQuality(str, enum.Enum):
    """Quality assessment of frame processing and spatial estimation."""

    OPTIMAL = "OPTIMAL"
    DEGRADED = "DEGRADED"
    OCCLUDED = "OCCLUDED"
    FAILED = "FAILED"


def quantize_normalized_coord(val: float) -> float:
    """Quantize normalized coordinate to 1e-6 precision and normalize -0.0 to 0.0."""
    if math.isnan(val) or math.isinf(val):
        raise VisionValidationError(f"Coordinate must be finite, got {val!r}")
    rounded = round(val, 6)
    return 0.0 if rounded == 0.0 else rounded


@dataclass(frozen=True)
class FrameDescriptor:
    """Lightweight immutable metadata descriptor referencing an in-memory frame buffer."""

    frame_id: str
    device_id: str
    physical_id: str
    sequence_number: int
    timestamp_utc: str
    width: int
    height: int
    pixel_format: PixelFormat
    stride_bytes: int
    total_bytes: int
    checksum_crc32: int
    epoch_id: int
    buffer_handle_id: str

    def __post_init__(self) -> None:
        # Validate UUIDv4 frame_id
        if not isinstance(self.frame_id, str):
            raise VisionValidationError(f"frame_id must be str, got {type(self.frame_id).__name__}")
        try:
            parsed = uuid.UUID(self.frame_id, version=4)
            if str(parsed) != self.frame_id.lower():
                raise ValueError()
        except Exception:
            raise VisionValidationError(f"frame_id must be canonical lowercase UUIDv4, got {self.frame_id!r}")

        if not isinstance(self.device_id, str) or not (1 <= len(self.device_id) <= MAX_DEVICE_ID_LENGTH):
            raise VisionValidationError(f"Invalid device_id syntax or length: {self.device_id!r}")
        if not isinstance(self.physical_id, str) or not self.physical_id:
            raise VisionValidationError(f"physical_id cannot be empty, got {self.physical_id!r}")
        if type(self.sequence_number) is not int or self.sequence_number < 0:
            raise VisionValidationError(f"sequence_number must be non-negative int, got {self.sequence_number!r}")
        if not isinstance(self.timestamp_utc, str) or not self.timestamp_utc:
            raise VisionValidationError("timestamp_utc cannot be empty")
        if not isinstance(self.pixel_format, PixelFormat):
            raise VisionValidationError(f"Invalid pixel_format: {self.pixel_format!r}")

        # Dimensions check
        if type(self.width) is not int or not (1 <= self.width <= MAX_FRAME_WIDTH):
            raise VisionFrameValidationError(
                f"width must be in range [1, {MAX_FRAME_WIDTH}], got {self.width!r}"
            )
        if type(self.height) is not int or not (1 <= self.height <= MAX_FRAME_HEIGHT):
            raise VisionFrameValidationError(
                f"height must be in range [1, {MAX_FRAME_HEIGHT}], got {self.height!r}"
            )

        expected_min_stride = self.width * self.pixel_format.bytes_per_pixel
        if type(self.stride_bytes) is not int or self.stride_bytes < expected_min_stride:
            raise VisionFrameValidationError(
                f"stride_bytes must be >= {expected_min_stride}, got {self.stride_bytes!r}"
            )

        expected_total = self.stride_bytes * self.height
        if type(self.total_bytes) is not int or self.total_bytes != expected_total:
            raise VisionFrameValidationError(
                f"total_bytes must equal stride * height ({expected_total}), got {self.total_bytes!r}"
            )
        if self.total_bytes > MAX_FRAME_BYTES:
            raise VisionFrameValidationError(
                f"total_bytes ({self.total_bytes}) exceeds MAX_FRAME_BYTES ({MAX_FRAME_BYTES})"
            )

        if type(self.checksum_crc32) is not int or not (0 <= self.checksum_crc32 <= 0xFFFFFFFF):
            raise VisionValidationError(f"checksum_crc32 must be uint32, got {self.checksum_crc32!r}")
        if type(self.epoch_id) is not int or self.epoch_id <= 0:
            raise VisionValidationError(f"epoch_id must be positive int, got {self.epoch_id!r}")
        if not isinstance(self.buffer_handle_id, str) or not self.buffer_handle_id:
            raise VisionValidationError("buffer_handle_id cannot be empty")


@dataclass(frozen=True)
class SpatialLandmark:
    """Normalized spatial landmark point with depth and confidence."""

    landmark_id: int
    name: str
    u: float
    v: float
    depth_m: Optional[float]
    confidence: float
    is_occluded: bool = False

    def __post_init__(self) -> None:
        if type(self.landmark_id) is not int or not (0 <= self.landmark_id < MAX_TRACKED_LANDMARKS):
            raise VisionValidationError(
                f"landmark_id must be int in range [0, {MAX_TRACKED_LANDMARKS}), got {self.landmark_id!r}"
            )
        if not isinstance(self.name, str) or not self.name:
            raise VisionValidationError("Landmark name must be non-empty string")

        for val, name in ((self.u, "u"), (self.v, "v"), (self.confidence, "confidence")):
            if not isinstance(val, (int, float)) or math.isnan(val) or math.isinf(val):
                raise VisionValidationError(f"Landmark {name} must be a finite float, got {val!r}")

        if not (0.0 <= self.u <= 1.0):
            raise VisionValidationError(f"u must be in [0.0, 1.0], got {self.u!r}")
        if not (0.0 <= self.v <= 1.0):
            raise VisionValidationError(f"v must be in [0.0, 1.0], got {self.v!r}")
        if not (0.0 <= self.confidence <= 1.0):
            raise VisionValidationError(f"confidence must be in [0.0, 1.0], got {self.confidence!r}")

        # Quantize normalized coordinates
        object.__setattr__(self, "u", quantize_normalized_coord(self.u))
        object.__setattr__(self, "v", quantize_normalized_coord(self.v))
        object.__setattr__(self, "confidence", quantize_normalized_coord(self.confidence))

        if self.depth_m is not None:
            if not isinstance(self.depth_m, (int, float)) or math.isnan(self.depth_m) or math.isinf(self.depth_m):
                raise VisionValidationError(f"depth_m must be finite float, got {self.depth_m!r}")
            if not (MIN_DEPTH_METERS <= self.depth_m <= MAX_DEPTH_METERS):
                raise VisionValidationError(
                    f"depth_m must be in range [{MIN_DEPTH_METERS}, {MAX_DEPTH_METERS}], got {self.depth_m!r}"
                )
            object.__setattr__(self, "depth_m", round(float(self.depth_m), 4))


@dataclass(frozen=True)
class SpatialPose:
    """Rigid 6-DOF transformation in Right-Handed Metric Space.
    
    Transforms vector from source_frame to target_frame:
        p_target = R * p_source + t
    """

    tx: float
    ty: float
    tz: float
    qw: float
    qx: float
    qy: float
    qz: float
    source_frame: str
    target_frame: str
    timestamp_utc: str

    def __post_init__(self) -> None:
        # 1. Finite-value verification
        for val, name in (
            (self.tx, "tx"), (self.ty, "ty"), (self.tz, "tz"),
            (self.qw, "qw"), (self.qx, "qx"), (self.qy, "qy"), (self.qz, "qz"),
        ):
            if not isinstance(val, (int, float)) or math.isnan(val) or math.isinf(val):
                raise VisionValidationError(f"SpatialPose element '{name}' must be finite, got {val!r}")

        # 2. Metric translation boundary check (-100m to +100m)
        for val, name in ((self.tx, "tx"), (self.ty, "ty"), (self.tz, "tz")):
            if abs(val) > 100.0:
                raise VisionValidationError(f"Translation '{name}' ({val}m) exceeds 100m clinical bound")

        # 3. Quaternion Unit Norm Invariant (||q|| = 1.0 +/- 1e-4)
        norm_sq = self.qw**2 + self.qx**2 + self.qy**2 + self.qz**2
        if abs(norm_sq - 1.0) > 1e-4:
            raise VisionValidationError(f"Quaternion is not unit-normalized (norm^2 = {norm_sq})")

        # 4. Canonical Hemisphere Convention (qw >= 0.0)
        qw, qx, qy, qz = float(self.qw), float(self.qx), float(self.qy), float(self.qz)
        if qw < 0.0:
            qw, qx, qy, qz = -qw, -qx, -qy, -qz

        object.__setattr__(self, "tx", round(float(self.tx), 6))
        object.__setattr__(self, "ty", round(float(self.ty), 6))
        object.__setattr__(self, "tz", round(float(self.tz), 6))
        object.__setattr__(self, "qw", round(qw, 6))
        object.__setattr__(self, "qx", round(qx, 6))
        object.__setattr__(self, "qy", round(qy, 6))
        object.__setattr__(self, "qz", round(qz, 6))

        if not isinstance(self.source_frame, str) or not self.source_frame:
            raise VisionValidationError("source_frame cannot be empty")
        if not isinstance(self.target_frame, str) or not self.target_frame:
            raise VisionValidationError("target_frame cannot be empty")
        if not isinstance(self.timestamp_utc, str) or not self.timestamp_utc:
            raise VisionValidationError("timestamp_utc cannot be empty")

    def to_matrix_4x4(self) -> tuple[float, ...]:
        """Produce canonical 16-element tuple in Row-Major format for p_target = M * p_source."""
        w, x, y, z = self.qw, self.qx, self.qy, self.qz
        tx, ty, tz = self.tx, self.ty, self.tz

        r00 = 1.0 - 2.0 * (y**2 + z**2)
        r01 = 2.0 * (x * y - z * w)
        r02 = 2.0 * (x * z + y * w)

        r10 = 2.0 * (x * y + z * w)
        r11 = 1.0 - 2.0 * (x**2 + z**2)
        r12 = 2.0 * (y * z - x * w)

        r20 = 2.0 * (x * z - y * w)
        r21 = 2.0 * (y * z + x * w)
        r22 = 1.0 - 2.0 * (x**2 + y**2)

        return (
            round(r00, 6), round(r01, 6), round(r02, 6), tx,
            round(r10, 6), round(r11, 6), round(r12, 6), ty,
            round(r20, 6), round(r21, 6), round(r22, 6), tz,
            0.0, 0.0, 0.0, 1.0,
        )


@dataclass(frozen=True)
class TrackedEntity:
    """Temporal spatial track representing an entity observed across frames."""

    entity_id: str
    state: TrackState
    pose: Optional[SpatialPose]
    landmarks: tuple[SpatialLandmark, ...]
    history: tuple[SpatialPose, ...]
    consecutive_detections: int
    coasting_frames: int

    def __post_init__(self) -> None:
        if not isinstance(self.entity_id, str) or not self.entity_id:
            raise VisionValidationError("entity_id cannot be empty")
        if not isinstance(self.state, TrackState):
            raise VisionValidationError(f"Invalid TrackState: {self.state!r}")
        if len(self.landmarks) > MAX_TRACKED_LANDMARKS:
            raise VisionCapacityError(
                f"landmarks ({len(self.landmarks)}) exceeds MAX_TRACKED_LANDMARKS ({MAX_TRACKED_LANDMARKS})"
            )
        if len(self.history) > MAX_TRACK_HISTORY_LENGTH:
            raise VisionCapacityError(
                f"history ({len(self.history)}) exceeds MAX_TRACK_HISTORY_LENGTH ({MAX_TRACK_HISTORY_LENGTH})"
            )


@dataclass(frozen=True)
class VisionProcessingResult:
    """Immutable outcome produced by running VisionPipeline on a frame."""

    frame_id: str
    device_id: str
    sequence_number: int
    timestamp_utc: str
    quality: VisionQuality
    landmarks: tuple[SpatialLandmark, ...]
    tracks: tuple[TrackedEntity, ...]
    execution_time_ms: float
    budget_exceeded: bool
    error_message: Optional[str] = None

    def __post_init__(self) -> None:
        if len(self.landmarks) > MAX_TRACKED_LANDMARKS:
            raise VisionCapacityError(
                f"landmarks ({len(self.landmarks)}) exceeds MAX_TRACKED_LANDMARKS ({MAX_TRACKED_LANDMARKS})"
            )
        if len(self.tracks) > MAX_TRACKED_ENTITIES:
            raise VisionCapacityError(
                f"tracks ({len(self.tracks)}) exceeds MAX_TRACKED_ENTITIES ({MAX_TRACKED_ENTITIES})"
            )


def convert_for_json_serialization(obj: Any) -> Any:
    """Recursively convert nested MappingProxyType, sets, tuples, and floats for JSON."""
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
            raise VisionValidationError(f"Non-finite float {obj} cannot be serialized to JSON")
        if obj == 0.0:
            return 0.0
        return obj
    return obj


def serialize_vision_payload(payload_dict: Mapping[str, Any]) -> dict[str, Any]:
    """Canonicalize, freeze, and validate wire size of vision payload (< 64 KiB)."""
    clean_dict = convert_for_json_serialization(payload_dict)
    serialized = json.dumps(clean_dict, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    if len(serialized.encode("utf-8")) > MAX_VISION_PAYLOAD_BYTES:
        raise VisionCapacityError(
            f"Vision payload ({len(serialized.encode('utf-8'))} bytes) exceeds MAX_VISION_PAYLOAD_BYTES ({MAX_VISION_PAYLOAD_BYTES})"
        )
    return clean_dict
