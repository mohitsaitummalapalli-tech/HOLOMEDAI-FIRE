# -*- coding: utf-8 -*-
"""Unit tests for M06 XR Data Models, Viewports, and Bounds."""

from __future__ import annotations

import pytest

from holomed.anatomy.models import BoundingBox3D, Point3D, RigidTransform3D, Vector3D
from holomed.xr.exceptions import XRValidationError
from holomed.xr.models import (
    AnnotationItem,
    CameraProjectionType,
    CameraViewport,
    InteractionRay,
    ViewportOrientation,
    VisibilityState,
    VisualCategory,
    VisualNode,
)


def make_dummy_node(node_id: str, metadata: dict | None = None) -> VisualNode:
    pose = RigidTransform3D(Point3D(0.0, 0.0, 0.0), (1.0, 0.0, 0.0, 0.0), "a", "b")
    bbox = BoundingBox3D(Point3D(0.0, 0.0, 0.0), Point3D(1.0, 1.0, 1.0))
    return VisualNode(
        node_id=node_id,
        name=node_id,
        category=VisualCategory.ANATOMY,
        parent_id=None,
        local_transform=pose,
        bounding_volume=bbox,
        visibility=VisibilityState.VISIBLE,
        opacity=0.8,
        layer_id=0,
        presentation_reference="ref",
        is_simulated=True,
        uncertainty_radius_m=0.05,
        metadata=metadata or {},
    )


def test_visual_node_validation_and_script_rejection() -> None:
    """Verify VisualNode validates bounds and strictly rejects script injection (D235)."""
    node = make_dummy_node("node_0")
    assert node.opacity == 0.8
    assert node.is_simulated is True

    # Script injection in metadata
    with pytest.raises(XRValidationError):
        make_dummy_node("node_bad", metadata={"hook": "<script>alert('pwn')</script>"})

    # Opacity bounds [0, 1]
    with pytest.raises(XRValidationError):
        VisualNode(
            node_id="bad",
            name="bad",
            category=VisualCategory.ANATOMY,
            parent_id=None,
            local_transform=RigidTransform3D(Point3D(0, 0, 0), (1, 0, 0, 0), "a", "b"),
            bounding_volume=BoundingBox3D(Point3D(0, 0, 0), Point3D(1, 1, 1)),
            visibility=VisibilityState.VISIBLE,
            opacity=1.5,
            layer_id=0,
            presentation_reference="ref",
            is_simulated=True,
            uncertainty_radius_m=0.0,
            metadata={},
        )


def test_camera_viewport_frustum_validation() -> None:
    """Verify CameraViewport frustum boundaries and bounds (D229, D233)."""
    pose = RigidTransform3D(Point3D(0, 0, 0), (1, 0, 0, 0), "a", "b")

    # Inverted clipping planes (near >= far)
    with pytest.raises(XRValidationError):
        CameraViewport(
            viewport_id="vp",
            orientation=ViewportOrientation.PERSPECTIVE_3D,
            projection_type=CameraProjectionType.PERSPECTIVE,
            field_of_view_deg=60.0,
            aspect_ratio=1.0,
            near_clip_m=10.0,
            far_clip_m=5.0,
            view_pose=pose,
            is_active=True,
        )

    # Invalid FOV (< 5 deg or > 160 deg)
    with pytest.raises(XRValidationError):
        CameraViewport(
            viewport_id="vp",
            orientation=ViewportOrientation.PERSPECTIVE_3D,
            projection_type=CameraProjectionType.PERSPECTIVE,
            field_of_view_deg=2.0,
            aspect_ratio=1.0,
            near_clip_m=0.1,
            far_clip_m=10.0,
            view_pose=pose,
            is_active=True,
        )


def test_annotation_item_length_limit() -> None:
    """Verify annotation text length is capped at 128 characters (D234)."""
    item = AnnotationItem(
        annotation_id="ann_0",
        target_node_id="visual.liver",
        text="Normal annotation",
        spatial_offset=Vector3D(0.0, 0.1, 0.0),
    )
    assert item.text == "Normal annotation"

    with pytest.raises(XRValidationError):
        AnnotationItem(
            annotation_id="ann_bad",
            target_node_id="visual.liver",
            text="x" * 129,
            spatial_offset=Vector3D(0.0, 0.1, 0.0),
        )


def test_interaction_ray_length_limit() -> None:
    """Verify interaction ray length is capped at 2.0m."""
    ray = InteractionRay(
        origin=Point3D(0, 0, 0),
        direction=Vector3D(0, 0, -1),
        max_distance_m=1.5,
    )
    assert ray.max_distance_m == 1.5

    with pytest.raises(XRValidationError):
        InteractionRay(
            origin=Point3D(0, 0, 0),
            direction=Vector3D(0, 0, -1),
            max_distance_m=2.5,
        )
