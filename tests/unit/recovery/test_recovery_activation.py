# -*- coding: utf-8 -*-
"""Unit Tests for M17 Non-Atomic Activation Window, Fail-Closed Behavior & Downstream Rebinds."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from holomed.recovery.exceptions import RecoveryActivationError
from holomed.recovery.models import RecoveryState
from holomed.recovery.service import RecoveryService

from tests.unit.recovery.conftest import (
    make_fiducial_cloud,
    make_recovery_authorization,
)


def _setup_verified_recovery(
    mock_dispatcher,
    mock_registration_service,
    mock_drift_service,
    mock_proximity_service,
    mock_navigation_service,
    mock_persistence_service,
    secret_filter,
    logger,
    runtime_context,
) -> RecoveryService:
    svc = RecoveryService(
        dispatcher=mock_dispatcher,
        registration_service=mock_registration_service,
        drift_service=mock_drift_service,
        proximity_service=mock_proximity_service,
        navigation_service=mock_navigation_service,
        persistence_service=mock_persistence_service,
        secret_filter=secret_filter,
        logger=logger,
    )
    svc.initialize(runtime_context)
    svc.start()

    cloud = make_fiducial_cloud()
    auth = make_recovery_authorization()

    # Stage candidate
    svc.stage_candidate("session-01", "plan-01", cloud)
    # Verify candidate
    svc.verify_candidate(
        session_id="session-01",
        authorization=auth,
        checkpoint_plan_mm=(100.0, 0.0, 0.0),
        checkpoint_measured_mm=(100.2, 0.0, 0.0),
    )
    return svc


class TestRecoveryActivationFailure:
    """Tests proving non-atomic activation window and fail-closed behavior."""

    def test_m13_not_called_during_staging_or_verification(
        self,
        mock_dispatcher,
        mock_registration_service,
        mock_drift_service,
        mock_proximity_service,
        mock_navigation_service,
        mock_persistence_service,
        secret_filter,
        logger,
        runtime_context,
    ) -> None:
        """Prove that staging and verification happen in isolated memory with ZERO M13 mutation."""
        svc = RecoveryService(
            dispatcher=mock_dispatcher,
            registration_service=mock_registration_service,
            drift_service=mock_drift_service,
            proximity_service=mock_proximity_service,
            navigation_service=mock_navigation_service,
            persistence_service=mock_persistence_service,
            secret_filter=secret_filter,
            logger=logger,
        )
        svc.initialize(runtime_context)
        svc.start()

        mock_registration_service.submit_fiducials.reset_mock()
        mock_registration_service.solve_registration.reset_mock()
        mock_registration_service.verify_registration.reset_mock()

        cloud = make_fiducial_cloud()
        auth = make_recovery_authorization()

        svc.stage_candidate("session-01", "plan-01", cloud)
        svc.verify_candidate("session-01", auth, (100.0, 0.0, 0.0), (100.2, 0.0, 0.0))

        # Assert M13 was NEVER touched
        assert mock_registration_service.submit_fiducials.call_count == 0
        assert mock_registration_service.solve_registration.call_count == 0
        assert mock_registration_service.verify_registration.call_count == 0

    def test_m13_activation_failure_transitions_to_failed_with_no_rollback(
        self,
        mock_dispatcher,
        mock_registration_service,
        mock_drift_service,
        mock_proximity_service,
        mock_navigation_service,
        mock_persistence_service,
        secret_filter,
        logger,
        runtime_context,
    ) -> None:
        """When M13 verify fails after submit succeeds, M17 enters FAILED, emits recovery.failed, and simulates no rollback."""
        svc = _setup_verified_recovery(
            mock_dispatcher,
            mock_registration_service,
            mock_drift_service,
            mock_proximity_service,
            mock_navigation_service,
            mock_persistence_service,
            secret_filter,
            logger,
            runtime_context,
        )

        # Make M13 verify_registration raise an error
        mock_registration_service.verify_registration.side_effect = RuntimeError("M13 drift verification failed")
        mock_dispatcher.dispatch.reset_mock()

        with pytest.raises(RecoveryActivationError, match="Recovery activation failed"):
            svc.activate_recovery("session-01")

        status = svc.get_recovery_status("session-01")
        assert status.state == RecoveryState.FAILED

        # Event recovery.failed was emitted
        assert mock_dispatcher.dispatch.call_count >= 1
        event_names = [call[0][0].message_name for call in mock_dispatcher.dispatch.call_args_list]
        assert "recovery.failed" in event_names

    def test_drift_service_rebind_failure_transitions_to_failed(
        self,
        mock_dispatcher,
        mock_registration_service,
        mock_drift_service,
        mock_proximity_service,
        mock_navigation_service,
        mock_persistence_service,
        secret_filter,
        logger,
        runtime_context,
    ) -> None:
        svc = _setup_verified_recovery(
            mock_dispatcher,
            mock_registration_service,
            mock_drift_service,
            mock_proximity_service,
            mock_navigation_service,
            mock_persistence_service,
            secret_filter,
            logger,
            runtime_context,
        )

        mock_drift_service.bind_landmarks.side_effect = RuntimeError("Drift bind error")

        with pytest.raises(RecoveryActivationError):
            svc.activate_recovery(
                session_id="session-01",
                landmarks=(MagicMock(),),
            )

        assert svc.get_recovery_status("session-01").state == RecoveryState.FAILED

    def test_audit_persistence_on_successful_activation(
        self,
        mock_dispatcher,
        mock_registration_service,
        mock_drift_service,
        mock_proximity_service,
        mock_navigation_service,
        mock_persistence_service,
        secret_filter,
        logger,
        runtime_context,
    ) -> None:
        svc = _setup_verified_recovery(
            mock_dispatcher,
            mock_registration_service,
            mock_drift_service,
            mock_proximity_service,
            mock_navigation_service,
            mock_persistence_service,
            secret_filter,
            logger,
            runtime_context,
        )

        mock_persistence_service.record_audit.reset_mock()
        status_rec = svc.activate_recovery("session-01")

        assert status_rec.state == RecoveryState.ACTIVATED
        assert mock_persistence_service.record_audit.call_count == 1

        audit_arg = mock_persistence_service.record_audit.call_args[0][0]
        assert audit_arg["session_id"] == "session-01"
        assert audit_arg["registration_revision"] == 1
        assert audit_arg["operator_id"] == "dr_chen_sr"
        assert "fre_rms_mm" in audit_arg
        assert "measured_drift_error_mm" in audit_arg
