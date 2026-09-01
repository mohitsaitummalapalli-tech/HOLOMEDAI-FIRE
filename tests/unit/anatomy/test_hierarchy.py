# -*- coding: utf-8 -*-
"""Unit tests for Anatomy Hierarchy and Cycle Detection (D220)."""

from __future__ import annotations

import pytest

from holomed.anatomy.exceptions import (
    AnatomyCapacityError,
    AnatomyHierarchyError,
)
from holomed.anatomy.hierarchy import AnatomyHierarchy
from holomed.anatomy.models import (
    AnatomicalClassification,
    AnatomicalEntity,
    BoundingBox3D,
    Point3D,
    RigidTransform3D,
)


def make_dummy_entity(entity_id: str, parent_id: str | None = None) -> AnatomicalEntity:
    pose = RigidTransform3D(Point3D(0.0, 0.0, 0.0), (1.0, 0.0, 0.0, 0.0), "a", "b")
    bbox = BoundingBox3D(Point3D(0.0, 0.0, 0.0), Point3D(1.0, 1.0, 1.0))
    return AnatomicalEntity(
        entity_id=entity_id,
        name=entity_id,
        classification=AnatomicalClassification.ORGAN,
        parent_id=parent_id,
        reference_pose=pose,
        bounding_volume=bbox,
        landmarks=(),
        metadata={},
    )


def test_d220_hierarchy_cycle_rejection() -> None:
    """Verify D220 cycle detection catches self-parenting and multi-node cycles."""
    hierarchy = AnatomyHierarchy()
    root = make_dummy_entity("anat.body", parent_id=None)
    hierarchy.add_entity(root)

    child = make_dummy_entity("anat.abdomen", parent_id="anat.body")
    hierarchy.add_entity(child)

    # Self-cycle
    with pytest.raises(AnatomyHierarchyError):
        hierarchy.add_entity(make_dummy_entity("anat.self", parent_id="anat.self"))


def test_d220_depth_limit_enforced() -> None:
    """Verify hierarchy depth is strictly capped at MAX_ANATOMY_HIERARCHY_DEPTH (8)."""
    hierarchy = AnatomyHierarchy()
    hierarchy.add_entity(make_dummy_entity("node_1", parent_id=None))

    for d in range(2, 9):  # depth 2 to 8
        hierarchy.add_entity(make_dummy_entity(f"node_{d}", parent_id=f"node_{d-1}"))

    # Depth 9 must be rejected
    with pytest.raises(AnatomyHierarchyError):
        hierarchy.add_entity(make_dummy_entity("node_9", parent_id="node_8"))


def test_hierarchy_capacity_limit_256() -> None:
    """Verify MAX_ANATOMICAL_ENTITIES = 256 capacity limit."""
    hierarchy = AnatomyHierarchy()
    root = make_dummy_entity("root", parent_id=None)
    hierarchy.add_entity(root)

    for i in range(1, 256):
        hierarchy.add_entity(make_dummy_entity(f"entity_{i}", parent_id="root"))

    assert hierarchy.entity_count == 256

    # 257th entity raises AnatomyCapacityError
    with pytest.raises(AnatomyCapacityError):
        hierarchy.add_entity(make_dummy_entity("entity_overflow", parent_id="root"))


def test_hierarchy_ancestry_and_descendants() -> None:
    """Verify deterministic ancestry and descendant traversal."""
    hierarchy = AnatomyHierarchy()
    hierarchy.add_entity(make_dummy_entity("anat.body"))
    hierarchy.add_entity(make_dummy_entity("anat.torso", parent_id="anat.body"))
    hierarchy.add_entity(make_dummy_entity("anat.liver", parent_id="anat.torso"))
    hierarchy.add_entity(make_dummy_entity("anat.stomach", parent_id="anat.torso"))

    # Ancestry from liver up to body
    ancestry = hierarchy.get_ancestry("anat.liver")
    assert ancestry == ("anat.torso", "anat.body")

    # Descendants of torso (breadth-first, sorted)
    descendants = hierarchy.get_descendants("anat.torso")
    assert descendants == ("anat.liver", "anat.stomach")
