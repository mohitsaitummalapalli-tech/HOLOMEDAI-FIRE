# -*- coding: utf-8 -*-
"""Unit tests for Spatial and Semantic Anatomy Queries."""

from __future__ import annotations

import pytest

from holomed.anatomy.hierarchy import AnatomyHierarchy
from holomed.anatomy.models import (
    AnatomicalClassification,
    AnatomicalEntity,
    BoundingBox3D,
    Point3D,
    RigidTransform3D,
)
from holomed.anatomy.queries import AnatomyQueryEngine


def make_entity_with_box(
    entity_id: str,
    min_pt: Point3D,
    max_pt: Point3D,
    landmarks: tuple[Point3D, ...] = (),
) -> AnatomicalEntity:
    pose = RigidTransform3D(Point3D(0.0, 0.0, 0.0), (1.0, 0.0, 0.0, 0.0), "a", "b")
    bbox = BoundingBox3D(min_pt, max_pt)
    return AnatomicalEntity(
        entity_id=entity_id,
        name=entity_id,
        classification=AnatomicalClassification.ORGAN,
        parent_id=None,
        reference_pose=pose,
        bounding_volume=bbox,
        landmarks=landmarks,
        metadata={},
    )


def test_spatial_containment_and_intersection_queries() -> None:
    """Verify point containment and bounding box intersection queries."""
    hierarchy = AnatomyHierarchy()
    e1 = make_entity_with_box("anat.heart", Point3D(0.0, 0.0, 0.0), Point3D(1.0, 1.0, 1.0))
    e2 = make_entity_with_box("anat.lung", Point3D(0.5, 0.5, 0.5), Point3D(1.5, 1.5, 1.5))
    hierarchy.add_entity(e1)
    hierarchy.add_entity(e2)

    engine = AnatomyQueryEngine(hierarchy)

    # Point (0.8, 0.8, 0.8) is inside both heart and lung
    contained = engine.find_entity_by_point(Point3D(0.8, 0.8, 0.8))
    assert contained == ("anat.heart", "anat.lung")

    # Point (0.2, 0.2, 0.2) is inside heart only
    contained_heart = engine.find_entity_by_point(Point3D(0.2, 0.2, 0.2))
    assert contained_heart == ("anat.heart",)

    # Intersection with box [0.9, 1.1] intersects both
    intersecting = engine.find_entities_intersecting_box(
        BoundingBox3D(Point3D(0.9, 0.9, 0.9), Point3D(1.1, 1.1, 1.1))
    )
    assert intersecting == ("anat.heart", "anat.lung")


def test_nearest_landmark_query() -> None:
    """Verify nearest landmark finding."""
    hierarchy = AnatomyHierarchy()
    lm1 = Point3D(1.0, 1.0, 1.0)
    lm2 = Point3D(5.0, 5.0, 5.0)
    e = make_entity_with_box("anat.brain", Point3D(0.0, 0.0, 0.0), Point3D(6.0, 6.0, 6.0), (lm1, lm2))
    hierarchy.add_entity(e)

    engine = AnatomyQueryEngine(hierarchy)
    nearest = engine.find_nearest_landmark(Point3D(1.1, 1.0, 1.0))
    assert nearest is not None
    assert nearest[0] == "anat.brain"
    assert nearest[1] == 0  # index 0 (lm1)
    assert abs(nearest[2] - 0.10) < 1e-4
