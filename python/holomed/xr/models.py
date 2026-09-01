# -*- coding: utf-8 -*-
"""M06 Headless XR Visualization & Presentation Models, Primitives, and Enums."""

from __future__ import annotations

from dataclasses import dataclass
import enum
import math
from types import MappingProxyType
from typing import Any, Mapping, Optional

from holomed.anatomy.models import (
    BoundingBox3D,
    Point3D,
    RigidTransform3D,
    Vector3D,
)
from holomed.xr.exceptions import XRValidationError

# Canonical Hard Bounds
MAX_SCENE_NODES: int = 256
MAX_SCENE_DEPTH: int = 8
MAX_VIEWPORTS: int = 4
MAX_ANNOTATIONS: int = 64
MAX_ANNOTATION_LENGTH: int = 128
MAX_RECORDED_XR_EVENTS: int = 1000
MAX_XR_PAYLOAD_BYTES: int = 65536
MAX_INTERACTION_RAY_LENGTH_M: float = 2.0

MIN_NEAR_CLIP_M: float = 0.001
MAX_FAR_CLIP_M: float = 100.0
MIN_FOV_DEG: float = 5.0
MAX_FOV_DEG: float = 160.0
MIN_ASPECT_RATIO: float = 0.1
MAX_ASPECT_RATIO: float = 10.0


class VisualCategory(str, enum.Enum):
    """Semantic category of visual spatial nodes."""

    ANATOMY = "ANATOMY"
    SURGICAL_TOOL = "SURGICAL_TOOL"
    GESTURE_POINTER = "GESTURE_POINTER"
    ANNOTATION = "ANNOTATION"
    VIEWPORT_GIZMO = "VIEWPORT_GIZMO"
    REFERENCE_FRAME = "REFERENCE_FRAME"


class VisibilityState(str, enum.Enum):
    """Visibility mode for holographic elements."""

    VISIBLE = "VISIBLE"
    HIDDEN = "HIDDEN"
    OCCLUDED = "OCCLUDED"
    GHOSTED = "GHOSTED"  # Semi-transparent / simulated context


class CameraProjectionType(str, enum.Enum):
    """Camera mathematical projection matrix model."""

    PERSPECTIVE = "PERSPECTIVE"
    ORTHOGRAPHIC = "ORTHOGRAPHIC"


class ViewportOrientation(str, enum.Enum):
    """Canonical surgical multi-viewport orientation."""

    PERSPECTIVE_3D = "PERSPECTIVE_3D"
    AXIAL = "AXIAL"
    SAGITTAL = "SAGITTAL"
    CORONAL = "CORONAL"


@dataclass(frozen=True)
class VisualNode:
    """Immutable representation of a visual scene element."""

    node_id: str
    name: str
    category: VisualCategory
    parent_id: Optional[str]
    local_transform: RigidTransform3D
    bounding_volume: BoundingBox3D
    visibility: VisibilityState
    opacity: float  # [0.0, 1.0]
    layer_id: int  # [0, 31] bitmask layer
    presentation_reference: str  # Reference key e.g. "anat.abdomen.liver"
    is_simulated: bool
    uncertainty_radius_m: float  # [0.0, 1.0]
    metadata: Mapping[str, Any]

    def __post_init__(self) -> None:
        if not isinstance(self.node_id, str) or not self.node_id.strip():
            raise XRValidationError("node_id must be a non-empty string")
        if not isinstance(self.name, str) or not self.name.strip():
            raise XRValidationError("name must be a non-empty string")
        if not isinstance(self.category, VisualCategory):
            raise XRValidationError(f"Invalid visual category: {self.category}")
        if self.parent_id is not None and (not isinstance(self.parent_id, str) or not self.parent_id.strip()):
            raise XRValidationError("parent_id must be non-empty string or None")
        if not isinstance(self.local_transform, RigidTransform3D):
            raise XRValidationError("local_transform must be RigidTransform3D")
        if not isinstance(self.bounding_volume, BoundingBox3D):
            raise XRValidationError("bounding_volume must be BoundingBox3D")
        if not isinstance(self.visibility, VisibilityState):
            raise XRValidationError(f"Invalid visibility state: {self.visibility}")

        if not isinstance(self.opacity, (int, float)) or not (0.0 <= self.opacity <= 1.0):
            raise XRValidationError(f"opacity must be in range [0.0, 1.0], got {self.opacity}")

        if not isinstance(self.layer_id, int) or not (0 <= self.layer_id <= 31):
            raise XRValidationError(f"layer_id must be integer in range [0, 31], got {self.layer_id}")

        if not isinstance(self.presentation_reference, str) or not self.presentation_reference.strip():
            raise XRValidationError("presentation_reference must be a non-empty string")

        if not isinstance(self.is_simulated, bool):
            raise XRValidationError("is_simulated must be a boolean")

        if not isinstance(self.uncertainty_radius_m, (int, float)) or not (0.0 <= self.uncertainty_radius_m <= 1.0):
            raise XRValidationError(
                f"uncertainty_radius_m must be in range [0.0, 1.0], got {self.uncertainty_radius_m}"
            )

        # Ensure no executable code in metadata
        for k, v in self.metadata.items():
            if isinstance(v, str) and ("<script" in v.lower() or "exec(" in v.lower() or "import " in v.lower()):
                raise XRValidationError(f"Executable script/code injection detected in metadata key {k!r}")

        object.__setattr__(self, "opacity", round(float(self.opacity), 4))
        object.__setattr__(self, "uncertainty_radius_m", round(float(self.uncertainty_radius_m), 4))
        if not isinstance(self.metadata, MappingProxyType):
            object.__setattr__(self, "metadata", MappingProxyType(dict(self.metadata)))


@dataclass(frozen=True)
class CameraViewport:
    """Viewport camera frustum and viewing pose."""

    viewport_id: str
    orientation: ViewportOrientation
    projection_type: CameraProjectionType
    field_of_view_deg: float
    aspect_ratio: float
    near_clip_m: float
    far_clip_m: float
    view_pose: RigidTransform3D
    is_active: bool

    def __post_init__(self) -> None:
        if not isinstance(self.viewport_id, str) or not self.viewport_id.strip():
            raise XRValidationError("viewport_id must be a non-empty string")
        if not isinstance(self.orientation, ViewportOrientation):
            raise XRValidationError(f"Invalid orientation: {self.orientation}")
        if not isinstance(self.projection_type, CameraProjectionType):
            raise XRValidationError(f"Invalid projection_type: {self.projection_type}")

        for val, name in (
            (self.field_of_view_deg, "field_of_view_deg"),
            (self.aspect_ratio, "aspect_ratio"),
            (self.near_clip_m, "near_clip_m"),
            (self.far_clip_m, "far_clip_m"),
        ):
            if not isinstance(val, (int, float)) or not math.isfinite(val):
                raise XRValidationError(f"{name} must be finite float, got {val!r}")

        if not (MIN_FOV_DEG <= self.field_of_view_deg <= MAX_FOV_DEG):
            raise XRValidationError(
                f"field_of_view_deg must be in [{MIN_FOV_DEG}, {MAX_FOV_DEG}], got {self.field_of_view_deg}"
            )
        if not (MIN_ASPECT_RATIO <= self.aspect_ratio <= MAX_ASPECT_RATIO):
            raise XRValidationError(
                f"aspect_ratio must be in [{MIN_ASPECT_RATIO}, {MAX_ASPECT_RATIO}], got {self.aspect_ratio}"
            )
        if not (MIN_NEAR_CLIP_M <= self.near_clip_m < self.far_clip_m <= MAX_FAR_CLIP_M):
            raise XRValidationError(
                f"Invalid clipping planes: near={self.near_clip_m}, far={self.far_clip_m}. "
                f"Must satisfy {MIN_NEAR_CLIP_M} <= near < far <= {MAX_FAR_CLIP_M}"
            )

        object.__setattr__(self, "field_of_view_deg", round(float(self.field_of_view_deg), 2))
        object.__setattr__(self, "aspect_ratio", round(float(self.aspect_ratio), 4))
        object.__setattr__(self, "near_clip_m", round(float(self.near_clip_m), 4))
        object.__setattr__(self, "far_clip_m", round(float(self.far_clip_m), 4))


@dataclass(frozen=True)
class ProjectionResult:
    """Result of projecting a 3D metric coordinate to Normalized Device Coordinates (NDC)."""

    point_ndc: Optional[tuple[float, float, float]]  # (x_ndc, y_ndc, z_ndc) in [-1.0, 1.0] or None if clipped
    is_visible: bool
    is_clipped: bool


@dataclass(frozen=True)
class AnnotationItem:
    """Bounded text annotation or spatial label."""

    annotation_id: str
    target_node_id: str
    text: str
    spatial_offset: Vector3D
    is_visible: bool = True

    def __post_init__(self) -> None:
        if not isinstance(self.annotation_id, str) or not self.annotation_id.strip():
            raise XRValidationError("annotation_id must be non-empty")
        if not isinstance(self.target_node_id, str) or not self.target_node_id.strip():
            raise XRValidationError("target_node_id must be non-empty")
        if not isinstance(self.text, str) or not self.text.strip():
            raise XRValidationError("text must be non-empty")
        if len(self.text) > MAX_ANNOTATION_LENGTH:
            raise XRValidationError(
                f"Annotation text length ({len(self.text)}) exceeds limit of {MAX_ANNOTATION_LENGTH}"
            )


@dataclass(frozen=True)
class InteractionRay:
    """Bounded 3D spatial ray from hand/pointer."""

    origin: Point3D
    direction: Vector3D
    max_distance_m: float = MAX_INTERACTION_RAY_LENGTH_M

    def __post_init__(self) -> None:
        if not isinstance(self.origin, Point3D):
            raise XRValidationError("origin must be Point3D")
        if not isinstance(self.direction, Vector3D):
            raise XRValidationError("direction must be Vector3D")
        if self.direction.magnitude < 1e-6:
            raise XRValidationError("Ray direction cannot have zero magnitude")
        if self.max_distance_m > MAX_INTERACTION_RAY_LENGTH_M + 1e-6:
            raise XRValidationError(
                f"Ray length ({self.max_distance_m}m) exceeds limit of {MAX_INTERACTION_RAY_LENGTH_M}m"
            )


@dataclass(frozen=True)
class PresentationDescription:
    """Immutable aggregate description of the holographic scene for a single frame."""

    epoch_id: int
    frame_sequence: int
    timestamp_utc: str
    nodes: Mapping[str, VisualNode]
    viewports: Mapping[str, CameraViewport]
    annotations: Mapping[str, AnnotationItem]
    active_interaction_ray: Optional[InteractionRay]
    is_simulated: bool = True

    def __post_init__(self) -> None:
        if not isinstance(self.nodes, MappingProxyType):
            object.__setattr__(self, "nodes", MappingProxyType(dict(self.nodes)))
        if not isinstance(self.viewports, MappingProxyType):
            object.__setattr__(self, "viewports", MappingProxyType(dict(self.viewports)))
        if not isinstance(self.annotations, MappingProxyType):
            object.__setattr__(self, "annotations", MappingProxyType(dict(self.annotations)))
