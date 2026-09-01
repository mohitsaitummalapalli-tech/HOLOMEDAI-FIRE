# -*- coding: utf-8 -*-
"""Unit tests for M05 Anatomy Data Models, Geometry Types, and Enums."""

from __future__ import annotations

import math
from types import MappingProxyType
import pytest

from holomed.anatomy.exceptions import AnatomyValidationError
from holomed.anatomy.models import (
    AnatomicalClassification,
    AnatomicalEntity,
    BoundingBox3D,
    ConvergenceStatus,
    Point3D,
    RigidTransform3D,
    SimulationState,
    TissueStateSnapshot,
    TissueType,
    Vector3D,
)


def test_point3d_finite_and_coordinate_bounds() -> None:
    """Verify Point3D requires finite values and enforces [-10m, +10m] bounds."""
    p = Point3D(1.2345678, -2.0, 0.0)
    assert p.x == 1.234568  # 6-decimal rounding
    assert p.y == -2.0
    assert p.z == 0.0

    # Non-finite rejection
    with pytest.raises(AnatomyValidationError):
        Point3D(float("nan"), 0.0, 0.0)
    with pytest.raises(AnatomyValidationError):
        Point3D(0.0, float("inf"), 0.0)

    # Exceeding 10m clinical bound
    with pytest.raises(AnatomyValidationError):
        Point3D(10.5, 0.0, 0.0)
    with pytest.raises(AnatomyValidationError):
        Point3D(0.0, -10.1, 0.0)


def test_point3d_negative_zero_normalized() -> None:
    """Verify -0.0 coordinate is normalized to +0.0."""
    p = Point3D(-0.0, 0.0, -0.0)
    assert p.x == 0.0
    assert p.z == 0.0
    assert math.copysign(1.0, p.x) == 1.0


def test_vector3d_properties() -> None:
    """Verify Vector3D magnitude and finite checks."""
    v = Vector3D(3.0, 4.0, 0.0)
    assert v.magnitude == 5.0

    with pytest.raises(AnatomyValidationError):
        Vector3D(float("nan"), 0.0, 0.0)


def test_bounding_box_containment_and_intersection() -> None:
    """Verify BoundingBox3D point containment and intersection logic."""
    bbox1 = BoundingBox3D(
        min_point=Point3D(0.0, 0.0, 0.0),
        max_point=Point3D(1.0, 1.0, 1.0),
    )
    # Point inside
    assert bbox1.contains_point(Point3D(0.5, 0.5, 0.5)) is True
    # Point on boundary
    assert bbox1.contains_point(Point3D(1.0, 1.0, 1.0)) is True
    # Point outside
    assert bbox1.contains_point(Point3D(1.5, 0.5, 0.5)) is False

    # Intersecting box
    bbox2 = BoundingBox3D(
        min_point=Point3D(0.5, 0.5, 0.5),
        max_point=Point3D(1.5, 1.5, 1.5),
    )
    assert bbox1.intersects(bbox2) is True

    # Disjoint box
    bbox3 = BoundingBox3D(
        min_point=Point3D(2.0, 2.0, 2.0),
        max_point=Point3D(3.0, 3.0, 3.0),
    )
    assert bbox1.intersects(bbox3) is False

    # Inverted box (min > max)
    with pytest.raises(AnatomyValidationError):
        BoundingBox3D(
            min_point=Point3D(1.0, 1.0, 1.0),
            max_point=Point3D(0.0, 0.0, 0.0),
        )


def test_rigid_transform_quaternion_and_frames() -> None:
    """Verify RigidTransform3D validates unit quaternions and frames."""
    t = Point3D(0.0, 0.0, 0.0)
    # Unit quaternion (1, 0, 0, 0)
    tf = RigidTransform3D(t, (1.0, 0.0, 0.0, 0.0), "source", "target")
    assert tf.rotation_quaternion == (1.0, 0.0, 0.0, 0.0)

    # Non-unit quaternion
    with pytest.raises(AnatomyValidationError):
        RigidTransform3D(t, (1.0, 1.0, 0.0, 0.0), "source", "target")

    # Canonical hemisphere inversion (qw < 0)
    tf_neg = RigidTransform3D(t, (-1.0, 0.0, 0.0, 0.0), "source", "target")
    assert tf_neg.rotation_quaternion == (1.0, 0.0, 0.0, 0.0)

    # Empty frame names
    with pytest.raises(AnatomyValidationError):
        RigidTransform3D(t, (1.0, 0.0, 0.0, 0.0), "", "target")


def test_tissue_state_displacement_boundary() -> None:
    """Verify D224/D226 tissue state displacement cap (0.10m) and is_simulated flag."""
    snap = TissueStateSnapshot(
        entity_id="anat.liver",
        simulation_time_s=0.5,
        displacement=Vector3D(0.05, 0.05, 0.0),
        stress_kpa=12.5,
        strain=0.015,
        temperature_c=37.0,
        is_deformed=True,
    )
    assert snap.is_simulated is True
    assert snap.displacement.magnitude < 0.10

    # Displacement exceeding 0.10m
    with pytest.raises(AnatomyValidationError):
        TissueStateSnapshot(
            entity_id="anat.liver",
            simulation_time_s=0.5,
            displacement=Vector3D(0.20, 0.0, 0.0),
            stress_kpa=10.0,
            strain=0.01,
            temperature_c=37.0,
            is_deformed=True,
        )
