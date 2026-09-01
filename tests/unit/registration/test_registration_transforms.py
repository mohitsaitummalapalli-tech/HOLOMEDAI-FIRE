# -*- coding: utf-8 -*-
"""Unit Tests for Spatial Transformations, Inversion, and Composition."""

from __future__ import annotations

import pytest

from holomed.planning.models import TrajectoryPlan
from holomed.registration.models import RigidRegistrationTransform3D
from holomed.registration.transforms import (
    compose_transforms,
    invert_transform,
    transform_point,
    transform_trajectory,
    transform_vector,
)


def test_transform_point_and_vector() -> None:
    """Verify point transformation applies R and t, while vector transformation applies ONLY R."""
    # 90 deg rotation around Z, translation (10, 20, 30)
    Rz = (
        (0.0, -1.0, 0.0),
        (1.0, 0.0, 0.0),
        (0.0, 0.0, 1.0),
    )
    t = (10.0, 20.0, 30.0)
    trans = RigidRegistrationTransform3D(
        rotation_matrix=Rz,
        translation_vector_mm=t,
        source_frame="plan_frame",
        target_frame="patient_tracker_frame",
        epoch_id=1,
        created_at_utc="2026-09-01T12:00:00Z",
    )

    # Point (1, 0, 0) -> R*(1,0,0) = (0, 1, 0) + (10, 20, 30) = (10, 21, 30)
    p_trans = transform_point(trans, (1.0, 0.0, 0.0))
    assert pytest.approx(p_trans[0], abs=1e-5) == 10.0
    assert pytest.approx(p_trans[1], abs=1e-5) == 21.0
    assert pytest.approx(p_trans[2], abs=1e-5) == 30.0

    # Vector (1, 0, 0) -> R*(1,0,0) = (0, 1, 0) (NO TRANSLATION!)
    v_trans = transform_vector(trans, (1.0, 0.0, 0.0))
    assert pytest.approx(v_trans[0], abs=1e-5) == 0.0
    assert pytest.approx(v_trans[1], abs=1e-5) == 1.0
    assert pytest.approx(v_trans[2], abs=1e-5) == 0.0


def test_invert_transform_consistency() -> None:
    """Verify mathematical consistency: T^-1 * T * p = p."""
    R = (
        (0.0, -1.0, 0.0),
        (1.0, 0.0, 0.0),
        (0.0, 0.0, 1.0),
    )
    t = (15.0, 25.0, 35.0)
    trans = RigidRegistrationTransform3D(
        rotation_matrix=R,
        translation_vector_mm=t,
        source_frame="plan_frame",
        target_frame="patient_tracker_frame",
        epoch_id=1,
        created_at_utc="2026-09-01T12:00:00Z",
    )
    trans_inv = invert_transform(trans)

    p_orig = (12.34, 56.78, 90.12)
    p_fwd = transform_point(trans, p_orig)
    p_back = transform_point(trans_inv, p_fwd)

    assert pytest.approx(p_back[0], abs=1e-5) == p_orig[0]
    assert pytest.approx(p_back[1], abs=1e-5) == p_orig[1]
    assert pytest.approx(p_back[2], abs=1e-5) == p_orig[2]


def test_compose_transforms() -> None:
    """Verify composition T2(T1(p)) matches compose_transforms."""
    t1 = RigidRegistrationTransform3D(
        rotation_matrix=((1.0, 0.0, 0.0), (0.0, 1.0, 0.0), (0.0, 0.0, 1.0)),
        translation_vector_mm=(10.0, 0.0, 0.0),
        source_frame="A",
        target_frame="B",
        epoch_id=1,
        created_at_utc="2026-09-01T12:00:00Z",
    )
    t2 = RigidRegistrationTransform3D(
        rotation_matrix=((0.0, -1.0, 0.0), (1.0, 0.0, 0.0), (0.0, 0.0, 1.0)),
        translation_vector_mm=(0.0, 20.0, 0.0),
        source_frame="B",
        target_frame="C",
        epoch_id=1,
        created_at_utc="2026-09-01T12:00:00Z",
    )
    t_comp = compose_transforms(t1, t2)

    p = (5.0, 5.0, 0.0)
    p_step = transform_point(t2, transform_point(t1, p))
    p_direct = transform_point(t_comp, p)

    assert pytest.approx(p_direct[0], abs=1e-5) == p_step[0]
    assert pytest.approx(p_direct[1], abs=1e-5) == p_step[1]
    assert pytest.approx(p_direct[2], abs=1e-5) == p_step[2]


def test_transform_trajectory_plan() -> None:
    """Verify TrajectoryPlan entry and target points are transformed into patient space."""
    trans = RigidRegistrationTransform3D(
        rotation_matrix=((1.0, 0.0, 0.0), (0.0, 1.0, 0.0), (0.0, 0.0, 1.0)),
        translation_vector_mm=(100.0, 200.0, 300.0),
        source_frame="plan_frame",
        target_frame="patient_tracker_frame",
        epoch_id=1,
        created_at_utc="2026-09-01T12:00:00Z",
    )
    traj = TrajectoryPlan(
        trajectory_id="t_canal",
        target_structure="femur",
        entry_point_mm=(0.0, 0.0, 0.0),
        target_point_mm=(0.0, 0.0, 50.0),
        max_lateral_deviation_mm=2.0,
        max_angular_deviation_deg=3.0,
        min_confidence=0.8,
        max_uncertainty=0.2,
    )
    traj_trans = transform_trajectory(trans, traj)
    assert traj_trans.entry_point_mm == (100.0, 200.0, 300.0)
    assert traj_trans.target_point_mm == (100.0, 200.0, 350.0)
    assert traj_trans.max_lateral_deviation_mm == 2.0
