# -*- coding: utf-8 -*-
"""Unit Tests for M17 Recovery Evaluator — Math, Staging, Checks & Consistency."""

from __future__ import annotations

from datetime import datetime, timezone, timedelta
import math
from unittest.mock import MagicMock

import pytest

from holomed.recovery.evaluator import RecoveryEvaluator
from holomed.recovery.exceptions import (
    RecoveryAccuracyError,
    RecoveryAuthorizationError,
    RecoveryConsistencyError,
    RecoveryValidationError,
    RecoveryVerificationError,
)
from holomed.registration.models import (
    FiducialCloud,
    FiducialPointPair,
    RegistrationState,
)

from tests.unit.recovery.conftest import (
    make_fiducial_cloud,
    make_identity_transform,
    make_recovery_authorization,
)


class TestRecoveryEvaluatorStaging:
    """Tests for pure staging and mathematical candidate solving."""

    def test_stage_and_solve_accurate_candidate(self) -> None:
        cloud = make_fiducial_cloud(offset_mm=(5.0, 10.0, -2.0), jitter_mm=0.2)
        candidate = RecoveryEvaluator.stage_and_solve_candidate(
            session_id="session-01",
            plan_id="plan-01",
            cloud=cloud,
            epoch_id=1,
        )
        assert candidate.session_id == "session-01"
        assert candidate.candidate_transform.epoch_id == 1
        assert candidate.quality_report.fre_rms_mm <= 1.5

    def test_stage_and_solve_noisy_candidate_exceeding_fre_raises(self) -> None:
        """Fiducials with anisotropic distortion -> FRE > 1.5 mm -> AccuracyError."""
        pairs = (
            FiducialPointPair("fid-01", (100.0, 0.0, 0.0), (102.0, 0.0, 0.0), "Landmark 1"),
            FiducialPointPair("fid-02", (0.0, 100.0, 0.0), (0.0, 97.0, 0.0), "Landmark 2"),
            FiducialPointPair("fid-03", (0.0, -100.0, 0.0), (0.0, -103.0, 0.0), "Landmark 3"),
            FiducialPointPair("fid-04", (0.0, 0.0, 100.0), (0.0, 0.0, 103.0), "Landmark 4"),
        )
        cloud = FiducialCloud(pairs=pairs)
        with pytest.raises(RecoveryAccuracyError, match="FRE RMS"):
            RecoveryEvaluator.stage_and_solve_candidate("session-01", "plan-01", cloud, 1)

    def test_stage_cloud_validation_error_propagates(self) -> None:
        """Invalid point cloud structure raises Validation error."""
        with pytest.raises(Exception):
            FiducialCloud(pairs=())


class TestRecoveryEvaluatorCheckpointVerification:
    """Tests for checkpoint drift verification against staged candidate."""

    def test_checkpoint_within_1_5_mm_passes(self) -> None:
        cloud = make_fiducial_cloud()
        candidate = RecoveryEvaluator.stage_and_solve_candidate("session-01", "plan-01", cloud, 1)

        snapshot = RecoveryEvaluator.verify_checkpoint(
            candidate=candidate,
            checkpoint_plan_mm=(100.0, 0.0, 0.0),
            checkpoint_measured_mm=(100.8, 0.0, 0.0),  # drift = 0.8 mm <= 1.5 mm
            operator_id="dr_chen",
        )
        assert snapshot.passed is True
        assert math.isclose(snapshot.measured_drift_error_mm, 0.8, abs_tol=1e-9)

    def test_checkpoint_exceeding_1_5_mm_raises(self) -> None:
        cloud = make_fiducial_cloud()
        candidate = RecoveryEvaluator.stage_and_solve_candidate("session-01", "plan-01", cloud, 1)

        with pytest.raises(RecoveryVerificationError, match="Candidate checkpoint drift"):
            RecoveryEvaluator.verify_checkpoint(
                candidate=candidate,
                checkpoint_plan_mm=(100.0, 0.0, 0.0),
                checkpoint_measured_mm=(102.5, 0.0, 0.0),  # drift = 2.5 mm > 1.5 mm
                operator_id="dr_chen",
            )


class TestRecoveryEvaluatorAuthorization:
    """Tests for operator authorization temporal validity."""

    def test_fresh_authorization_valid(self) -> None:
        now = datetime(2025, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
        auth = make_recovery_authorization(timestamp_utc=now.isoformat())
        RecoveryEvaluator.validate_authorization(auth, now_utc=now.isoformat())

    def test_stale_authorization_raises(self) -> None:
        now = datetime(2025, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
        stale_time = now - timedelta(milliseconds=350000)  # > 300,000 ms (5 min)
        auth = make_recovery_authorization(timestamp_utc=stale_time.isoformat())

        with pytest.raises(RecoveryAuthorizationError, match="stale"):
            RecoveryEvaluator.validate_authorization(auth, now_utc=now.isoformat())

    def test_future_skew_authorization_raises(self) -> None:
        now = datetime(2025, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
        future_time = now + timedelta(milliseconds=100)  # > 50 ms
        auth = make_recovery_authorization(timestamp_utc=future_time.isoformat())

        with pytest.raises(RecoveryAuthorizationError, match="future-dated"):
            RecoveryEvaluator.validate_authorization(auth, now_utc=now.isoformat())


class TestRecoveryEvaluatorConsistency:
    """Tests for post-activation consistency verification."""

    def test_consistent_services_pass(
        self, mock_registration_service, mock_drift_service, mock_proximity_service, mock_navigation_service
    ) -> None:
        RecoveryEvaluator.verify_post_activation_consistency(
            session_id="session-01",
            epoch_id=1,
            registration_service=mock_registration_service,
            drift_service=mock_drift_service,
            proximity_service=mock_proximity_service,
            navigation_service=mock_navigation_service,
        )

    def test_unverified_m13_raises(
        self, mock_registration_service, mock_drift_service, mock_proximity_service, mock_navigation_service
    ) -> None:
        rec = mock_registration_service.get_registration.return_value
        unverified_rec = MagicMock()
        unverified_rec.state = RegistrationState.DRAFT
        unverified_rec.transform = None
        mock_registration_service.get_registration.return_value = unverified_rec

        with pytest.raises(RecoveryConsistencyError, match="not VERIFIED"):
            RecoveryEvaluator.verify_post_activation_consistency(
                session_id="session-01",
                epoch_id=1,
                registration_service=mock_registration_service,
                drift_service=mock_drift_service,
                proximity_service=mock_proximity_service,
                navigation_service=mock_navigation_service,
            )

    def test_drift_service_not_ready_raises(
        self, mock_registration_service, mock_drift_service, mock_proximity_service, mock_navigation_service
    ) -> None:
        mock_drift_service.get_drift_status.return_value.state.value = "WARNING"
        with pytest.raises(RecoveryConsistencyError, match="DriftService state"):
            RecoveryEvaluator.verify_post_activation_consistency(
                session_id="session-01",
                epoch_id=1,
                registration_service=mock_registration_service,
                drift_service=mock_drift_service,
                proximity_service=mock_proximity_service,
                navigation_service=mock_navigation_service,
            )
