# -*- coding: utf-8 -*-
"""Unit tests for PresentationEngine and Spatial Frame Description Assembly."""

from __future__ import annotations

import pytest

from holomed.anatomy.models import Point3D, Vector3D
from holomed.xr.camera import ViewportManager
from holomed.xr.exceptions import XRCapacityError, XRValidationError
from holomed.xr.models import (
    AnnotationItem,
    InteractionRay,
)
from holomed.xr.presentation import PresentationEngine
from holomed.xr.scene import SceneGraph


def test_presentation_annotations_capacity_64() -> None:
    """Verify MAX_ANNOTATIONS = 64 capacity limit (D234)."""
    pe = PresentationEngine()
    for i in range(64):
        pe.add_annotation(
            AnnotationItem(
                annotation_id=f"ann_{i}",
                target_node_id="target",
                text=f"Label {i}",
                spatial_offset=Vector3D(0, 0, 0),
            )
        )
    assert pe.annotation_count == 64

    # 65th annotation raises XRCapacityError
    with pytest.raises(XRCapacityError):
        pe.add_annotation(
            AnnotationItem(
                annotation_id="ann_overflow",
                target_node_id="target",
                text="Overflow",
                spatial_offset=Vector3D(0, 0, 0),
            )
        )


def test_build_presentation_description() -> None:
    """Verify building complete presentation frame snapshot."""
    pe = PresentationEngine()
    pe.set_interaction_ray(InteractionRay(Point3D(0, 0, 0), Vector3D(0, 0, -1)))
    sg = SceneGraph()
    vm = ViewportManager()

    desc = pe.build_description(
        epoch_id=1,
        frame_sequence=42,
        scene_graph=sg,
        viewport_manager=vm,
    )
    assert desc.epoch_id == 1
    assert desc.frame_sequence == 42
    assert desc.is_simulated is True
    assert desc.active_interaction_ray is not None
