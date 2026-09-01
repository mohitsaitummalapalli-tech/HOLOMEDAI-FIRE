# -*- coding: utf-8 -*-
"""Unit tests for SceneGraph Tree Invariants, Traversal, and Cycles (D230)."""

from __future__ import annotations

import pytest

from holomed.anatomy.models import BoundingBox3D, Point3D, RigidTransform3D
from holomed.xr.exceptions import XRCapacityError, XRHierarchyError
from holomed.xr.models import (
    VisibilityState,
    VisualCategory,
    VisualNode,
)
from holomed.xr.scene import SceneGraph


def make_test_node(node_id: str, parent_id: str | None = None) -> VisualNode:
    pose = RigidTransform3D(Point3D(0.0, 0.0, 0.0), (1.0, 0.0, 0.0, 0.0), "a", "b")
    bbox = BoundingBox3D(Point3D(0.0, 0.0, 0.0), Point3D(1.0, 1.0, 1.0))
    return VisualNode(
        node_id=node_id,
        name=node_id,
        category=VisualCategory.ANATOMY,
        parent_id=parent_id,
        local_transform=pose,
        bounding_volume=bbox,
        visibility=VisibilityState.VISIBLE,
        opacity=1.0,
        layer_id=0,
        presentation_reference="ref",
        is_simulated=True,
        uncertainty_radius_m=0.0,
        metadata={},
    )


def test_d230_scene_graph_cycle_rejection() -> None:
    """Verify D230 cycle detection catches self-cycles and multi-node cycles."""
    sg = SceneGraph()
    root = make_test_node("root")
    sg.add_node(root)

    # Self-cycle
    with pytest.raises(XRHierarchyError):
        sg.add_node(make_test_node("self_cycle", parent_id="self_cycle"))


def test_d230_scene_depth_limit() -> None:
    """Verify scene depth limit is strictly capped at MAX_SCENE_DEPTH (8)."""
    sg = SceneGraph()
    sg.add_node(make_test_node("node_1", parent_id=None))

    for d in range(2, 9):  # depth 2 to 8
        sg.add_node(make_test_node(f"node_{d}", parent_id=f"node_{d-1}"))

    # Depth 9 must be rejected
    with pytest.raises(XRHierarchyError):
        sg.add_node(make_test_node("node_9", parent_id="node_8"))


def test_scene_graph_capacity_256() -> None:
    """Verify MAX_SCENE_NODES = 256 capacity limit."""
    sg = SceneGraph()
    sg.add_node(make_test_node("root"))

    for i in range(1, 256):
        sg.add_node(make_test_node(f"node_{i}", parent_id="root"))

    assert sg.node_count == 256

    # 257th node raises XRCapacityError
    with pytest.raises(XRCapacityError):
        sg.add_node(make_test_node("node_overflow", parent_id="root"))


def test_global_transform_accumulation() -> None:
    """Verify global transform accumulates translations down the hierarchy."""
    sg = SceneGraph()
    t1 = RigidTransform3D(Point3D(1, 0, 0), (1, 0, 0, 0), "node_1", "world")
    bbox = BoundingBox3D(Point3D(0, 0, 0), Point3D(1, 1, 1))
    n1 = VisualNode("n1", "n1", VisualCategory.ANATOMY, None, t1, bbox, VisibilityState.VISIBLE, 1.0, 0, "r", True, 0.0, {})
    sg.add_node(n1)

    t2 = RigidTransform3D(Point3D(0, 2, 0), (1, 0, 0, 0), "node_2", "node_1")
    n2 = VisualNode("n2", "n2", VisualCategory.ANATOMY, "n1", t2, bbox, VisibilityState.VISIBLE, 1.0, 0, "r", True, 0.0, {})
    sg.add_node(n2)

    gt = sg.get_global_transform("n2")
    assert gt.translation.x == 1.0
    assert gt.translation.y == 2.0
    assert gt.translation.z == 0.0
