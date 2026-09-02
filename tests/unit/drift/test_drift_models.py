# -*- coding: utf-8 -*-
"""Unit Tests for M16 Drift Models — LandmarkDefinition, LandmarkObservation, LandmarkVerificationRecord."""

from __future__ import annotations

import math
from datetime import datetime, timezone

import pytest

from holomed.drift.constants import (
    MAX_LANDMARK_TOLERANCE_MM,
    MAX_POINT_COORDINATE_MM,
)
from holomed.drift.exceptions import DriftValidationError
from holomed.drift.models import (
    DriftState,
    DriftStatusRecord,
    LandmarkDefinition,
    LandmarkObservation,
    LandmarkVerificationRecord,
)

from tests.unit.drift.conftest import (
    make_landmark_definition,
    make_landmark_observation,
)


# ===========================================================================
# DriftState Enum
# ===========================================================================

class TestDriftState:
    """Tests for DriftState enum."""

    def test_all_states_exist(self) -> None:
        assert DriftState.UNINITIALIZED.value == "UNINITIALIZED"
        assert DriftState.READY.value == "READY"
        assert DriftState.STABLE.value == "STABLE"
        assert DriftState.WARNING.value == "WARNING"
        assert DriftState.DRIFT_EXCEEDED.value == "DRIFT_EXCEEDED"
        assert DriftState.INTERLOCKED.value == "INTERLOCKED"

    def test_state_count(self) -> None:
        assert len(DriftState) == 6


# ===========================================================================
# LandmarkDefinition Validation
# ===========================================================================

class TestLandmarkDefinition:
    """Tests for LandmarkDefinition validation."""

    def test_valid_construction(self) -> None:
        lm = make_landmark_definition()
        assert lm.landmark_id == "lm-nasion"
        assert lm.max_tolerance_mm == 2.0

    def test_immutable(self) -> None:
        lm = make_landmark_definition()
        with pytest.raises(AttributeError):
            lm.max_tolerance_mm = 1.0  # type: ignore

    def test_invalid_landmark_id(self) -> None:
        with pytest.raises(DriftValidationError, match="landmark_id"):
            make_landmark_definition(landmark_id="")

    def test_empty_name(self) -> None:
        with pytest.raises(DriftValidationError, match="name"):
            make_landmark_definition(name="")

    def test_nan_planned_point(self) -> None:
        with pytest.raises(DriftValidationError, match="planned_point_mm"):
            make_landmark_definition(planned_point_mm=(float("nan"), 0.0, 0.0))

    def test_inf_planned_point(self) -> None:
        with pytest.raises(DriftValidationError, match="planned_point_mm"):
            make_landmark_definition(planned_point_mm=(float("inf"), 0.0, 0.0))

    def test_planned_point_exceeds_bound(self) -> None:
        with pytest.raises(DriftValidationError, match="operational bound"):
            make_landmark_definition(planned_point_mm=(MAX_POINT_COORDINATE_MM + 1.0, 0.0, 0.0))

    def test_tolerance_zero_rejected(self) -> None:
        with pytest.raises(DriftValidationError, match="max_tolerance_mm"):
            make_landmark_definition(max_tolerance_mm=0.0)

    def test_tolerance_negative_rejected(self) -> None:
        with pytest.raises(DriftValidationError, match="max_tolerance_mm"):
            make_landmark_definition(max_tolerance_mm=-1.0)

    def test_tolerance_exceeding_global_ceiling_rejected(self) -> None:
        with pytest.raises(DriftValidationError, match="max_tolerance_mm"):
            make_landmark_definition(max_tolerance_mm=MAX_LANDMARK_TOLERANCE_MM + 0.1)

    def test_min_confidence_out_of_range(self) -> None:
        with pytest.raises(DriftValidationError, match="min_confidence"):
            make_landmark_definition(min_confidence=1.5)

    def test_max_uncertainty_negative(self) -> None:
        with pytest.raises(DriftValidationError, match="max_uncertainty_mm"):
            make_landmark_definition(max_uncertainty_mm=-0.5)


# ===========================================================================
# LandmarkObservation Validation
# ===========================================================================

class TestLandmarkObservation:
    """Tests for LandmarkObservation validation."""

    def test_valid_construction(self) -> None:
        obs = make_landmark_observation()
        assert obs.landmark_id == "lm-nasion"
        assert obs.confidence == 0.95

    def test_immutable(self) -> None:
        obs = make_landmark_observation()
        with pytest.raises(AttributeError):
            obs.confidence = 0.5  # type: ignore

    def test_invalid_session_id(self) -> None:
        with pytest.raises(DriftValidationError, match="session_id"):
            make_landmark_observation(session_id="")

    def test_negative_epoch(self) -> None:
        with pytest.raises(DriftValidationError, match="epoch_id"):
            make_landmark_observation(epoch_id=-1)

    def test_negative_sequence(self) -> None:
        with pytest.raises(DriftValidationError, match="sequence_number"):
            make_landmark_observation(sequence_number=-1)

    def test_nan_observed_point(self) -> None:
        with pytest.raises(DriftValidationError, match="observed_point_mm"):
            make_landmark_observation(observed_point_mm=(float("nan"), 0.0, 0.0))

    def test_observed_point_exceeds_bound(self) -> None:
        with pytest.raises(DriftValidationError, match="operational bound"):
            make_landmark_observation(observed_point_mm=(MAX_POINT_COORDINATE_MM + 10.0, 0.0, 0.0))

    def test_confidence_negative(self) -> None:
        with pytest.raises(DriftValidationError, match="confidence"):
            make_landmark_observation(confidence=-0.1)

    def test_invalid_timestamp(self) -> None:
        with pytest.raises(DriftValidationError, match="timestamp"):
            make_landmark_observation(timestamp_utc="invalid-date")

    def test_empty_frame(self) -> None:
        with pytest.raises(DriftValidationError, match="coordinate_frame"):
            make_landmark_observation(coordinate_frame="")


# ===========================================================================
# LandmarkVerificationRecord Validation
# ===========================================================================

class TestLandmarkVerificationRecord:
    """Tests for LandmarkVerificationRecord validation."""

    def test_valid_construction(self) -> None:
        now = datetime.now(timezone.utc).isoformat()
        rec = LandmarkVerificationRecord(
            record_id="rec-01",
            session_id="test-session-01",
            landmark_id="lm-nasion",
            epoch_id=1,
            expected_point_mm=(100.0, 0.0, 0.0),
            observed_point_mm=(100.5, 0.0, 0.0),
            residual_error_mm=0.5,
            effective_tolerance_mm=2.0,
            state=DriftState.STABLE,
            passed=True,
            evaluated_at_utc=now,
        )
        assert rec.passed is True
        assert rec.residual_error_mm == 0.5

    def test_negative_residual_rejected(self) -> None:
        now = datetime.now(timezone.utc).isoformat()
        with pytest.raises(DriftValidationError, match="residual_error_mm"):
            LandmarkVerificationRecord(
                record_id="rec-01",
                session_id="test-session-01",
                landmark_id="lm-nasion",
                epoch_id=1,
                expected_point_mm=(100.0, 0.0, 0.0),
                observed_point_mm=(100.5, 0.0, 0.0),
                residual_error_mm=-0.5,
                effective_tolerance_mm=2.0,
                state=DriftState.STABLE,
                passed=True,
                evaluated_at_utc=now,
            )
