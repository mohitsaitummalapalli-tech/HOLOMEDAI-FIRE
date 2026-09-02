# -*- coding: utf-8 -*-
"""Adversarial, Boundary & Invariant Verification Tests for M17 Spatial Recovery."""

from __future__ import annotations

import math
from unittest.mock import MagicMock

import pytest

from holomed.protocol.builders import create_command, create_query
from holomed.recovery.evaluator import RecoveryEvaluator
from holomed.recovery.exceptions import (
    RecoveryAccuracyError,
    RecoveryLifecycleError,
    RecoveryValidationError,
    RecoveryVerificationError,
)
from holomed.recovery.models import RecoveryState
from holomed.recovery.service import RecoveryService
from holomed.registration.models import FiducialCloud, FiducialPointPair
from holomed.runtime.service import ServiceState

from tests.unit.recovery.conftest import (
    make_fiducial_cloud,
    make_recovery_authorization,
)


def _make_started_service(
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
    return svc


class TestRecoveryAdversarialBoundaries:
    """Rigorous mathematical boundary tests for 1.5 mm thresholds."""

    def test_checkpoint_drift_boundary_1_5_mm_exact_passes(self) -> None:
        cloud = make_fiducial_cloud()
        candidate = RecoveryEvaluator.stage_and_solve_candidate("session-01", "plan-01", cloud, 1)

        # 1.5 mm exact Euclidean distance
        snapshot = RecoveryEvaluator.verify_checkpoint(
            candidate=candidate,
            checkpoint_plan_mm=(100.0, 0.0, 0.0),
            checkpoint_measured_mm=(101.5, 0.0, 0.0),
            operator_id="dr_chen",
        )
        assert snapshot.passed is True
        assert math.isclose(snapshot.measured_drift_error_mm, 1.5, abs_tol=1e-9)

    def test_checkpoint_drift_boundary_1_501_mm_fails(self) -> None:
        cloud = make_fiducial_cloud()
        candidate = RecoveryEvaluator.stage_and_solve_candidate("session-01", "plan-01", cloud, 1)

        # 1.501 mm exceeds threshold
        with pytest.raises(RecoveryVerificationError, match="exceeds recovery threshold"):
            RecoveryEvaluator.verify_checkpoint(
                candidate=candidate,
                checkpoint_plan_mm=(100.0, 0.0, 0.0),
                checkpoint_measured_mm=(101.501, 0.0, 0.0),
                operator_id="dr_chen",
            )


class TestRecoveryIllegalTransitions:
    """Exhaustive matrix of prohibited state transitions."""

    @pytest.mark.parametrize(
        "invalid_action",
        ["verify_from_idle", "activate_from_idle", "activate_from_staged"],
    )
    def test_illegal_state_actions_raise(
        self,
        invalid_action: str,
        mock_dispatcher,
        mock_registration_service,
        mock_drift_service,
        mock_proximity_service,
        mock_navigation_service,
        mock_persistence_service,
        secret_filter,
        logger,
        runtime_context,
        make_recovery_capability,
    ) -> None:
        svc = _make_started_service(
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
        auth = make_recovery_authorization()
        cap = make_recovery_capability(svc, "session-01", seq=1)

        if invalid_action == "verify_from_idle":
            with pytest.raises(RecoveryLifecycleError, match="must be in SOLVED state"):
                svc.verify_candidate("session-01", auth, (0, 0, 0), (0, 0, 0), sequence_number=1, capability=cap)
        elif invalid_action == "activate_from_idle":
            with pytest.raises(RecoveryLifecycleError, match="must be in VERIFIED state"):
                svc.activate_recovery("session-01", sequence_number=1, capability=cap)
        elif invalid_action == "activate_from_staged":
            svc.stage_candidate("session-01", "plan-01", make_fiducial_cloud(), sequence_number=1, capability=cap)
            cap2 = make_recovery_capability(svc, "session-01", seq=2)
            with pytest.raises(RecoveryLifecycleError, match="must be in VERIFIED state"):
                svc.activate_recovery("session-01", sequence_number=2, capability=cap2)

    def test_blocked_state_latching(
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
        make_recovery_capability,
    ) -> None:
        svc = _make_started_service(
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
        # Directly set state to BLOCKED
        svc._session_states["session-blocked"] = RecoveryState.BLOCKED
        cap = make_recovery_capability(svc, "session-blocked", seq=1)

        with pytest.raises(RecoveryLifecycleError, match="latching state BLOCKED"):
            svc.stage_candidate("session-blocked", "plan-01", make_fiducial_cloud(), sequence_number=1, capability=cap)


class TestRecoveryMultiSessionIsolation:
    """Test candidate isolation across concurrent clinical sessions."""

    def test_concurrent_sessions_do_not_interfere(
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
        make_recovery_capability,
    ) -> None:
        svc = _make_started_service(
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

        cloud_s1 = make_fiducial_cloud(offset_mm=(1.0, 0.0, 0.0))
        cloud_s2 = make_fiducial_cloud(offset_mm=(0.0, 2.0, 0.0))

        cap1 = make_recovery_capability(svc, "session-01", seq=1)
        cap2 = make_recovery_capability(svc, "session-02", seq=1)

        c1 = svc.stage_candidate("session-01", "plan-01", cloud_s1, sequence_number=1, capability=cap1)
        c2 = svc.stage_candidate("session-02", "plan-02", cloud_s2, sequence_number=1, capability=cap2)

        assert svc.get_recovery_status("session-01").state == RecoveryState.SOLVED
        assert svc.get_recovery_status("session-02").state == RecoveryState.SOLVED

        assert c1.candidate_transform.translation_vector_mm != c2.candidate_transform.translation_vector_mm

        # Verify session 1 only
        auth1 = make_recovery_authorization()
        cap3 = make_recovery_capability(svc, "session-01", seq=2)
        svc.verify_candidate("session-01", auth1, (100.0, 0.0, 0.0), (101.0, 0.0, 0.0), sequence_number=2, capability=cap3)

        assert svc.get_recovery_status("session-01").state == RecoveryState.VERIFIED
        assert svc.get_recovery_status("session-02").state == RecoveryState.SOLVED


class TestRecoveryPartialActivationFailures:
    """Rigorous tests for all partial activation failure branches."""

    def test_case_a_m16_rebind_failure(
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
        make_recovery_capability,
    ) -> None:
        svc = _make_started_service(
            mock_dispatcher, mock_registration_service, mock_drift_service,
            mock_proximity_service, mock_navigation_service, mock_persistence_service,
            secret_filter, logger, runtime_context,
        )
        cap1 = make_recovery_capability(svc, "session-01", seq=1)
        svc.stage_candidate("session-01", "plan-01", make_fiducial_cloud(), sequence_number=1, capability=cap1)
        cap2 = make_recovery_capability(svc, "session-01", seq=2)
        svc.verify_candidate("session-01", make_recovery_authorization(), (100, 0, 0), (100, 0, 0), sequence_number=2, capability=cap2)

        mock_drift_service.bind_landmarks.side_effect = RuntimeError("Drift bind crashed")
        mock_dispatcher.dispatch.reset_mock()

        cap3 = make_recovery_capability(svc, "session-01", seq=3)
        with pytest.raises(Exception):
            svc.activate_recovery("session-01", landmarks=(MagicMock(),), sequence_number=3, capability=cap3)

        assert svc.get_recovery_status("session-01").state == RecoveryState.FAILED
        event_names = [call[0][0].message_name for call in mock_dispatcher.dispatch.call_args_list]
        assert "recovery.spatial.activated" not in event_names
        assert "recovery.failed" in event_names

    def test_case_b_m15_rebind_failure(
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
        make_recovery_capability,
    ) -> None:
        svc = _make_started_service(
            mock_dispatcher, mock_registration_service, mock_drift_service,
            mock_proximity_service, mock_navigation_service, mock_persistence_service,
            secret_filter, logger, runtime_context,
        )
        cap1 = make_recovery_capability(svc, "session-01", seq=1)
        svc.stage_candidate("session-01", "plan-01", make_fiducial_cloud(), sequence_number=1, capability=cap1)
        cap2 = make_recovery_capability(svc, "session-01", seq=2)
        svc.verify_candidate("session-01", make_recovery_authorization(), (100, 0, 0), (100, 0, 0), sequence_number=2, capability=cap2)

        mock_proximity_service.bind_zones.side_effect = RuntimeError("Proximity bind crashed")
        mock_dispatcher.dispatch.reset_mock()

        cap3 = make_recovery_capability(svc, "session-01", seq=3)
        with pytest.raises(Exception):
            svc.activate_recovery("session-01", zones=(MagicMock(),), sequence_number=3, capability=cap3)

        assert svc.get_recovery_status("session-01").state == RecoveryState.FAILED
        event_names = [call[0][0].message_name for call in mock_dispatcher.dispatch.call_args_list]
        assert "recovery.spatial.activated" not in event_names
        assert "recovery.failed" in event_names

    def test_case_c_m14_rebind_failure(
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
        make_recovery_capability,
    ) -> None:
        svc = _make_started_service(
            mock_dispatcher, mock_registration_service, mock_drift_service,
            mock_proximity_service, mock_navigation_service, mock_persistence_service,
            secret_filter, logger, runtime_context,
        )
        cap1 = make_recovery_capability(svc, "session-01", seq=1)
        svc.stage_candidate("session-01", "plan-01", make_fiducial_cloud(), sequence_number=1, capability=cap1)
        cap2 = make_recovery_capability(svc, "session-01", seq=2)
        svc.verify_candidate("session-01", make_recovery_authorization(), (100, 0, 0), (100, 0, 0), sequence_number=2, capability=cap2)

        mock_navigation_service.bind_trajectory.side_effect = RuntimeError("Navigation bind crashed")
        mock_dispatcher.dispatch.reset_mock()

        cap3 = make_recovery_capability(svc, "session-01", seq=3)
        with pytest.raises(Exception):
            svc.activate_recovery("session-01", plan_trajectory=MagicMock(), sequence_number=3, capability=cap3)

        assert svc.get_recovery_status("session-01").state == RecoveryState.FAILED
        event_names = [call[0][0].message_name for call in mock_dispatcher.dispatch.call_args_list]
        assert "recovery.spatial.activated" not in event_names
        assert "recovery.failed" in event_names

    def test_case_d_post_activation_consistency_failure(
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
        make_recovery_capability,
    ) -> None:
        svc = _make_started_service(
            mock_dispatcher, mock_registration_service, mock_drift_service,
            mock_proximity_service, mock_navigation_service, mock_persistence_service,
            secret_filter, logger, runtime_context,
        )
        cap1 = make_recovery_capability(svc, "session-01", seq=1)
        svc.stage_candidate("session-01", "plan-01", make_fiducial_cloud(), sequence_number=1, capability=cap1)
        cap2 = make_recovery_capability(svc, "session-01", seq=2)
        svc.verify_candidate("session-01", make_recovery_authorization(), (100, 0, 0), (100, 0, 0), sequence_number=2, capability=cap2)

        # Make post-activation drift service return unready state
        mock_drift_service.get_drift_status.return_value.state.value = "WARNING"
        mock_dispatcher.dispatch.reset_mock()

        cap3 = make_recovery_capability(svc, "session-01", seq=3)
        with pytest.raises(Exception):
            svc.activate_recovery("session-01", landmarks=(MagicMock(),), sequence_number=3, capability=cap3)

        assert svc.get_recovery_status("session-01").state == RecoveryState.FAILED
        event_names = [call[0][0].message_name for call in mock_dispatcher.dispatch.call_args_list]
        assert "recovery.spatial.activated" not in event_names
        assert "recovery.failed" in event_names

    def test_recovery_revision_monotonicity_after_failure(
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
        make_recovery_capability,
    ) -> None:
        svc = _make_started_service(
            mock_dispatcher, mock_registration_service, mock_drift_service,
            mock_proximity_service, mock_navigation_service, mock_persistence_service,
            secret_filter, logger, runtime_context,
        )
        # 1. Success -> Revision 1
        cap1 = make_recovery_capability(svc, "session-01", seq=1)
        svc.stage_candidate("session-01", "plan-01", make_fiducial_cloud(), sequence_number=1, capability=cap1)
        cap2 = make_recovery_capability(svc, "session-01", seq=2)
        svc.verify_candidate("session-01", make_recovery_authorization(), (100, 0, 0), (100, 0, 0), sequence_number=2, capability=cap2)
        cap3 = make_recovery_capability(svc, "session-01", seq=3)
        rec1 = svc.activate_recovery("session-01", sequence_number=3, capability=cap3)
        assert rec1.registration_revision == 1

        # 2. Failure on next cycle -> State FAILED, revision stays 1
        cap4 = make_recovery_capability(svc, "session-01", seq=4)
        svc.stage_candidate("session-01", "plan-01", make_fiducial_cloud(), sequence_number=4, capability=cap4)
        cap5 = make_recovery_capability(svc, "session-01", seq=5)
        svc.verify_candidate("session-01", make_recovery_authorization(), (100, 0, 0), (100, 0, 0), sequence_number=5, capability=cap5)
        mock_drift_service.bind_landmarks.side_effect = RuntimeError("Crash")
        cap6 = make_recovery_capability(svc, "session-01", seq=6)
        with pytest.raises(Exception):
            svc.activate_recovery("session-01", landmarks=(MagicMock(),), sequence_number=6, capability=cap6)
        assert svc.get_recovery_status("session-01").registration_revision == 1
        assert svc.get_recovery_status("session-01").state == RecoveryState.FAILED

        # 3. Reset and retry -> Success -> Revision 2 (monotonic, no duplicate reuse)
        mock_drift_service.bind_landmarks.side_effect = None
        svc.reset_session("session-01")
        cap7 = make_recovery_capability(svc, "session-01", seq=7)
        svc.stage_candidate("session-01", "plan-01", make_fiducial_cloud(), sequence_number=7, capability=cap7)
        cap8 = make_recovery_capability(svc, "session-01", seq=8)
        svc.verify_candidate("session-01", make_recovery_authorization(), (100, 0, 0), (100, 0, 0), sequence_number=8, capability=cap8)
        cap9 = make_recovery_capability(svc, "session-01", seq=9)
        rec2 = svc.activate_recovery("session-01", sequence_number=9, capability=cap9)
        assert rec2.registration_revision == 2


class TestRecoveryInvariants:
    """Prove that RuntimeContext and M10 workflow are untouched."""

    def test_runtime_context_epoch_is_untouched(
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
        make_recovery_capability,
    ) -> None:
        svc = _make_started_service(
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
        assert runtime_context.epoch_id == 1

        cloud = make_fiducial_cloud()
        auth = make_recovery_authorization()
        cap1 = make_recovery_capability(svc, "session-01", seq=1)
        svc.stage_candidate("session-01", "plan-01", cloud, sequence_number=1, capability=cap1)
        cap2 = make_recovery_capability(svc, "session-01", seq=2)
        svc.verify_candidate("session-01", auth, (100.0, 0.0, 0.0), (100.0, 0.0, 0.0), sequence_number=2, capability=cap2)
        cap3 = make_recovery_capability(svc, "session-01", seq=3)
        svc.activate_recovery("session-01", sequence_number=3, capability=cap3)

        # Runtime epoch remains strictly 1
        assert runtime_context.epoch_id == 1
        assert svc.get_recovery_status("session-01").registration_revision == 1
