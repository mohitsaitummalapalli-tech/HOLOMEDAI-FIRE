# -*- coding: utf-8 -*-
"""Spatial and Semantic Query Engine for M05 Anatomy."""

from __future__ import annotations

import time
from typing import Optional, Sequence

from holomed.anatomy.geometry import point_distance
from holomed.anatomy.hierarchy import AnatomyHierarchy
from holomed.anatomy.models import (
    BoundingBox3D,
    Point3D,
)


class AnatomyQueryEngine:
    """Evaluates spatial and semantic queries over anatomical model and hierarchy."""

    def __init__(self, hierarchy: AnatomyHierarchy) -> None:
        self._hierarchy = hierarchy

    def find_entity_by_point(self, point: Point3D) -> tuple[str, ...]:
        """Find entity IDs whose bounding boxes contain the given 3D point, ordered entity_id ASC."""
        matches: list[str] = []
        for entity in self._hierarchy.entities:
            if entity.bounding_volume.contains_point(point):
                matches.append(entity.entity_id)
        return tuple(sorted(matches))

    def find_entities_intersecting_box(self, bbox: BoundingBox3D) -> tuple[str, ...]:
        """Find entity IDs whose bounding boxes intersect the query box, ordered entity_id ASC."""
        matches: list[str] = []
        for entity in self._hierarchy.entities:
            if entity.bounding_volume.intersects(bbox):
                matches.append(entity.entity_id)
        return tuple(sorted(matches))

    def find_nearest_landmark(
        self,
        point: Point3D,
        max_distance: float = 5.0,
    ) -> Optional[tuple[str, int, float]]:
        """Find nearest landmark to point across all registered entities.
        
        Returns:
            Tuple of (entity_id, landmark_index, distance) or None if no landmark within max_distance.
        """
        best_match: Optional[tuple[str, int, float]] = None
        best_dist = float("inf")

        for entity in self._hierarchy.entities:
            for idx, lm in enumerate(entity.landmarks):
                dist = point_distance(point, lm)
                if dist <= max_distance:
                    if dist < best_dist or (abs(dist - best_dist) < 1e-6 and best_match and entity.entity_id < best_match[0]):
                        best_dist = dist
                        best_match = (entity.entity_id, idx, round(dist, 6))

        return best_match
