"""HoloMed AI Spatial Vision and Tracking subsystem (M01)."""

from __future__ import annotations

from holomed.vision.exceptions import (
    VisionCapacityError,
    VisionError,
    VisionFrameValidationError,
    VisionLifecycleError,
    VisionPipelineError,
    VisionResourceIntegrityError,
    VisionShutdownError,
    VisionValidationError,
)
from holomed.vision.models import (
    MAX_BUFFERED_FRAMES,
    MAX_DEPTH_METERS,
    MAX_FRAME_BYTES,
    MAX_FRAME_HEIGHT,
    MAX_FRAME_WIDTH,
    MAX_PROCESSING_BUDGET_MS,
    MAX_TRACK_HISTORY_LENGTH,
    MAX_TRACKED_ENTITIES,
    MAX_TRACKED_LANDMARKS,
    MAX_VISION_PAYLOAD_BYTES,
    MIN_DEPTH_METERS,
    NORMALIZED_QUANTIZATION,
    FrameDescriptor,
    PixelFormat,
    SlotState,
    SpatialLandmark,
    SpatialPose,
    TrackState,
    TrackedEntity,
    VisionProcessingResult,
    VisionQuality,
    quantize_normalized_coord,
    serialize_vision_payload,
)
from holomed.vision.pipeline import VisionPipeline
from holomed.vision.service import VisionService
from holomed.vision.store import VisionFrameStore
from holomed.vision.tracker import TemporalLandmarkTracker

__all__ = [
    # Constants
    "MAX_FRAME_WIDTH",
    "MAX_FRAME_HEIGHT",
    "MAX_FRAME_BYTES",
    "MAX_BUFFERED_FRAMES",
    "MAX_TRACKED_LANDMARKS",
    "MIN_DEPTH_METERS",
    "MAX_DEPTH_METERS",
    "MAX_TRACKED_ENTITIES",
    "MAX_TRACK_HISTORY_LENGTH",
    "MAX_PROCESSING_BUDGET_MS",
    "NORMALIZED_QUANTIZATION",
    "MAX_VISION_PAYLOAD_BYTES",
    # Enums
    "PixelFormat",
    "SlotState",
    "TrackState",
    "VisionQuality",
    # Models
    "FrameDescriptor",
    "SpatialLandmark",
    "SpatialPose",
    "TrackedEntity",
    "VisionProcessingResult",
    "quantize_normalized_coord",
    "serialize_vision_payload",
    # Exceptions
    "VisionError",
    "VisionLifecycleError",
    "VisionCapacityError",
    "VisionValidationError",
    "VisionFrameValidationError",
    "VisionPipelineError",
    "VisionResourceIntegrityError",
    "VisionShutdownError",
    # Components
    "VisionFrameStore",
    "TemporalLandmarkTracker",
    "VisionPipeline",
    "VisionService",
]
