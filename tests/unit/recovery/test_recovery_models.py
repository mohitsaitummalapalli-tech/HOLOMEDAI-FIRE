# -*- coding: utf-8 -*-
"""Unit Tests for M17 Recovery Data Models, Invariants & Immutability."""

from __future__ import annotations

from dataclasses import FrozenInstanceError
import math

import pytest

from holomed.recovery.exceptions import RecoveryValidationError
from holomed.recovery.models import (
    RecoveryAuthorization,
    RecoveryState,
    RecoveryStatusRecord,
    RecoveryVerificationSnapshot,
    StagedRegistrationCandidate,
)
from holomed.registration.models import RegistrationQualityReport, RegistrationState

from tests.unit.recovery.conftest import (
    make_fiducial_cloud,
    make_identity_transform,
    make_recovery_authorization,
)


class TestRecoveryModelValidation:
    """Tests for model schema and invariant validations."""

    def test_authorization_valid(self) -> None:
        auth = make_recovery_authorization()
        assert auth.operator_id == "dr_chen_sr"
        assert auth.sequence_number == 1
        assert auth.authorization_reference == "AUTH-REC-2025-01"

    def test_authorization_invalid_operator_id(self) -> None:
        with pytest.raises(RecoveryValidationError, match="Invalid operator_id"):
            make_recovery_authorization(operator_id="invalid operator with spaces!")

    def test_authorization_empty_rationale(self) -> None:
        with pytest.raises(RecoveryValidationError, match="rationale"):
            make_recovery_authorization(rationale="   ")

    def test_authorization_invalid_sequence_number(self) -> None:
        with pytest.raises(RecoveryValidationError, match="sequence_number"):
            make_recovery_authorization(sequence_number=0)

    def test_authorization_empty_reference(self) -> None:
        with pytest.raises(RecoveryValidationError, match="authorization_reference"):
            make_recovery_authorization(authorization_reference="")

    def test_staged_candidate_invalid_session_id(self) -> None:
        cloud = make_fiducial_cloud()
        transform = make_identity_transform()
        quality = RegistrationQualityReport(
            fre_rms_mm=0.4,
            fre_max_mm=0.6,
            per_fiducial_residuals_mm=(0.3, 0.4, 0.5, 0.4),
            passed_clinical_threshold=True,
            warning_threshold_exceeded=False,
        )

        with pytest.raises(RecoveryValidationError, match="Invalid session_id"):
            StagedRegistrationCandidate(
                session_id="invalid session!@#",
                plan_id="plan-01",
                fiducial_cloud=cloud,
                candidate_transform=transform,
                quality_report=quality,
                staged_at_utc="2025-01-01T12:00:00Z",
            )

    def test_verification_snapshot_nan_drift_rejected(self) -> None:
        with pytest.raises(RecoveryValidationError, match="finite float"):
            RecoveryVerificationSnapshot(
                verification_id="v-01",
                session_id="session-01",
                operator_id="dr_chen",
                measured_drift_error_mm=float("nan"),
                passed=True,
                verified_at_utc="2025-01-01T12:00:00Z",
            )

    def test_verification_snapshot_negative_drift_rejected(self) -> None:
        with pytest.raises(RecoveryValidationError, match="non-negative"):
            RecoveryVerificationSnapshot(
                verification_id="v-01",
                session_id="session-01",
                operator_id="dr_chen",
                measured_drift_error_mm=-0.5,
                passed=True,
                verified_at_utc="2025-01-01T12:00:00Z",
            )

    def test_status_record_negative_revision_rejected(self) -> None:
        with pytest.raises(RecoveryValidationError, match="registration_revision"):
            RecoveryStatusRecord(
                recovery_id="rec-01",
                session_id="session-01",
                state=RecoveryState.IDLE,
                registration_revision=-1,
                prior_registration_state=None,
                active_transform=None,
                last_quality_report=None,
                last_verification=None,
                updated_at_utc="2025-01-01T12:00:00Z",
            )


class TestRecoveryModelImmutability:
    """Tests for model immutability."""

    def test_authorization_is_immutable(self) -> None:
        auth = make_recovery_authorization()
        with pytest.raises(FrozenInstanceError):
            auth.operator_id = "dr_other"  # type: ignore

    def test_staged_candidate_is_immutable(self) -> None:
        cloud = make_fiducial_cloud()
        transform = make_identity_transform()
        quality = RegistrationQualityReport(
            fre_rms_mm=0.4,
            fre_max_mm=0.6,
            per_fiducial_residuals_mm=(0.3, 0.4, 0.5, 0.4),
            passed_clinical_threshold=True,
            warning_threshold_exceeded=False,
        )

        candidate = StagedRegistrationCandidate(
            session_id="session-01",
            plan_id="plan-01",
            fiducial_cloud=cloud,
            candidate_transform=transform,
            quality_report=quality,
            staged_at_utc="2025-01-01T12:00:00Z",
        )
        with pytest.raises(FrozenInstanceError):
            candidate.session_id = "other"  # type: ignore

    def test_state_enum_completeness(self) -> None:
        expected_states = {"IDLE", "STAGED", "SOLVED", "VERIFIED", "ACTIVATING", "ACTIVATED", "FAILED", "BLOCKED"}
        actual_states = {s.value for s in RecoveryState}
        assert actual_states == expected_states
