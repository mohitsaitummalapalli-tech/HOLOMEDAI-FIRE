# -*- coding: utf-8 -*-
"""HoloMed AI Headless XR Visualization & Presentation Foundation (M06)."""

from __future__ import annotations

from holomed.xr.camera import ViewportManager
from holomed.xr.events import RecordingXREventSink
from holomed.xr.exceptions import (
    XRCapacityError,
    XREpochMismatchError,
    XRError,
    XRHierarchyError,
    XRLifecycleError,
    XRProjectionError,
    XRResourceIntegrityError,
    XRShutdownError,
    XRValidationError,
)
from holomed.xr.models import (
    MAX_ANNOTATION_LENGTH,
    MAX_ANNOTATIONS,
    MAX_ASPECT_RATIO,
    MAX_FAR_CLIP_M,
    MAX_FOV_DEG,
    MAX_INTERACTION_RAY_LENGTH_M,
    MAX_RECORDED_XR_EVENTS,
    MAX_SCENE_DEPTH,
    MAX_SCENE_NODES,
    MAX_VIEWPORTS,
    MAX_XR_PAYLOAD_BYTES,
    MIN_ASPECT_RATIO,
    MIN_FOV_DEG,
    MIN_NEAR_CLIP_M,
    AnnotationItem,
    CameraProjectionType,
    CameraViewport,
    InteractionRay,
    PresentationDescription,
    ProjectionResult,
    ViewportOrientation,
    VisibilityState,
    VisualCategory,
    VisualNode,
)
from holomed.xr.presentation import PresentationEngine
from holomed.xr.projection import project_world_point_to_viewport
from holomed.xr.renderer import (
    DefaultExternalRendererAdapter,
    IExternalRendererAdapter,
)
from holomed.xr.scene import SceneGraph
from holomed.xr.serialization import (
    serialize_xr_bytes,
    serialize_xr_payload,
)
from holomed.xr.service import XRService
from holomed.xr.transforms import (
    convert_right_to_left_handed_point,
    convert_right_to_left_handed_quaternion,
    convert_right_to_left_handed_transform,
)

__all__ = [
    # Exceptions
    "XRError",
    "XRLifecycleError",
    "XRValidationError",
    "XRCapacityError",
    "XREpochMismatchError",
    "XRHierarchyError",
    "XRProjectionError",
    "XRResourceIntegrityError",
    "XRShutdownError",
    # Constants
    "MAX_SCENE_NODES",
    "MAX_SCENE_DEPTH",
    "MAX_VIEWPORTS",
    "MAX_ANNOTATIONS",
    "MAX_ANNOTATION_LENGTH",
    "MAX_RECORDED_XR_EVENTS",
    "MAX_XR_PAYLOAD_BYTES",
    "MAX_INTERACTION_RAY_LENGTH_M",
    "MIN_NEAR_CLIP_M",
    "MAX_FAR_CLIP_M",
    "MIN_FOV_DEG",
    "MAX_FOV_DEG",
    "MIN_ASPECT_RATIO",
    "MAX_ASPECT_RATIO",
    # Enums
    "VisualCategory",
    "VisibilityState",
    "CameraProjectionType",
    "ViewportOrientation",
    # Models
    "VisualNode",
    "CameraViewport",
    "ProjectionResult",
    "AnnotationItem",
    "InteractionRay",
    "PresentationDescription",
    # Transforms & Conversions
    "convert_right_to_left_handed_point",
    "convert_right_to_left_handed_quaternion",
    "convert_right_to_left_handed_transform",
    # Subsystems
    "SceneGraph",
    "ViewportManager",
    "PresentationEngine",
    "project_world_point_to_viewport",
    "IExternalRendererAdapter",
    "DefaultExternalRendererAdapter",
    "RecordingXREventSink",
    "serialize_xr_payload",
    "serialize_xr_bytes",
    "XRService",
]
