# -*- coding: utf-8 -*-
"""Unit Tests for Registration Models, Fiducial Validation, and Spacing Rules."""

from __future__ import annotations

from dataclasses import replace
import pytest

from holomed.registration.exceptions import (
    RegistrationCapacityError,
    RegistrationDegeneracyError,
    RegistrationValidationError,
)
from holomed.registration.models import (
    FiducialCloud,
    FiducialPointPair,
    RegistrationQualityReport,
    RegistrationState,
    RegistrationStatusRecord,
    RigidRegistrationTransform3D,
)


def test_fiducial_point_pair_valid() -> None:
    """Verify valid FiducialPointPair construction."""
    pair = FiducialPointPair(
        fiducial_id="fid_asis_r",
        planned_point_mm=(10.0, 20.0, 30.0),
        measured_point_mm=(15.0, 25.0, 35.0),
        label="anterior_superior_iliac_spine",
    )
    assert pair.fiducial_id == "fid_asis_r"
    assert pair.planned_point_mm == (10.0, 20.0, 30.0)


def test_fiducial_point_pair_rejects_nan_and_inf() -> None:
    """Verify rejection of NaN and Inf in coordinates."""
    with pytest.raises(RegistrationValidationError):
        FiducialPointPair(
            fiducial_id="fid_bad",
            planned_point_mm=(float("nan"), 0.0, 0.0),
            measured_point_mm=(0.0, 0.0, 0.0),
            label="test",
        )

    with pytest.raises(RegistrationValidationError):
        FiducialPointPair(
            fiducial_id="fid_bad",
            planned_point_mm=(0.0, 0.0, 0.0),
            measured_point_mm=(0.0, float("inf"), 0.0),
            label="test",
        )


def test_fiducial_point_pair_rejects_coordinate_bounds() -> None:
    """Verify coordinates exceeding operational bound (+/- 2000 mm) are rejected."""
    with pytest.raises(RegistrationValidationError):
        FiducialPointPair(
            fiducial_id="fid_far",
            planned_point_mm=(2000.1, 0.0, 0.0),
            measured_point_mm=(0.0, 0.0, 0.0),
            label="test",
        )


def test_fiducial_cloud_capacity_bounds(sample_fiducials: tuple[FiducialPointPair, ...]) -> None:
    """Verify minimum 3 points and maximum 16 points limits."""
    # Under minimum (< 3 points)
    with pytest.raises(RegistrationValidationError):
        FiducialCloud(pairs=sample_fiducials[:2])

    # Over maximum (> 16 points)
    overflow = tuple(
        FiducialPointPair(
            fiducial_id=f"fid_{i:02d}",
            planned_point_mm=(float(i * 20), float(i * 20), 0.0),
            measured_point_mm=(float(i * 20), float(i * 20), 0.0),
            label=f"pt_{i}",
        )
        for i in range(17)
    )
    with pytest.raises(RegistrationCapacityError):
        FiducialCloud(pairs=overflow)


def test_fiducial_cloud_minimum_spacing_rejection() -> None:
    """Verify fiducials closer than 10 mm in either frame are rejected."""
    # Points closer than 10 mm in planned frame (distance = 5 mm)
    p1 = FiducialPointPair("f1", (0.0, 0.0, 0.0), (0.0, 0.0, 0.0), "p1")
    p2 = FiducialPointPair("f2", (5.0, 0.0, 0.0), (50.0, 0.0, 0.0), "p2")
    p3 = FiducialPointPair("f3", (0.0, 50.0, 0.0), (0.0, 50.0, 0.0), "p3")

    with pytest.raises(RegistrationValidationError) as exc:
        FiducialCloud(pairs=(p1, p2, p3))
    assert "spacing" in str(exc.value)


def test_collinear_fiducial_rejection() -> None:
    """Verify 1D collinear fiducials are detected and rejected as degenerate (D313)."""
    # 3 points on line y = 0, z = 0
    p1 = FiducialPointPair("f1", (0.0, 0.0, 0.0), (0.0, 0.0, 0.0), "p1")
    p2 = FiducialPointPair("f2", (20.0, 0.0, 0.0), (20.0, 0.0, 0.0), "p2")
    p3 = FiducialPointPair("f3", (50.0, 0.0, 0.0), (50.0, 0.0, 0.0), "p3")

    with pytest.raises(RegistrationDegeneracyError) as exc:
        FiducialCloud(pairs=(p1, p2, p3))
    assert "collinear" in str(exc.value)


def test_coplanar_non_collinear_fiducials_accepted() -> None:
    """Verify non-collinear coplanar points are accepted and NOT marked degenerate."""
    # 3 non-collinear points on the z=0 plane spanning a 50x50 triangle
    p1 = FiducialPointPair("f1", (0.0, 0.0, 0.0), (0.0, 0.0, 0.0), "p1")
    p2 = FiducialPointPair("f2", (50.0, 0.0, 0.0), (50.0, 0.0, 0.0), "p2")
    p3 = FiducialPointPair("f3", (0.0, 50.0, 0.0), (0.0, 50.0, 0.0), "p3")

    cloud = FiducialCloud(pairs=(p1, p2, p3))
    assert len(cloud.pairs) == 3


def test_rigid_transform_validation() -> None:
    """Verify RigidRegistrationTransform3D validates orthonormality and det=+1."""
    # Valid identity transform
    t_id = RigidRegistrationTransform3D(
        rotation_matrix=((1.0, 0.0, 0.0), (0.0, 1.0, 0.0), (0.0, 0.0, 1.0)),
        translation_vector_mm=(0.0, 0.0, 0.0),
        source_frame="plan_frame",
        target_frame="patient_tracker_frame",
        epoch_id=1,
        created_at_utc="2026-09-01T12:00:00Z",
    )
    assert t_id.epoch_id == 1

    # Reflection matrix (det = -1) -> rejected!
    with pytest.raises(RegistrationValidationError) as exc:
        RigidRegistrationTransform3D(
            rotation_matrix=((-1.0, 0.0, 0.0), (0.0, 1.0, 0.0), (0.0, 0.0, 1.0)),
            translation_vector_mm=(0.0, 0.0, 0.0),
            source_frame="plan_frame",
            target_frame="patient_tracker_frame",
            epoch_id=1,
            created_at_utc="2026-09-01T12:00:00Z",
        )
    assert "determinant" in str(exc.value)

    # Non-orthogonal matrix -> rejected!
    with pytest.raises(RegistrationValidationError):
        RigidRegistrationTransform3D(
            rotation_matrix=((1.0, 0.5, 0.0), (0.0, 1.0, 0.0), (0.0, 0.0, 1.0)),
            translation_vector_mm=(0.0, 0.0, 0.0),
            source_frame="plan_frame",
            target_frame="patient_tracker_frame",
            epoch_id=1,
            created_at_utc="2026-09-01T12:00:00Z",
        )
