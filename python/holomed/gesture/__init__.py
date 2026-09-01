# -*- coding: utf-8 -*-
"""HoloMed AI Gesture, Hand Tracking & Spatial Interaction subsystem (M03)."""

from __future__ import annotations

from holomed.gesture.events import RecordingGestureEventSink
from holomed.gesture.exceptions import (
    GestureCapacityError,
    GestureEpochMismatchError,
    GestureError,
    GestureLifecycleError,
    GesturePipelineError,
    GestureResourceIntegrityError,
    GestureSequenceError,
    GestureShutdownError,
    GestureValidationError,
)
from holomed.gesture.models import (
    CANONICAL_LANDMARK_NAMES,
    COORDINATE_EPSILON,
    FingerState,
    FingerStateSet,
    GestureProcessingResult,
    GestureQuality,
    GestureResult,
    GestureState,
    GestureType,
    HAND_ASSOCIATION_THRESHOLD_METERS,
    HAND_CONFIRMATION_FRAMES,
    HandLandmark3D,
    HandObservation,
    HandTrack,
    HandTrackState,
    Handedness,
    MAX_GESTURE_EVENT_KEYS,
    MAX_GESTURE_HISTORY,
    MAX_GESTURE_PAYLOAD_BYTES,
    MAX_GESTURE_PROCESSING_BUDGET_MS,
    MAX_HAND_COASTING_FRAMES,
    MAX_HAND_COORDINATE_METERS,
    MAX_HAND_HISTORY,
    MAX_HAND_LANDMARKS,
    MAX_RECORDED_GESTURE_EVENTS,
    MAX_TRACKED_HANDS,
    MIN_METRIC_DEPTH_METERS,
    NORMALIZED_QUANTIZATION,
    PINCH_DISTANCE_METERS,
    SpatialInteraction,
    quantize_coord,
    serialize_gesture_payload,
    unproject_spatial_landmark,
)
from holomed.gesture.pipeline import GesturePipeline
from holomed.gesture.primitives import (
    classify_gesture_primitive,
    compute_finger_states,
)
from holomed.gesture.service import GestureService
from holomed.gesture.spatial import compute_spatial_interaction
from holomed.gesture.state_machine import GestureStateMachine
from holomed.gesture.tracker import HandTracker

__all__ = [
    # Constants
    "MAX_HAND_LANDMARKS",
    "MAX_HAND_COORDINATE_METERS",
    "PINCH_DISTANCE_METERS",
    "HAND_CONFIRMATION_FRAMES",
    "MAX_HAND_COASTING_FRAMES",
    "MAX_TRACKED_HANDS",
    "MAX_HAND_HISTORY",
    "MAX_GESTURE_HISTORY",
    "MAX_GESTURE_EVENT_KEYS",
    "MAX_RECORDED_GESTURE_EVENTS",
    "MAX_GESTURE_PROCESSING_BUDGET_MS",
    "MAX_GESTURE_PAYLOAD_BYTES",
    "HAND_ASSOCIATION_THRESHOLD_METERS",
    "NORMALIZED_QUANTIZATION",
    "COORDINATE_EPSILON",
    "MIN_METRIC_DEPTH_METERS",
    "CANONICAL_LANDMARK_NAMES",
    # Enums
    "Handedness",
    "FingerState",
    "HandTrackState",
    "GestureType",
    "GestureState",
    "GestureQuality",
    # Models
    "HandLandmark3D",
    "HandObservation",
    "FingerStateSet",
    "SpatialInteraction",
    "GestureResult",
    "HandTrack",
    "GestureProcessingResult",
    # Helpers
    "quantize_coord",
    "unproject_spatial_landmark",
    "serialize_gesture_payload",
    # Exceptions
    "GestureError",
    "GestureLifecycleError",
    "GestureCapacityError",
    "GestureValidationError",
    "GestureEpochMismatchError",
    "GestureSequenceError",
    "GesturePipelineError",
    "GestureResourceIntegrityError",
    "GestureShutdownError",
    # Pipeline & Subsystems
    "compute_finger_states",
    "classify_gesture_primitive",
    "compute_spatial_interaction",
    "HandTracker",
    "GestureStateMachine",
    "RecordingGestureEventSink",
    "GesturePipeline",
    "GestureService",
]
