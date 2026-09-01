# -*- coding: utf-8 -*-
"""Unit Tests for TrackedInstrumentPose, Navigation Models, and Boundaries."""

from __future__ import annotations

import pytest

from holomed.navigation.constants import (
    DEFAULT_PATIENT_TRACKER_FRAME,
    MAX_TOOL_COORDINATE_MM,
)
from holomed.navigation.exceptions import NavigationValidationError
from holomed.navigation.models import (
    NavigationState,
    NavigationStatusRecord,
    PositionClass,
    TrackedInstrumentPose,
    TrajectoryDeviationRecord,
)


def test_tracked_instrument_pose_valid() -> None:
    """Verify valid TrackedInstrumentPose construction."""
    pose = TrackedInstrumentPose(
        instrument_id="femoral_probe_01",
        session_id="sess_01",
        epoch_id=1,
        sequence_number=100,
        tip_position_mm=(10.0, 20.0, 30.0),
        orientation_quaternion=(1.0, 0.0, 0.0, 0.0),
        coordinate_frame=DEFAULT_PATIENT_TRACKER_FRAME,
        confidence=0.95,
        uncertainty=0.05,
        timestamp_utc="2026-09-01T12:00:00Z",
    )
    assert pose.instrument_id == "femoral_probe_01"
    assert pose.tip_position_mm == (10.0, 20.0, 30.0)
    assert pose.sequence_number == 100


def test_tracked_instrument_pose_rejects_nan_and_inf() -> None:
    """Verify rejection of NaN and Inf in tip coordinates and quaternions."""
    # NaN coordinate
    with pytest.raises(NavigationValidationError):
        TrackedInstrumentPose(
            instrument_id="probe_01",
            session_id="s1",
            epoch_id=1,
            sequence_number=1,
            tip_position_mm=(float("nan"), 0.0, 0.0),
            orientation_quaternion=(1.0, 0.0, 0.0, 0.0),
            coordinate_frame=DEFAULT_PATIENT_TRACKER_FRAME,
            confidence=0.9,
            uncertainty=0.1,
            timestamp_utc="2026-09-01T12:00:00Z",
        )

    # Inf quaternion
    with pytest.raises(NavigationValidationError):
        TrackedInstrumentPose(
            instrument_id="probe_01",
            session_id="s1",
            epoch_id=1,
            sequence_number=1,
            tip_position_mm=(0.0, 0.0, 0.0),
            orientation_quaternion=(float("inf"), 0.0, 0.0, 0.0),
            coordinate_frame=DEFAULT_PATIENT_TRACKER_FRAME,
            confidence=0.9,
            uncertainty=0.1,
            timestamp_utc="2026-09-01T12:00:00Z",
        )


def test_tracked_instrument_pose_rejects_coordinate_bounds() -> None:
    """Verify coordinates exceeding MAX_TOOL_COORDINATE_MM (2000 mm) are rejected."""
    # Exactly +/- 2000 mm is accepted
    pose_edge = TrackedInstrumentPose(
        instrument_id="probe_01",
        session_id="s1",
        epoch_id=1,
        sequence_number=1,
        tip_position_mm=(MAX_TOOL_COORDINATE_MM, -MAX_TOOL_COORDINATE_MM, 0.0),
        orientation_quaternion=(1.0, 0.0, 0.0, 0.0),
        coordinate_frame=DEFAULT_PATIENT_TRACKER_FRAME,
        confidence=0.9,
        uncertainty=0.1,
        timestamp_utc="2026-09-01T12:00:00Z",
    )
    assert pose_edge.tip_position_mm[0] == 2000.0

    # 2000.1 mm is rejected
    with pytest.raises(NavigationValidationError) as exc:
        TrackedInstrumentPose(
            instrument_id="probe_01",
            session_id="s1",
            epoch_id=1,
            sequence_number=1,
            tip_position_mm=(2000.1, 0.0, 0.0),
            orientation_quaternion=(1.0, 0.0, 0.0, 0.0),
            coordinate_frame=DEFAULT_PATIENT_TRACKER_FRAME,
            confidence=0.9,
            uncertainty=0.1,
            timestamp_utc="2026-09-01T12:00:00Z",
        )
    assert "exceeds operational bound" in str(exc.value)


def test_quaternion_unit_norm_enforcement() -> None:
    """Verify non-unit-normalized quaternions are rejected."""
    # Norm^2 = 2.0 (unnormalized)
    with pytest.raises(NavigationValidationError) as exc:
        TrackedInstrumentPose(
            instrument_id="probe_01",
            session_id="s1",
            epoch_id=1,
            sequence_number=1,
            tip_position_mm=(0.0, 0.0, 0.0),
            orientation_quaternion=(1.0, 1.0, 0.0, 0.0),
            coordinate_frame=DEFAULT_PATIENT_TRACKER_FRAME,
            confidence=0.9,
            uncertainty=0.1,
            timestamp_utc="2026-09-01T12:00:00Z",
        )
    assert "unit-normalized" in str(exc.value)


def test_quaternion_canonical_hemisphere_qw_positive() -> None:
    """Verify negative qw quaternions are defensively flipped to canonical hemisphere qw >= 0."""
    pose = TrackedInstrumentPose(
        instrument_id="probe_01",
        session_id="s1",
        epoch_id=1,
        sequence_number=1,
        tip_position_mm=(0.0, 0.0, 0.0),
        orientation_quaternion=(-1.0, 0.0, 0.0, 0.0),
        coordinate_frame=DEFAULT_PATIENT_TRACKER_FRAME,
        confidence=0.9,
        uncertainty=0.1,
        timestamp_utc="2026-09-01T12:00:00Z",
    )
    assert pose.orientation_quaternion[0] == 1.0


def test_confidence_and_uncertainty_range_validation() -> None:
    """Verify confidence and uncertainty must be in [0.0, 1.0]."""
    with pytest.raises(NavigationValidationError):
        TrackedInstrumentPose(
            instrument_id="probe_01",
            session_id="s1",
            epoch_id=1,
            sequence_number=1,
            tip_position_mm=(0.0, 0.0, 0.0),
            orientation_quaternion=(1.0, 0.0, 0.0, 0.0),
            coordinate_frame=DEFAULT_PATIENT_TRACKER_FRAME,
            confidence=1.05,  # > 1.0
            uncertainty=0.1,
            timestamp_utc="2026-09-01T12:00:00Z",
        )

    with pytest.raises(NavigationValidationError):
        TrackedInstrumentPose(
            instrument_id="probe_01",
            session_id="s1",
            epoch_id=1,
            sequence_number=1,
            tip_position_mm=(0.0, 0.0, 0.0),
            orientation_quaternion=(1.0, 0.0, 0.0, 0.0),
            coordinate_frame=DEFAULT_PATIENT_TRACKER_FRAME,
            confidence=0.9,
            uncertainty=-0.05,  # < 0.0
            timestamp_utc="2026-09-01T12:00:00Z",
        )


def test_timestamp_iso8601_validation() -> None:
    """Verify invalid ISO-8601 timestamp string is rejected."""
    with pytest.raises(NavigationValidationError):
        TrackedInstrumentPose(
            instrument_id="probe_01",
            session_id="s1",
            epoch_id=1,
            sequence_number=1,
            tip_position_mm=(0.0, 0.0, 0.0),
            orientation_quaternion=(1.0, 0.0, 0.0, 0.0),
            coordinate_frame=DEFAULT_PATIENT_TRACKER_FRAME,
            confidence=0.9,
            uncertainty=0.1,
            timestamp_utc="not-a-timestamp",
        )
