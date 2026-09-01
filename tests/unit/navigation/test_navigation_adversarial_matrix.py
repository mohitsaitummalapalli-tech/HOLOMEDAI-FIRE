# -*- coding: utf-8 -*-
"""Adversarial Matrix and Strict Boundary Tests for M14 Navigation."""

from __future__ import annotations

import math
import pytest

from holomed.core.dispatcher import MessageDispatcher
from holomed.navigation.constants import (
    DEFAULT_PATIENT_TRACKER_FRAME,
    MAX_ALLOWED_POSE_AGE_MS,
    MAX_FUTURE_TIMESTAMP_SKEW_MS,
)
from holomed.navigation.evaluator import TrajectoryDeviationEvaluator
from holomed.navigation.exceptions import NavigationValidationError
from holomed.navigation.models import (
    NavigationState,
    PositionClass,
    TrackedInstrumentPose,
)
from holomed.planning.models import TrajectoryPlan


def test_adversarial_quaternion_normalization_boundary() -> None:
    """Verify quaternion with norm^2 deviation within 1e-4 is accepted, exceeding is rejected."""
    # norm^2 = 1.0 + 0.00009 <= 1e-4 -> accepted
    qw_acc = math.sqrt(1.00009)
    p_acc = TrackedInstrumentPose(
        instrument_id="p1",
        session_id="s1",
        epoch_id=1,
        sequence_number=1,
        tip_position_mm=(0.0, 0.0, 0.0),
        orientation_quaternion=(qw_acc, 0.0, 0.0, 0.0),
        coordinate_frame=DEFAULT_PATIENT_TRACKER_FRAME,
        confidence=0.9,
        uncertainty=0.1,
        timestamp_utc="2026-09-01T12:00:00Z",
    )
    assert p_acc.sequence_number == 1

    # norm^2 = 1.0 + 0.00011 > 1e-4 -> rejected
    qw_rej = math.sqrt(1.00011)
    with pytest.raises(NavigationValidationError):
        TrackedInstrumentPose(
            instrument_id="p1",
            session_id="s1",
            epoch_id=1,
            sequence_number=1,
            tip_position_mm=(0.0, 0.0, 0.0),
            orientation_quaternion=(qw_rej, 0.0, 0.0, 0.0),
            coordinate_frame=DEFAULT_PATIENT_TRACKER_FRAME,
            confidence=0.9,
            uncertainty=0.1,
            timestamp_utc="2026-09-01T12:00:00Z",
        )


def test_adversarial_pose_age_exact_boundary(sample_plan_trajectory: TrajectoryPlan) -> None:
    """Verify exact 200.0 ms age is accepted, while 200.001 ms is STALE."""
    pose = TrackedInstrumentPose(
        instrument_id="p1",
        session_id="s1",
        epoch_id=1,
        sequence_number=1,
        tip_position_mm=(0.0, 0.0, 50.0),
        orientation_quaternion=(1.0, 0.0, 0.0, 0.0),
        coordinate_frame=DEFAULT_PATIENT_TRACKER_FRAME,
        confidence=0.9,
        uncertainty=0.1,
        timestamp_utc="2026-09-01T12:00:00.000Z",
    )
    # 200.0 ms age -> ALIGNED
    dev_200 = TrajectoryDeviationEvaluator.evaluate(
        pose, sample_plan_trajectory, now_utc="2026-09-01T12:00:00.200Z"
    )
    assert dev_200.state == NavigationState.ALIGNED
    assert dev_200.is_safe is True

    # 200.001 ms age -> STALE
    dev_200_plus = TrajectoryDeviationEvaluator.evaluate(
        pose, sample_plan_trajectory, now_utc="2026-09-01T12:00:00.201Z"
    )
    assert dev_200_plus.state == NavigationState.STALE
    assert dev_200_plus.is_safe is False


def test_adversarial_future_skew_exact_boundary(sample_plan_trajectory: TrajectoryPlan) -> None:
    """Verify exact -50.0 ms skew is accepted, while -50.001 ms is STALE/faulted."""
    pose = TrackedInstrumentPose(
        instrument_id="p1",
        session_id="s1",
        epoch_id=1,
        sequence_number=1,
        tip_position_mm=(0.0, 0.0, 50.0),
        orientation_quaternion=(1.0, 0.0, 0.0, 0.0),
        coordinate_frame=DEFAULT_PATIENT_TRACKER_FRAME,
        confidence=0.9,
        uncertainty=0.1,
        timestamp_utc="2026-09-01T12:00:00.050Z",  # 50 ms in future
    )
    dev_50 = TrajectoryDeviationEvaluator.evaluate(
        pose, sample_plan_trajectory, now_utc="2026-09-01T12:00:00.000Z"
    )
    assert dev_50.state == NavigationState.ALIGNED

    # 51 ms in future -> STALE
    pose_future = TrackedInstrumentPose(
        instrument_id="p1",
        session_id="s1",
        epoch_id=1,
        sequence_number=1,
        tip_position_mm=(0.0, 0.0, 50.0),
        orientation_quaternion=(1.0, 0.0, 0.0, 0.0),
        coordinate_frame=DEFAULT_PATIENT_TRACKER_FRAME,
        confidence=0.9,
        uncertainty=0.1,
        timestamp_utc="2026-09-01T12:00:00.051Z",
    )
    dev_future = TrajectoryDeviationEvaluator.evaluate(
        pose_future, sample_plan_trajectory, now_utc="2026-09-01T12:00:00.000Z"
    )
    assert dev_future.state == NavigationState.STALE


def test_adversarial_axial_depth_exact_entry_and_target_boundaries(
    sample_plan_trajectory: TrajectoryPlan,
) -> None:
    """Verify axial distance exactly at entry (s=0.0) and target (s=L=100.0) is IN_TRAJECTORY."""
    # Entry point (s = 0.0)
    p_entry = TrackedInstrumentPose(
        instrument_id="p1",
        session_id="s1",
        epoch_id=1,
        sequence_number=1,
        tip_position_mm=(0.0, 0.0, 0.0),
        orientation_quaternion=(1.0, 0.0, 0.0, 0.0),
        coordinate_frame=DEFAULT_PATIENT_TRACKER_FRAME,
        confidence=0.9,
        uncertainty=0.1,
        timestamp_utc="2026-09-01T12:00:00.000Z",
    )
    dev_entry = TrajectoryDeviationEvaluator.evaluate(
        p_entry, sample_plan_trajectory, now_utc="2026-09-01T12:00:00.010Z"
    )
    assert dev_entry.position_class == PositionClass.IN_TRAJECTORY
    assert dev_entry.state == NavigationState.ALIGNED

    # Target point (s = 100.0)
    p_target = TrackedInstrumentPose(
        instrument_id="p1",
        session_id="s1",
        epoch_id=1,
        sequence_number=1,
        tip_position_mm=(0.0, 0.0, 100.0),
        orientation_quaternion=(1.0, 0.0, 0.0, 0.0),
        coordinate_frame=DEFAULT_PATIENT_TRACKER_FRAME,
        confidence=0.9,
        uncertainty=0.1,
        timestamp_utc="2026-09-01T12:00:00.000Z",
    )
    dev_target = TrajectoryDeviationEvaluator.evaluate(
        p_target, sample_plan_trajectory, now_utc="2026-09-01T12:00:00.010Z"
    )
    assert dev_target.position_class == PositionClass.IN_TRAJECTORY
    assert dev_target.state == NavigationState.ALIGNED


def test_adversarial_lateral_and_angular_exact_tolerance_boundaries(
    sample_plan_trajectory: TrajectoryPlan,
) -> None:
    """Verify lateral deviation exactly at tolerance (2.0 mm) passes, 2.01 mm fails."""
    # Exact 2.0 mm lateral deviation
    p_exact = TrackedInstrumentPose(
        instrument_id="p1",
        session_id="s1",
        epoch_id=1,
        sequence_number=1,
        tip_position_mm=(2.0, 0.0, 50.0),
        orientation_quaternion=(1.0, 0.0, 0.0, 0.0),
        coordinate_frame=DEFAULT_PATIENT_TRACKER_FRAME,
        confidence=0.9,
        uncertainty=0.1,
        timestamp_utc="2026-09-01T12:00:00.000Z",
    )
    dev_exact = TrajectoryDeviationEvaluator.evaluate(
        p_exact, sample_plan_trajectory, now_utc="2026-09-01T12:00:00.010Z"
    )
    assert dev_exact.state == NavigationState.ALIGNED

    # 2.01 mm lateral deviation
    p_over = TrackedInstrumentPose(
        instrument_id="p1",
        session_id="s1",
        epoch_id=1,
        sequence_number=1,
        tip_position_mm=(2.01, 0.0, 50.0),
        orientation_quaternion=(1.0, 0.0, 0.0, 0.0),
        coordinate_frame=DEFAULT_PATIENT_TRACKER_FRAME,
        confidence=0.9,
        uncertainty=0.1,
        timestamp_utc="2026-09-01T12:00:00.000Z",
    )
    dev_over = TrajectoryDeviationEvaluator.evaluate(
        p_over, sample_plan_trajectory, now_utc="2026-09-01T12:00:00.010Z"
    )
    assert dev_over.state == NavigationState.DEVIATED


def test_adversarial_confidence_and_uncertainty_exact_boundaries(
    sample_plan_trajectory: TrajectoryPlan,
) -> None:
    """Verify exact min_confidence (0.80) and max_uncertainty (0.20) pass, slight violation triggers interlock."""
    # Exact confidence 0.80 and uncertainty 0.20
    p_exact = TrackedInstrumentPose(
        instrument_id="p1",
        session_id="s1",
        epoch_id=1,
        sequence_number=1,
        tip_position_mm=(0.0, 0.0, 50.0),
        orientation_quaternion=(1.0, 0.0, 0.0, 0.0),
        coordinate_frame=DEFAULT_PATIENT_TRACKER_FRAME,
        confidence=0.80,
        uncertainty=0.20,
        timestamp_utc="2026-09-01T12:00:00.000Z",
    )
    dev_exact = TrajectoryDeviationEvaluator.evaluate(
        p_exact, sample_plan_trajectory, now_utc="2026-09-01T12:00:00.010Z"
    )
    assert dev_exact.state == NavigationState.ALIGNED

    # 0.799 confidence -> INTERLOCKED
    p_bad_conf = TrackedInstrumentPose(
        instrument_id="p1",
        session_id="s1",
        epoch_id=1,
        sequence_number=1,
        tip_position_mm=(0.0, 0.0, 50.0),
        orientation_quaternion=(1.0, 0.0, 0.0, 0.0),
        coordinate_frame=DEFAULT_PATIENT_TRACKER_FRAME,
        confidence=0.799,
        uncertainty=0.20,
        timestamp_utc="2026-09-01T12:00:00.000Z",
    )
    dev_bad_conf = TrajectoryDeviationEvaluator.evaluate(
        p_bad_conf, sample_plan_trajectory, now_utc="2026-09-01T12:00:00.010Z"
    )
    assert dev_bad_conf.state == NavigationState.INTERLOCKED
