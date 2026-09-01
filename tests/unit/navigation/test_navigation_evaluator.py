# -*- coding: utf-8 -*-
"""Unit Tests for TrajectoryDeviationEvaluator and Safety Synthesis."""

from __future__ import annotations

import pytest

from holomed.navigation.constants import (
    DEFAULT_PATIENT_TRACKER_FRAME,
    MAX_ALLOWED_POSE_AGE_MS,
    MAX_FUTURE_TIMESTAMP_SKEW_MS,
)
from holomed.navigation.evaluator import TrajectoryDeviationEvaluator
from holomed.navigation.exceptions import (
    NavigationFrameMismatchError,
    NavigationStalenessError,
)
from holomed.navigation.models import (
    NavigationState,
    PositionClass,
    TrackedInstrumentPose,
)
from holomed.planning.models import TrajectoryPlan
from holomed.workflow.models import InterlockSeverity


def test_evaluator_aligned_in_trajectory(sample_plan_trajectory: TrajectoryPlan) -> None:
    """Verify in-trajectory aligned tool produces ALIGNED state and INFO interlock."""
    # Tool tip on trajectory axis at z=50, oriented along +Z
    pose = TrackedInstrumentPose(
        instrument_id="probe_01",
        session_id="s1",
        epoch_id=1,
        sequence_number=1,
        tip_position_mm=(0.0, 0.0, 50.0),
        orientation_quaternion=(1.0, 0.0, 0.0, 0.0),  # +Z
        coordinate_frame=DEFAULT_PATIENT_TRACKER_FRAME,
        confidence=0.95,
        uncertainty=0.05,
        timestamp_utc="2026-09-01T12:00:00.000Z",
    )
    dev = TrajectoryDeviationEvaluator.evaluate(
        pose, sample_plan_trajectory, now_utc="2026-09-01T12:00:00.050Z"  # 50ms age
    )
    assert dev.state == NavigationState.ALIGNED
    assert dev.position_class == PositionClass.IN_TRAJECTORY
    assert dev.is_aligned is True
    assert dev.is_safe is True
    assert dev.safety_interlock is not None
    assert dev.safety_interlock.severity == InterlockSeverity.INFO


def test_evaluator_pre_entry_approach(sample_plan_trajectory: TrajectoryPlan) -> None:
    """Verify tool before entry (z=-20) is classified as PRE_ENTRY with safe=True, is_aligned=False."""
    pose = TrackedInstrumentPose(
        instrument_id="probe_01",
        session_id="s1",
        epoch_id=1,
        sequence_number=1,
        tip_position_mm=(0.0, 0.0, -20.0),
        orientation_quaternion=(1.0, 0.0, 0.0, 0.0),
        coordinate_frame=DEFAULT_PATIENT_TRACKER_FRAME,
        confidence=0.95,
        uncertainty=0.05,
        timestamp_utc="2026-09-01T12:00:00.000Z",
    )
    dev = TrajectoryDeviationEvaluator.evaluate(
        pose, sample_plan_trajectory, now_utc="2026-09-01T12:00:00.050Z"
    )
    assert dev.state == NavigationState.PRE_ENTRY
    assert dev.position_class == PositionClass.PRE_ENTRY
    assert dev.is_aligned is False
    assert dev.is_safe is True
    assert dev.axial_distance_mm == -20.0


def test_evaluator_post_target_overextension(sample_plan_trajectory: TrajectoryPlan) -> None:
    """Verify tool past target (z=110 > 100) triggers DEVIATED with BLOCKING interlock."""
    pose = TrackedInstrumentPose(
        instrument_id="probe_01",
        session_id="s1",
        epoch_id=1,
        sequence_number=1,
        tip_position_mm=(0.0, 0.0, 110.0),
        orientation_quaternion=(1.0, 0.0, 0.0, 0.0),
        coordinate_frame=DEFAULT_PATIENT_TRACKER_FRAME,
        confidence=0.95,
        uncertainty=0.05,
        timestamp_utc="2026-09-01T12:00:00.000Z",
    )
    dev = TrajectoryDeviationEvaluator.evaluate(
        pose, sample_plan_trajectory, now_utc="2026-09-01T12:00:00.050Z"
    )
    assert dev.state == NavigationState.DEVIATED
    assert dev.position_class == PositionClass.POST_TARGET
    assert dev.is_aligned is False
    assert dev.is_safe is False
    assert dev.safety_interlock.severity == InterlockSeverity.BLOCKING


def test_evaluator_lateral_tolerance_exceeded(sample_plan_trajectory: TrajectoryPlan) -> None:
    """Verify lateral deviation > 2.0 mm triggers DEVIATED state."""
    # Offset by 3.0 mm in X (tolerance is 2.0 mm)
    pose = TrackedInstrumentPose(
        instrument_id="probe_01",
        session_id="s1",
        epoch_id=1,
        sequence_number=1,
        tip_position_mm=(3.0, 0.0, 50.0),
        orientation_quaternion=(1.0, 0.0, 0.0, 0.0),
        coordinate_frame=DEFAULT_PATIENT_TRACKER_FRAME,
        confidence=0.95,
        uncertainty=0.05,
        timestamp_utc="2026-09-01T12:00:00.000Z",
    )
    dev = TrajectoryDeviationEvaluator.evaluate(
        pose, sample_plan_trajectory, now_utc="2026-09-01T12:00:00.050Z"
    )
    assert dev.state == NavigationState.DEVIATED
    assert dev.lateral_deviation_mm == 3.0
    assert dev.is_aligned is False
    assert dev.is_safe is False


def test_evaluator_angular_tolerance_exceeded(sample_plan_trajectory: TrajectoryPlan) -> None:
    """Verify angular deviation > 5.0 deg triggers DEVIATED state."""
    # Tool tilted 10 degrees around X (tolerance is 5.0 deg)
    import math
    angle = math.radians(5.0)  # half angle = 5 deg -> total rot = 10 deg
    q_tilted = (math.cos(angle), math.sin(angle), 0.0, 0.0)

    pose = TrackedInstrumentPose(
        instrument_id="probe_01",
        session_id="s1",
        epoch_id=1,
        sequence_number=1,
        tip_position_mm=(0.0, 0.0, 50.0),
        orientation_quaternion=q_tilted,
        coordinate_frame=DEFAULT_PATIENT_TRACKER_FRAME,
        confidence=0.95,
        uncertainty=0.05,
        timestamp_utc="2026-09-01T12:00:00.000Z",
    )
    dev = TrajectoryDeviationEvaluator.evaluate(
        pose, sample_plan_trajectory, now_utc="2026-09-01T12:00:00.050Z"
    )
    assert dev.state == NavigationState.DEVIATED
    assert pytest.approx(dev.angular_deviation_deg, abs=1e-1) == 10.0
    assert dev.is_aligned is False
    assert dev.is_safe is False


def test_evaluator_stale_pose_exceeding_200ms(sample_plan_trajectory: TrajectoryPlan) -> None:
    """Verify pose age > 200 ms triggers STALE state."""
    pose = TrackedInstrumentPose(
        instrument_id="probe_01",
        session_id="s1",
        epoch_id=1,
        sequence_number=1,
        tip_position_mm=(0.0, 0.0, 50.0),
        orientation_quaternion=(1.0, 0.0, 0.0, 0.0),
        coordinate_frame=DEFAULT_PATIENT_TRACKER_FRAME,
        confidence=0.95,
        uncertainty=0.05,
        timestamp_utc="2026-09-01T12:00:00.000Z",
    )
    # Evaluated at 201 ms later
    dev = TrajectoryDeviationEvaluator.evaluate(
        pose, sample_plan_trajectory, now_utc="2026-09-01T12:00:00.201Z"
    )
    assert dev.state == NavigationState.STALE
    assert dev.is_safe is False
    assert dev.safety_interlock.condition_name == "STALE_POSE_DATA"


def test_evaluator_future_timestamp_skew_rejected(sample_plan_trajectory: TrajectoryPlan) -> None:
    """Verify future timestamp exceeding 50 ms skew triggers STALE/faulted state."""
    pose = TrackedInstrumentPose(
        instrument_id="probe_01",
        session_id="s1",
        epoch_id=1,
        sequence_number=1,
        tip_position_mm=(0.0, 0.0, 50.0),
        orientation_quaternion=(1.0, 0.0, 0.0, 0.0),
        coordinate_frame=DEFAULT_PATIENT_TRACKER_FRAME,
        confidence=0.95,
        uncertainty=0.05,
        timestamp_utc="2026-09-01T12:00:01.000Z",  # 1 second in the future
    )
    dev = TrajectoryDeviationEvaluator.evaluate(
        pose, sample_plan_trajectory, now_utc="2026-09-01T12:00:00.000Z"
    )
    assert dev.state == NavigationState.STALE
    assert dev.is_safe is False


def test_evaluator_insufficient_confidence_triggers_interlock(sample_plan_trajectory: TrajectoryPlan) -> None:
    """Verify confidence below min_confidence (0.80) triggers INTERLOCKED state."""
    pose = TrackedInstrumentPose(
        instrument_id="probe_01",
        session_id="s1",
        epoch_id=1,
        sequence_number=1,
        tip_position_mm=(0.0, 0.0, 50.0),
        orientation_quaternion=(1.0, 0.0, 0.0, 0.0),
        coordinate_frame=DEFAULT_PATIENT_TRACKER_FRAME,
        confidence=0.75,  # min is 0.80
        uncertainty=0.05,
        timestamp_utc="2026-09-01T12:00:00.000Z",
    )
    dev = TrajectoryDeviationEvaluator.evaluate(
        pose, sample_plan_trajectory, now_utc="2026-09-01T12:00:00.050Z"
    )
    assert dev.state == NavigationState.INTERLOCKED
    assert dev.safety_interlock.condition_name == "INSUFFICIENT_CONFIDENCE"


def test_evaluator_excessive_uncertainty_triggers_interlock(sample_plan_trajectory: TrajectoryPlan) -> None:
    """Verify uncertainty above max_uncertainty (0.20) triggers INTERLOCKED state."""
    pose = TrackedInstrumentPose(
        instrument_id="probe_01",
        session_id="s1",
        epoch_id=1,
        sequence_number=1,
        tip_position_mm=(0.0, 0.0, 50.0),
        orientation_quaternion=(1.0, 0.0, 0.0, 0.0),
        coordinate_frame=DEFAULT_PATIENT_TRACKER_FRAME,
        confidence=0.95,
        uncertainty=0.25,  # max is 0.20
        timestamp_utc="2026-09-01T12:00:00.000Z",
    )
    dev = TrajectoryDeviationEvaluator.evaluate(
        pose, sample_plan_trajectory, now_utc="2026-09-01T12:00:00.050Z"
    )
    assert dev.state == NavigationState.INTERLOCKED
    assert dev.safety_interlock.condition_name == "EXCESSIVE_UNCERTAINTY"


def test_evaluator_coordinate_frame_mismatch(sample_plan_trajectory: TrajectoryPlan) -> None:
    """Verify pose in non-patient-tracker coordinate frame raises NavigationFrameMismatchError."""
    pose = TrackedInstrumentPose(
        instrument_id="probe_01",
        session_id="s1",
        epoch_id=1,
        sequence_number=1,
        tip_position_mm=(0.0, 0.0, 50.0),
        orientation_quaternion=(1.0, 0.0, 0.0, 0.0),
        coordinate_frame="camera_optical_frame",  # Wrong frame!
        confidence=0.95,
        uncertainty=0.05,
        timestamp_utc="2026-09-01T12:00:00.000Z",
    )
    with pytest.raises(NavigationFrameMismatchError):
        TrajectoryDeviationEvaluator.evaluate(pose, sample_plan_trajectory)
