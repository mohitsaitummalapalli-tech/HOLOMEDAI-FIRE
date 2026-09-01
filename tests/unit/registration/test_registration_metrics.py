# -*- coding: utf-8 -*-
"""Unit Tests for Registration Error Metrics, Residuals, and TRE Estimation."""

from __future__ import annotations

import pytest

from holomed.registration.metrics import compute_registration_metrics
from holomed.registration.models import (
    FiducialCloud,
    FiducialPointPair,
    RigidRegistrationTransform3D,
)
from holomed.registration.solver import HornRigidRegistrationSolver


def test_fre_zero_on_exact_points(sample_cloud: FiducialCloud) -> None:
    """Verify FRE is 0.0 when points match exact rigid transform."""
    t = HornRigidRegistrationSolver.solve(sample_cloud)
    metrics = compute_registration_metrics(sample_cloud, t)

    assert pytest.approx(metrics.fre_rms_mm, abs=1e-5) == 0.0
    assert pytest.approx(metrics.fre_max_mm, abs=1e-5) == 0.0
    assert metrics.passed_clinical_threshold is True
    assert metrics.warning_threshold_exceeded is False


def test_fre_with_known_perturbations() -> None:
    """Verify FRE RMS calculation with intentional known residuals (1 mm on each point)."""
    # 4 points with 1.0 mm error in Z
    pts = [
        (0.0, 0.0, 0.0),
        (50.0, 0.0, 0.0),
        (0.0, 50.0, 0.0),
        (50.0, 50.0, 0.0),
    ]
    # Measure points shifted by 1.0 mm in Z
    pairs = tuple(
        FiducialPointPair(f"f{i}", p, (p[0], p[1], p[2] + 1.0), f"p{i}")
        for i, p in enumerate(pts)
    )
    cloud = FiducialCloud(pairs=pairs)
    # If transform is identity (t=0), residual is exactly 1.0 mm on each point -> RMS = 1.0
    t_id = RigidRegistrationTransform3D(
        rotation_matrix=((1.0, 0.0, 0.0), (0.0, 1.0, 0.0), (0.0, 0.0, 1.0)),
        translation_vector_mm=(0.0, 0.0, 0.0),
        source_frame="plan_frame",
        target_frame="patient_tracker_frame",
        epoch_id=1,
        created_at_utc="2026-09-01T12:00:00Z",
    )
    metrics = compute_registration_metrics(cloud, t_id)
    assert pytest.approx(metrics.fre_rms_mm, abs=1e-5) == 1.0
    assert pytest.approx(metrics.fre_max_mm, abs=1e-5) == 1.0
    assert metrics.passed_clinical_threshold is True


def test_tre_model_based_estimate() -> None:
    """Verify model-based TRE estimate calculation."""
    pts = [
        (0.0, 0.0, 0.0),
        (50.0, 0.0, 0.0),
        (0.0, 50.0, 0.0),
        (50.0, 50.0, 0.0),
    ]
    pairs = tuple(
        FiducialPointPair(f"f{i}", p, (p[0], p[1], p[2] + 0.8), f"p{i}")
        for i, p in enumerate(pts)
    )
    cloud = FiducialCloud(pairs=pairs)
    t = HornRigidRegistrationSolver.solve(cloud)

    # Supply surgical target at (25, 25, 20)
    metrics = compute_registration_metrics(cloud, t, target_point_mm=(25.0, 25.0, 20.0))
    assert metrics.target_registration_error_estimate_mm is not None
    assert metrics.target_registration_error_estimate_mm >= 0.0

    # Without target point -> None
    metrics_no_tgt = compute_registration_metrics(cloud, t, target_point_mm=None)
    assert metrics_no_tgt.target_registration_error_estimate_mm is None
