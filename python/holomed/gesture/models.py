# -*- coding: utf-8 -*-
"""Data models, mathematical representations, and limits for M03 Gesture & Spatial Interaction."""

from __future__ import annotations

from dataclasses import dataclass
import enum
import json
import math
from types import MappingProxyType
from typing import Any, Mapping, Optional, Sequence
import unicodedata
import uuid

from holomed.gesture.exceptions import (
    GestureCapacityError,
    GestureValidationError,
)
from holomed.vision.models import SpatialLandmark

# Hard Architectural Limits
MAX_HAND_LANDMARKS: int = 21
MAX_HAND_COORDINATE_METERS: float = 10.0
PINCH_DISTANCE_METERS: float = 0.035
HAND_CONFIRMATION_FRAMES: int = 3
MAX_HAND_COASTING_FRAMES: int = 5
MAX_TRACKED_HANDS: int = 8
MAX_HAND_HISTORY: int = 32
GESTURE_CONFIRMATION_FRAMES: int = 2
GESTURE_RELEASE_FRAMES: int = 2
MAX_GESTURE_HISTORY: int = 256
MAX_GESTURE_EVENT_KEYS: int = 2048
MAX_RECORDED_GESTURE_EVENTS: int = 1000
MAX_GESTURE_PROCESSING_BUDGET_MS: float = 10.0
MAX_GESTURE_PAYLOAD_BYTES: int = 65536  # 64 KiB
HAND_ASSOCIATION_THRESHOLD_METERS: float = 0.35
MAX_DEVICE_ID_LENGTH: int = 128
NORMALIZED_QUANTIZATION: float = 1e-6
COORDINATE_EPSILON: float = 1e-6
MIN_METRIC_DEPTH_METERS: float = 0.05

CANONICAL_LANDMARK_NAMES: tuple[str, ...] = (
    "WRIST",        # 0
    "THUMB_CMC",     # 1
    "THUMB_MCP",     # 2
    "THUMB_IP",      # 3
    "THUMB_TIP",     # 4
    "INDEX_MCP",     # 5
    "INDEX_PIP",     # 6
    "INDEX_DIP",     # 7
    "INDEX_TIP",     # 8
    "MIDDLE_MCP",    # 9
    "MIDDLE_PIP",    # 10
    "MIDDLE_DIP",    # 11
    "MIDDLE_TIP",    # 12
    "RING_MCP",      # 13
    "RING_PIP",      # 14
    "RING_DIP",      # 15
    "RING_TIP",      # 16
    "PINKY_MCP",     # 17
    "PINKY_PIP",     # 18
    "PINKY_DIP",     # 19
    "PINKY_TIP",     # 20
)


def quantize_coord(val: float, precision: int = 6) -> float:
    """Quantize coordinate value to specified precision and normalize -0.0 to +0.0."""
    if not isinstance(val, (int, float)) or math.isnan(val) or math.isinf(val):
        raise GestureValidationError(f"Coordinate must be finite, got {val!r}")
    rounded = round(float(val), precision)
    return 0.0 if rounded == 0.0 else rounded


class Handedness(str, enum.Enum):
    """Categorical classification of hand handedness."""

    LEFT = "LEFT"
    RIGHT = "RIGHT"
    UNKNOWN = "UNKNOWN"


class FingerState(str, enum.Enum):
    """Categorical extension state of an individual finger."""

    EXTENDED = "EXTENDED"
    FOLDED = "FOLDED"
    UNKNOWN = "UNKNOWN"


class HandTrackState(str, enum.Enum):
    """Authoritative lifecycle states of an individual hand track."""

    TENTATIVE = "TENTATIVE"
    CONFIRMED = "CONFIRMED"
    COASTING = "COASTING"
    LOST = "LOST"
    RETIRED = "RETIRED"


class GestureType(str, enum.Enum):
    """Standardized gesture primitive and composite classification types."""

    PINCH = "PINCH"
    POINT = "POINT"
    TWO_FINGER = "TWO_FINGER"
    THUMB_UP = "THUMB_UP"
    THUMB_DOWN = "THUMB_DOWN"
    OPEN_PALM = "OPEN_PALM"
    CLOSED_HAND = "CLOSED_HAND"
    UNKNOWN = "UNKNOWN"


class GestureState(str, enum.Enum):
    """Authoritative 5-state lifecycle for active gesture state machines."""

    IDLE = "IDLE"
    CANDIDATE = "CANDIDATE"
    ACTIVE = "ACTIVE"
    RELEASED = "RELEASED"
    CANCELLED = "CANCELLED"


class GestureQuality(str, enum.Enum):
    """Synchronous health and observational fidelity assessment."""

    HEALTHY = "HEALTHY"
    DEGRADED = "DEGRADED"
    UNHEALTHY = "UNHEALTHY"
    FAILED = "FAILED"


@dataclass(frozen=True)
class HandLandmark3D:
    """Canonical 3D metric and projected 2D landmark model in M01 optical convention."""

    landmark_id: int
    name: str
    x: float
    y: float
    z: float
    u: float
    v: float
    confidence: float
    is_occluded: bool = False

    def __post_init__(self) -> None:
        if type(self.landmark_id) is not int or not (0 <= self.landmark_id < MAX_HAND_LANDMARKS):
            raise GestureValidationError(
                f"landmark_id must be int in range [0, {MAX_HAND_LANDMARKS}), got {self.landmark_id!r}"
            )
        if not isinstance(self.name, str) or not self.name:
            raise GestureValidationError("Landmark name must be non-empty string")

        for val, name in (
            (self.x, "x"), (self.y, "y"), (self.z, "z"),
            (self.u, "u"), (self.v, "v"), (self.confidence, "confidence")
        ):
            if not isinstance(val, (int, float)) or math.isnan(val) or math.isinf(val):
                raise GestureValidationError(f"Landmark {name} must be a finite float, got {val!r}")

        # Coordinate bounds check [-10m, +10m]
        if abs(self.x) > MAX_HAND_COORDINATE_METERS:
            raise GestureValidationError(f"Landmark x ({self.x}m) exceeds bound +/-{MAX_HAND_COORDINATE_METERS}m")
        if abs(self.y) > MAX_HAND_COORDINATE_METERS:
            raise GestureValidationError(f"Landmark y ({self.y}m) exceeds bound +/-{MAX_HAND_COORDINATE_METERS}m")
        if abs(self.z) > MAX_HAND_COORDINATE_METERS:
            raise GestureValidationError(f"Landmark z ({self.z}m) exceeds bound +/-{MAX_HAND_COORDINATE_METERS}m")

        if not (0.0 <= self.u <= 1.0):
            raise GestureValidationError(f"Landmark u must be in [0.0, 1.0], got {self.u!r}")
        if not (0.0 <= self.v <= 1.0):
            raise GestureValidationError(f"Landmark v must be in [0.0, 1.0], got {self.v!r}")
        if not (0.0 <= self.confidence <= 1.0):
            raise GestureValidationError(f"Landmark confidence must be in [0.0, 1.0], got {self.confidence!r}")

        object.__setattr__(self, "x", quantize_coord(self.x, 6))
        object.__setattr__(self, "y", quantize_coord(self.y, 6))
        object.__setattr__(self, "z", quantize_coord(self.z, 6))
        object.__setattr__(self, "u", quantize_coord(self.u, 6))
        object.__setattr__(self, "v", quantize_coord(self.v, 6))
        object.__setattr__(self, "confidence", quantize_coord(self.confidence, 6))


def unproject_spatial_landmark(lm: SpatialLandmark) -> HandLandmark3D:
    """Deterministically convert an M01 SpatialLandmark into a metric HandLandmark3D."""
    if lm.depth_m is None or not isinstance(lm.depth_m, (int, float)) or math.isnan(lm.depth_m) or math.isinf(lm.depth_m):
        raise GestureValidationError(f"Landmark {lm.name} ({lm.landmark_id}) has invalid or missing depth_m")
    if lm.depth_m < MIN_METRIC_DEPTH_METERS or lm.depth_m > MAX_HAND_COORDINATE_METERS:
        raise GestureValidationError(
            f"Landmark depth {lm.depth_m}m out of valid range [{MIN_METRIC_DEPTH_METERS}, {MAX_HAND_COORDINATE_METERS}]"
        )

    z = float(lm.depth_m)
    x = (lm.u - 0.5) * z
    y = (0.5 - lm.v) * z  # Optical space: +Y points UP

    name = CANONICAL_LANDMARK_NAMES[lm.landmark_id] if 0 <= lm.landmark_id < len(CANONICAL_LANDMARK_NAMES) else lm.name

    return HandLandmark3D(
        landmark_id=lm.landmark_id,
        name=name,
        x=x,
        y=y,
        z=z,
        u=lm.u,
        v=lm.v,
        confidence=lm.confidence,
        is_occluded=lm.is_occluded,
    )


@dataclass(frozen=True)
class HandObservation:
    """Immutable sensory input observation ingested from upstream M01 vision."""

    frame_id: str
    device_id: str
    physical_id: str
    sequence_number: int
    timestamp_utc: str
    epoch_id: int
    hand_id: str
    handedness: Handedness | str
    landmarks: tuple[SpatialLandmark, ...]
    confidence: float
    is_partial: bool = False

    def __post_init__(self) -> None:
        # Validate UUIDv4 frame_id
        if not isinstance(self.frame_id, str):
            raise GestureValidationError(f"frame_id must be str, got {type(self.frame_id).__name__}")
        try:
            parsed = uuid.UUID(self.frame_id, version=4)
            if str(parsed) != self.frame_id:
                raise ValueError()
        except Exception:
            raise GestureValidationError(f"frame_id must be canonical lowercase UUIDv4, got {self.frame_id!r}")

        if not isinstance(self.device_id, str) or not (1 <= len(self.device_id) <= MAX_DEVICE_ID_LENGTH):
            raise GestureValidationError(f"Invalid device_id syntax or length: {self.device_id!r}")
        if not isinstance(self.physical_id, str) or not self.physical_id:
            raise GestureValidationError(f"physical_id cannot be empty, got {self.physical_id!r}")
        if type(self.sequence_number) is not int or self.sequence_number < 0:
            raise GestureValidationError(f"sequence_number must be non-negative int, got {self.sequence_number!r}")
        if not isinstance(self.timestamp_utc, str) or not self.timestamp_utc:
            raise GestureValidationError("timestamp_utc cannot be empty")
        if type(self.epoch_id) is not int or self.epoch_id <= 0:
            raise GestureValidationError(f"epoch_id must be positive int, got {self.epoch_id!r}")
        if not isinstance(self.hand_id, str) or not self.hand_id:
            raise GestureValidationError("hand_id cannot be empty")

        # Handedness coercion/validation
        if isinstance(self.handedness, str):
            try:
                object.__setattr__(self, "handedness", Handedness(self.handedness))
            except ValueError:
                raise GestureValidationError(f"Invalid handedness value: {self.handedness!r}")
        elif not isinstance(self.handedness, Handedness):
            raise GestureValidationError(f"Invalid handedness type: {type(self.handedness).__name__}")

        if not isinstance(self.landmarks, tuple):
            raise GestureValidationError("landmarks must be an immutable tuple")
        if len(self.landmarks) == 0:
            raise GestureValidationError("landmarks cannot be empty")
        if len(self.landmarks) > MAX_HAND_LANDMARKS:
            raise GestureValidationError(
                f"landmarks ({len(self.landmarks)}) exceeds MAX_HAND_LANDMARKS ({MAX_HAND_LANDMARKS})"
            )
        if len(self.landmarks) < MAX_HAND_LANDMARKS and not self.is_partial:
            raise GestureValidationError(
                f"Partial hand landmark count ({len(self.landmarks)}) requires is_partial=True"
            )

        # Unique landmark IDs
        seen_ids: set[int] = set()
        for lm in self.landmarks:
            if not isinstance(lm, SpatialLandmark):
                raise GestureValidationError(f"Landmark element must be SpatialLandmark, got {type(lm).__name__}")
            if lm.landmark_id in seen_ids:
                raise GestureValidationError(f"Duplicate landmark_id {lm.landmark_id} in observation")
            seen_ids.add(lm.landmark_id)

        if not isinstance(self.confidence, (int, float)) or math.isnan(self.confidence) or math.isinf(self.confidence):
            raise GestureValidationError(f"confidence must be finite float, got {self.confidence!r}")
        if not (0.0 <= self.confidence <= 1.0):
            raise GestureValidationError(f"confidence must be in [0.0, 1.0], got {self.confidence!r}")

        object.__setattr__(self, "confidence", quantize_coord(self.confidence, 6))


@dataclass(frozen=True)
class FingerStateSet:
    """Immutable state set capturing the flexion status of all five digits."""

    thumb: FingerState
    index: FingerState
    middle: FingerState
    ring: FingerState
    pinky: FingerState


@dataclass(frozen=True)
class SpatialInteraction:
    """Immutable geometric interaction points, rays, and normals."""

    pointing_ray_origin: Optional[tuple[float, float, float]] = None
    pointing_ray_direction: Optional[tuple[float, float, float]] = None
    pinch_point: Optional[tuple[float, float, float]] = None
    palm_center: Optional[tuple[float, float, float]] = None
    palm_normal: Optional[tuple[float, float, float]] = None


@dataclass(frozen=True)
class GestureResult:
    """Immutable outcome produced for an individual classified gesture."""

    hand_track_id: str
    gesture_type: GestureType
    state: str
    confidence: float
    timestamp_utc: str
    sequence_number: int
    epoch_id: int
    interaction_point: Optional[tuple[float, float, float]] = None
    interaction_direction: Optional[tuple[float, float, float]] = None

    def __post_init__(self) -> None:
        if not (0.0 <= self.confidence <= 1.0):
            raise GestureValidationError(f"Gesture confidence out of bounds: {self.confidence}")
        object.__setattr__(self, "confidence", quantize_coord(self.confidence, 6))


@dataclass(frozen=True)
class HandTrack:
    """Immutable temporal spatial hand trajectory state."""

    track_id: str
    state: HandTrackState
    handedness: Handedness
    landmarks_3d: tuple[HandLandmark3D, ...]
    wrist_history: tuple[tuple[float, float, float], ...]
    consecutive_detections: int
    coasting_frames: int

    def __post_init__(self) -> None:
        if len(self.wrist_history) > MAX_HAND_HISTORY:
            raise GestureCapacityError(
                f"wrist_history ({len(self.wrist_history)}) exceeds MAX_HAND_HISTORY ({MAX_HAND_HISTORY})"
            )


@dataclass(frozen=True)
class GestureProcessingResult:
    """Immutable holistic outcome produced by running GesturePipeline on an observation."""

    frame_id: str
    device_id: str
    sequence_number: int
    timestamp_utc: str
    epoch_id: int
    quality: GestureQuality
    tracks: tuple[HandTrack, ...]
    gestures: tuple[GestureResult, ...]
    execution_time_ms: float
    budget_exceeded: bool
    error_message: Optional[str] = None

    def __post_init__(self) -> None:
        if len(self.tracks) > MAX_TRACKED_HANDS:
            raise GestureCapacityError(
                f"tracks ({len(self.tracks)}) exceeds MAX_TRACKED_HANDS ({MAX_TRACKED_HANDS})"
            )


def convert_for_json_serialization(obj: Any) -> Any:
    """Recursively convert nested MappingProxyType, sets, tuples, and floats for JSON."""
    if isinstance(obj, (MappingProxyType, dict)):
        return {str(k): convert_for_json_serialization(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [convert_for_json_serialization(item) for item in obj]
    if isinstance(obj, (set, frozenset)):
        return sorted([convert_for_json_serialization(item) for item in obj], key=str)
    if isinstance(obj, str):
        return unicodedata.normalize("NFC", obj)
    if isinstance(obj, float):
        if math.isnan(obj) or math.isinf(obj):
            raise GestureValidationError(f"Non-finite float {obj} cannot be serialized to JSON")
        if obj == 0.0:
            return 0.0
        return obj
    if isinstance(obj, enum.Enum):
        return obj.value
    return obj


def serialize_gesture_payload(payload_dict: Mapping[str, Any]) -> dict[str, Any]:
    """Canonicalize, freeze, and validate wire size of gesture payload (< 64 KiB)."""
    clean_dict = convert_for_json_serialization(payload_dict)
    serialized = json.dumps(clean_dict, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    if len(serialized.encode("utf-8")) > MAX_GESTURE_PAYLOAD_BYTES:
        raise GestureCapacityError(
            f"Gesture payload ({len(serialized.encode('utf-8'))} bytes) exceeds MAX_GESTURE_PAYLOAD_BYTES ({MAX_GESTURE_PAYLOAD_BYTES})"
        )
    return clean_dict
