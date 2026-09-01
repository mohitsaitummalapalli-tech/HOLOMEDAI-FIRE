# -*- coding: utf-8 -*-
"""Spatial Registration and Frame Transformation Solvers for M05 Anatomy."""

from __future__ import annotations

import math
from typing import Optional, Sequence

from holomed.anatomy.exceptions import AnatomyValidationError
from holomed.anatomy.geometry import (
    apply_rigid_transform,
    point_distance,
    quaternion_multiply,
    rotate_vector_by_quaternion,
)
from holomed.anatomy.models import (
    BoundingBox3D,
    COORDINATE_EPSILON,
    Point3D,
    RigidTransform3D,
    Vector3D,
)


class SpatialRegistrationSolver:
    """Computes rigid spatial alignments and landmark target matching."""

    @staticmethod
    def align_landmarks(
        source_landmarks: Sequence[Point3D],
        target_landmarks: Sequence[Point3D],
        source_frame: str,
        target_frame: str,
    ) -> RigidTransform3D:
        """Compute closed-form translation alignment between source and target landmark centroids."""
        if len(source_landmarks) != len(target_landmarks) or not source_landmarks:
            raise AnatomyValidationError(
                "Source and target landmarks must have matching non-zero lengths"
            )

        n = float(len(source_landmarks))
        src_cx = sum(p.x for p in source_landmarks) / n
        src_cy = sum(p.y for p in source_landmarks) / n
        src_cz = sum(p.z for p in source_landmarks) / n

        tgt_cx = sum(p.x for p in target_landmarks) / n
        tgt_cy = sum(p.y for p in target_landmarks) / n
        tgt_cz = sum(p.z for p in target_landmarks) / n

        # Translation aligns centroid: t = target_centroid - source_centroid
        tx = tgt_cx - src_cx
        ty = tgt_cy - src_cy
        tz = tgt_cz - src_cz

        return RigidTransform3D(
            translation=Point3D(tx, ty, tz),
            rotation_quaternion=(1.0, 0.0, 0.0, 0.0),  # Identity orientation
            source_frame=source_frame,
            target_frame=target_frame,
        )

    @staticmethod
    def intersect_ray_bounding_box(
        ray_origin: Point3D,
        ray_direction: Vector3D,
        bbox: BoundingBox3D,
    ) -> Optional[float]:
        """Compute intersection distance t >= 0 of ray p = origin + t * dir with axis-aligned bounding box.
        
        Returns minimum positive distance t or None if no intersection.
        """
        if ray_direction.magnitude < COORDINATE_EPSILON:
            return None

        # Normalize direction
        inv_mag = 1.0 / ray_direction.magnitude
        dx = ray_direction.dx * inv_mag
        dy = ray_direction.dy * inv_mag
        dz = ray_direction.dz * inv_mag

        tmin = -float("inf")
        tmax = float("inf")

        for p_origin, d, b_min, b_max in (
            (ray_origin.x, dx, bbox.min_point.x, bbox.max_point.x),
            (ray_origin.y, dy, bbox.min_point.y, bbox.max_point.y),
            (ray_origin.z, dz, bbox.min_point.z, bbox.max_point.z),
        ):
            if abs(d) < COORDINATE_EPSILON:
                if p_origin < b_min or p_origin > b_max:
                    return None
            else:
                t1 = (b_min - p_origin) / d
                t2 = (b_max - p_origin) / d
                if t1 > t2:
                    t1, t2 = t2, t1
                tmin = max(tmin, t1)
                tmax = min(tmax, t2)
                if tmin > tmax:
                    return None

        if tmax < 0.0:
            return None
        return tmin if tmin >= 0.0 else tmax
