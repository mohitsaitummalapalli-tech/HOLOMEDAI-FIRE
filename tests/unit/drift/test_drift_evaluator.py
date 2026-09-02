# -*- coding: utf-8 -*-
"""Unit Tests for M16 Drift Evaluator — Residuals, State Transitions & Interlocks."""

from __future__ import annotations

import math
from datetime import datetime, timezone, timedelta
from typing import List, Tuple

import pytest

from holomed.drift.constants import DEFAULT_PATIENT_TRACKER_FRAME
from holomed.drift.evaluator import DriftEvaluator
from holomed.drift.exceptions import (
    DriftEpochMismatchError,
    DriftValidationError,
)
from holomed.drift.models import DriftState, LandmarkObservation
from holomed.workflow.models import InterlockSeverity

from tests.unit.drift.conftest import (
    make_identity_transform,
    make_landmark_definition,
    make_landmark_observation,
)


def _make_dwell_history(
    t_current: datetime,
    pt: Tuple[float, float, float] = (100.0, 0.0, 0.0),
    sample_count: int = 2,
    duration_ms: float = 200.0,
    jitter_offset: float = 0.0,
) -> List[LandmarkObservation]:
    """Helper to create a dwell history buffer preceding t_current."""
    if sample_count == 0:
        return []
    history = []
    t_start = t_current - timedelta(milliseconds=duration_ms)
    step_ms = duration_ms / max(sample_count, 1)
    for i in range(sample_count):
        t_sample = t_start + timedelta(milliseconds=i * step_ms)
        offset = jitter_offset if i % 2 == 1 else 0.0
        sample_pt = (pt[0] + offset, pt[1], pt[2])
        history.append(
            make_landmark_observation(
                observed_point_mm=sample_pt,
                timestamp_utc=t_sample.isoformat(),
                sequence_number=i + 1,
            )
        )
    return history


# ===========================================================================
# Residual Evaluation & State Transitions
# ===========================================================================

class TestDriftEvaluatorStateTransitions:
    """Tests for DriftEvaluator.evaluate() state classifications."""

    def test_stable_state_when_residual_below_warning_and_dwell_satisfied(self) -> None:
        """Residual <= 1.0 mm AND dwell satisfied -> STABLE."""
        t_now = datetime(2025, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
        lm = make_landmark_definition(planned_point_mm=(100.0, 0.0, 0.0), max_tolerance_mm=2.0)
        obs = make_landmark_observation(
            observed_point_mm=(100.5, 0.0, 0.0),  # residual = 0.5 mm
            sequence_number=3,
            timestamp_utc=t_now.isoformat(),
        )
        transform = make_identity_transform()
        history = _make_dwell_history(t_now, pt=(100.5, 0.0, 0.0), sample_count=2, duration_ms=200.0)

        rec = DriftEvaluator.evaluate(obs, lm, transform, dwell_history=history, now_utc=t_now.isoformat())
        assert rec.state == DriftState.STABLE
        assert rec.passed is True
        assert math.isclose(rec.residual_error_mm, 0.5, abs_tol=1e-9)

    def test_warning_state_between_warning_and_effective_tolerance(self) -> None:
        """1.0 mm < residual <= effective_tolerance -> WARNING."""
        t_now = datetime(2025, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
        lm = make_landmark_definition(planned_point_mm=(100.0, 0.0, 0.0), max_tolerance_mm=2.0)
        obs = make_landmark_observation(
            observed_point_mm=(101.5, 0.0, 0.0),  # residual = 1.5 mm
            sequence_number=3,
            timestamp_utc=t_now.isoformat(),
        )
        transform = make_identity_transform()
        history = _make_dwell_history(t_now, pt=(101.5, 0.0, 0.0), sample_count=2, duration_ms=200.0)

        rec = DriftEvaluator.evaluate(obs, lm, transform, dwell_history=history, now_utc=t_now.isoformat())
        assert rec.state == DriftState.WARNING
        assert rec.passed is False
        assert math.isclose(rec.residual_error_mm, 1.5, abs_tol=1e-9)

    def test_drift_exceeded_state_when_residual_above_effective_tolerance(self) -> None:
        """Residual > effective_tolerance -> DRIFT_EXCEEDED."""
        t_now = datetime(2025, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
        lm = make_landmark_definition(planned_point_mm=(100.0, 0.0, 0.0), max_tolerance_mm=2.0)
        obs = make_landmark_observation(
            observed_point_mm=(102.5, 0.0, 0.0),  # residual = 2.5 mm
            sequence_number=3,
            timestamp_utc=t_now.isoformat(),
        )
        transform = make_identity_transform()
        history = _make_dwell_history(t_now, pt=(102.5, 0.0, 0.0), sample_count=2, duration_ms=200.0)

        rec = DriftEvaluator.evaluate(obs, lm, transform, dwell_history=history, now_utc=t_now.isoformat())
        assert rec.state == DriftState.DRIFT_EXCEEDED
        assert rec.passed is False
        assert math.isclose(rec.residual_error_mm, 2.5, abs_tol=1e-9)

    def test_tighter_landmark_tolerance_enforced(self) -> None:
        """When landmark tolerance = 1.2 mm, residual = 1.5 mm -> DRIFT_EXCEEDED."""
        t_now = datetime(2025, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
        lm = make_landmark_definition(planned_point_mm=(100.0, 0.0, 0.0), max_tolerance_mm=1.2)
        obs = make_landmark_observation(
            observed_point_mm=(101.5, 0.0, 0.0),  # residual = 1.5 mm > 1.2 mm
            sequence_number=3,
            timestamp_utc=t_now.isoformat(),
        )
        transform = make_identity_transform()
        history = _make_dwell_history(t_now, pt=(101.5, 0.0, 0.0), sample_count=2, duration_ms=200.0)

        rec = DriftEvaluator.evaluate(obs, lm, transform, dwell_history=history, now_utc=t_now.isoformat())
        assert rec.state == DriftState.DRIFT_EXCEEDED
        assert rec.passed is False
        assert math.isclose(rec.effective_tolerance_mm, 1.2, abs_tol=1e-9)


# ===========================================================================
# Confidence & Uncertainty Gating
# ===========================================================================

class TestDriftEvaluatorGating:
    """Tests for confidence and uncertainty gating."""

    def test_low_confidence_prevents_stable_state(self) -> None:
        """Low confidence fails gating even if residual is 0.0 mm and dwell is satisfied."""
        t_now = datetime(2025, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
        lm = make_landmark_definition(planned_point_mm=(100.0, 0.0, 0.0), min_confidence=0.85)
        obs = make_landmark_observation(
            observed_point_mm=(100.0, 0.0, 0.0), confidence=0.70,
            sequence_number=3, timestamp_utc=t_now.isoformat(),
        )
        transform = make_identity_transform()
        history = _make_dwell_history(t_now, pt=(100.0, 0.0, 0.0), sample_count=2, duration_ms=200.0)

        rec = DriftEvaluator.evaluate(obs, lm, transform, dwell_history=history, now_utc=t_now.isoformat())
        assert rec.state == DriftState.WARNING
        assert rec.passed is False

    def test_high_uncertainty_prevents_stable_state(self) -> None:
        """Excessive uncertainty fails gating even if residual is 0.0 mm."""
        t_now = datetime(2025, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
        lm = make_landmark_definition(planned_point_mm=(100.0, 0.0, 0.0), max_uncertainty_mm=1.5)
        obs = make_landmark_observation(
            observed_point_mm=(100.0, 0.0, 0.0), uncertainty_mm=2.5,
            sequence_number=3, timestamp_utc=t_now.isoformat(),
        )
        transform = make_identity_transform()
        history = _make_dwell_history(t_now, pt=(100.0, 0.0, 0.0), sample_count=2, duration_ms=200.0)

        rec = DriftEvaluator.evaluate(obs, lm, transform, dwell_history=history, now_utc=t_now.isoformat())
        assert rec.state == DriftState.WARNING
        assert rec.passed is False


# ===========================================================================
# Temporal & Freshness Checks
# ===========================================================================

class TestDriftEvaluatorTemporalChecks:
    """Tests for timestamp age, future skew, and epoch validation."""

    def test_stale_observation_rejected(self) -> None:
        now = datetime(2025, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
        stale_time = now - timedelta(milliseconds=600)  # > 500 ms

        lm = make_landmark_definition()
        obs = make_landmark_observation(timestamp_utc=stale_time.isoformat())
        transform = make_identity_transform()

        with pytest.raises(DriftValidationError, match="stale"):
            DriftEvaluator.evaluate(obs, lm, transform, now_utc=now.isoformat())

    def test_future_skew_rejected(self) -> None:
        now = datetime(2025, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
        future_time = now + timedelta(milliseconds=100)  # > 50 ms future skew

        lm = make_landmark_definition()
        obs = make_landmark_observation(timestamp_utc=future_time.isoformat())
        transform = make_identity_transform()

        with pytest.raises(DriftValidationError, match="future-dated"):
            DriftEvaluator.evaluate(obs, lm, transform, now_utc=now.isoformat())

    def test_epoch_mismatch_rejected(self) -> None:
        lm = make_landmark_definition()
        obs = make_landmark_observation(epoch_id=2)
        transform = make_identity_transform(epoch_id=1)

        with pytest.raises(DriftEpochMismatchError, match="Observation epoch"):
            DriftEvaluator.evaluate(obs, lm, transform)

    def test_wrong_coordinate_frame_rejected(self) -> None:
        lm = make_landmark_definition()
        obs = make_landmark_observation(coordinate_frame="wrong_frame")
        transform = make_identity_transform()

        with pytest.raises(DriftValidationError, match="coordinate_frame"):
            DriftEvaluator.evaluate(obs, lm, transform)


# ===========================================================================
# Strict Dwell Stability Matrix (N=0, 1, 2, 3)
# ===========================================================================

class TestDriftEvaluatorDwellMatrix:
    """Comprehensive test matrix for dwell stability rules."""

    def test_n0_samples_fails_dwell(self) -> None:
        """N=0 history samples (total=1 < 3) -> dwell not satisfied -> cannot be STABLE."""
        t_now = datetime(2025, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
        lm = make_landmark_definition(planned_point_mm=(100.0, 0.0, 0.0))
        obs = make_landmark_observation(observed_point_mm=(100.2, 0.0, 0.0), timestamp_utc=t_now.isoformat())
        transform = make_identity_transform()

        rec = DriftEvaluator.evaluate(obs, lm, transform, dwell_history=[], now_utc=t_now.isoformat())
        assert rec.state == DriftState.WARNING
        assert rec.passed is False

    def test_n1_samples_fails_dwell(self) -> None:
        """N=1 history sample (total=2 < 3) -> dwell not satisfied -> cannot be STABLE."""
        t_now = datetime(2025, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
        lm = make_landmark_definition(planned_point_mm=(100.0, 0.0, 0.0))
        h = _make_dwell_history(t_now, pt=(100.2, 0.0, 0.0), sample_count=1, duration_ms=100.0)
        obs = make_landmark_observation(observed_point_mm=(100.2, 0.0, 0.0), timestamp_utc=t_now.isoformat())
        transform = make_identity_transform()

        rec = DriftEvaluator.evaluate(obs, lm, transform, dwell_history=h, now_utc=t_now.isoformat())
        assert rec.state == DriftState.WARNING
        assert rec.passed is False

    def test_n2_samples_with_short_duration_fails_dwell(self) -> None:
        """N=2 history samples (total=3), duration < 150 ms -> dwell not satisfied."""
        t_now = datetime(2025, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
        lm = make_landmark_definition(planned_point_mm=(100.0, 0.0, 0.0))
        h = _make_dwell_history(t_now, pt=(100.2, 0.0, 0.0), sample_count=2, duration_ms=80.0)  # < 150 ms
        obs = make_landmark_observation(observed_point_mm=(100.2, 0.0, 0.0), timestamp_utc=t_now.isoformat())
        transform = make_identity_transform()

        rec = DriftEvaluator.evaluate(obs, lm, transform, dwell_history=h, now_utc=t_now.isoformat())
        assert rec.state == DriftState.WARNING
        assert rec.passed is False

    def test_n2_samples_with_excessive_jitter_fails_dwell(self) -> None:
        """N=2 history samples (total=3), duration >= 150 ms but jitter > 0.5 mm -> fails."""
        t_now = datetime(2025, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
        lm = make_landmark_definition(planned_point_mm=(100.0, 0.0, 0.0))
        h = _make_dwell_history(t_now, pt=(100.2, 0.0, 0.0), sample_count=2, duration_ms=200.0, jitter_offset=1.2)  # Jitter > 0.5 mm
        obs = make_landmark_observation(observed_point_mm=(100.2, 0.0, 0.0), timestamp_utc=t_now.isoformat())
        transform = make_identity_transform()

        rec = DriftEvaluator.evaluate(obs, lm, transform, dwell_history=h, now_utc=t_now.isoformat())
        assert rec.state == DriftState.WARNING
        assert rec.passed is False

    def test_n2_samples_with_valid_duration_and_low_jitter_satisfies_dwell(self) -> None:
        """N=2 history samples (total=3), duration >= 150 ms AND jitter <= 0.5 mm -> STABLE."""
        t_now = datetime(2025, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
        lm = make_landmark_definition(planned_point_mm=(100.0, 0.0, 0.0))
        h = _make_dwell_history(t_now, pt=(100.2, 0.0, 0.0), sample_count=2, duration_ms=180.0, jitter_offset=0.2)  # Duration >= 150, Jitter <= 0.5
        obs = make_landmark_observation(observed_point_mm=(100.2, 0.0, 0.0), timestamp_utc=t_now.isoformat())
        transform = make_identity_transform()

        rec = DriftEvaluator.evaluate(obs, lm, transform, dwell_history=h, now_utc=t_now.isoformat())
        assert rec.state == DriftState.STABLE
        assert rec.passed is True


# ===========================================================================
# Safety Interlock Generation
# ===========================================================================

class TestDriftInterlockGeneration:
    """Tests for M10 SafetyInterlock generation."""

    def test_no_interlock_when_stable(self) -> None:
        t_now = datetime(2025, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
        lm = make_landmark_definition(planned_point_mm=(100.0, 0.0, 0.0))
        obs = make_landmark_observation(observed_point_mm=(100.2, 0.0, 0.0), timestamp_utc=t_now.isoformat())
        transform = make_identity_transform()
        history = _make_dwell_history(t_now, pt=(100.2, 0.0, 0.0), sample_count=2, duration_ms=200.0)

        rec = DriftEvaluator.evaluate(obs, lm, transform, dwell_history=history, now_utc=t_now.isoformat())
        interlock = DriftEvaluator.generate_interlock(rec, "session-01", 1)
        assert interlock is None

    def test_blocking_interlock_on_drift_exceeded(self) -> None:
        lm = make_landmark_definition(planned_point_mm=(100.0, 0.0, 0.0), max_tolerance_mm=2.0)
        obs = make_landmark_observation(observed_point_mm=(103.0, 0.0, 0.0))
        transform = make_identity_transform()

        rec = DriftEvaluator.evaluate(obs, lm, transform)
        interlock = DriftEvaluator.generate_interlock(rec, "session-01", 1)
        assert interlock is not None
        assert interlock.status is False  # Tripped
        assert interlock.severity == InterlockSeverity.BLOCKING
        assert interlock.condition_name == "REGISTRATION_DRIFT_EXCEEDED"
        assert interlock.source_service == "drift_service"
