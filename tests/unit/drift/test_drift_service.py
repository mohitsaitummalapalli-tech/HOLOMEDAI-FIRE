# -*- coding: utf-8 -*-
"""Unit Tests for M16 DriftService — Lifecycle, Bind, Evaluate & Dispatcher Routes."""

from __future__ import annotations

import math
from datetime import datetime, timezone, timedelta
from unittest.mock import MagicMock

import pytest

from holomed.drift.constants import MAX_ACTIVE_DRIFT_SESSIONS, MAX_LANDMARKS_PER_SESSION
from holomed.drift.exceptions import (
    DriftCapacityError,
    DriftEpochMismatchError,
    DriftLifecycleError,
    DriftRegistrationError,
    DriftSequenceError,
    DriftValidationError,
)
from holomed.drift.models import DriftState
from holomed.drift.service import DriftService, STRUCTURAL_RESOURCE_IDS
from holomed.registration.models import RegistrationState
from holomed.runtime.models import HealthStatus
from holomed.runtime.service import ServiceState

from tests.unit.drift.conftest import (
    make_landmark_definition,
    make_landmark_observation,
    make_registration_record,
)


def _make_started_service(
    mock_dispatcher=None,
    mock_registration_service=None,
    secret_filter=None,
    logger=None,
    runtime_context=None,
) -> DriftService:
    """Helper to initialize and start DriftService for testing."""
    svc = DriftService(
        dispatcher=mock_dispatcher,
        registration_service=mock_registration_service,
        secret_filter=secret_filter,
        logger=logger,
    )
    svc.initialize(runtime_context)
    svc.start()
    return svc


# ===========================================================================
# Lifecycle Tests
# ===========================================================================

class TestDriftServiceLifecycle:
    """Tests for DriftService IService lifecycle."""

    def test_initial_state_uninitialized(self, mock_dispatcher, mock_registration_service, secret_filter, logger) -> None:
        svc = DriftService(mock_dispatcher, mock_registration_service, secret_filter, logger)
        assert svc.state == ServiceState.UNINITIALIZED

    def test_initialize_acquires_4_handles(self, mock_dispatcher, mock_registration_service, secret_filter, logger, runtime_context) -> None:
        svc = DriftService(mock_dispatcher, mock_registration_service, secret_filter, logger)
        svc.initialize(runtime_context)
        assert svc.state == ServiceState.INITIALIZED
        assert len(svc.resources.outstanding_handles) == 4

    def test_start_transitions_to_started(self, mock_dispatcher, mock_registration_service, secret_filter, logger, runtime_context) -> None:
        svc = DriftService(mock_dispatcher, mock_registration_service, secret_filter, logger)
        svc.initialize(runtime_context)
        svc.start()
        assert svc.state == ServiceState.STARTED

    def test_stop_releases_all_handles(self, mock_dispatcher, mock_registration_service, secret_filter, logger, runtime_context) -> None:
        svc = _make_started_service(mock_dispatcher, mock_registration_service, secret_filter, logger, runtime_context)
        svc.stop()
        assert svc.state == ServiceState.STOPPED
        assert svc.resources.is_empty

    def test_structural_resource_ids(self) -> None:
        assert len(STRUCTURAL_RESOURCE_IDS) == 4
        assert "drift.registry" in STRUCTURAL_RESOURCE_IDS
        assert "drift.evaluator" in STRUCTURAL_RESOURCE_IDS
        assert "drift.landmarks" in STRUCTURAL_RESOURCE_IDS
        assert "drift.interlock" in STRUCTURAL_RESOURCE_IDS


# ===========================================================================
# Bind Landmarks Tests
# ===========================================================================

class TestDriftServiceBindLandmarks:
    """Tests for bind_landmarks()."""

    def test_bind_landmarks_sets_state_to_ready(
        self, mock_dispatcher, mock_registration_service, secret_filter, logger, runtime_context, default_landmarks
    ) -> None:
        """Binding landmarks sets session state to READY, never prematurely STABLE."""
        svc = _make_started_service(mock_dispatcher, mock_registration_service, secret_filter, logger, runtime_context)
        svc.bind_landmarks("session-01", default_landmarks)

        status = svc.get_drift_status("session-01")
        assert status.state == DriftState.READY
        assert status.bound_landmark_count == 3
        assert status.verified_landmark_count == 0
        assert status.is_safe is True

    def test_bind_landmarks_without_verified_registration_fails(
        self, mock_dispatcher, mock_unverified_registration_service, secret_filter, logger, runtime_context, default_landmarks
    ) -> None:
        svc = _make_started_service(mock_dispatcher, mock_unverified_registration_service, secret_filter, logger, runtime_context)
        with pytest.raises(DriftRegistrationError, match="verified M13"):
            svc.bind_landmarks("session-01", default_landmarks)

    def test_duplicate_landmark_id_rejected(
        self, mock_dispatcher, mock_registration_service, secret_filter, logger, runtime_context
    ) -> None:
        svc = _make_started_service(mock_dispatcher, mock_registration_service, secret_filter, logger, runtime_context)
        dupes = (
            make_landmark_definition("lm-01", "Nasion", (100.0, 0.0, 0.0)),
            make_landmark_definition("lm-01", "Nasion Dup", (100.0, 0.0, 0.0)),
        )
        with pytest.raises(DriftValidationError, match="Duplicate landmark_id"):
            svc.bind_landmarks("session-01", dupes)

    def test_empty_landmarks_rejected(
        self, mock_dispatcher, mock_registration_service, secret_filter, logger, runtime_context
    ) -> None:
        svc = _make_started_service(mock_dispatcher, mock_registration_service, secret_filter, logger, runtime_context)
        with pytest.raises(DriftValidationError, match="at least one"):
            svc.bind_landmarks("session-01", ())


# ===========================================================================
# Evaluate Tests & State Transitions
# ===========================================================================

class TestDriftServiceEvaluate:
    """Tests for evaluate() and session state updates."""

    def test_successful_verification_transitions_ready_to_stable_with_dwell(
        self, mock_dispatcher, mock_registration_service, secret_filter, logger, runtime_context, default_landmarks
    ) -> None:
        """Dwell progression: sample 1 & 2 fail dwell (WARNING), sample 3 satisfies dwell (STABLE)."""
        svc = _make_started_service(mock_dispatcher, mock_registration_service, secret_filter, logger, runtime_context)
        svc.bind_landmarks("session-01", default_landmarks)

        t0 = datetime(2025, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
        t1 = t0 + timedelta(milliseconds=80)
        t2 = t0 + timedelta(milliseconds=180)  # Total dwell = 180 ms >= 150 ms

        # Sample 1 (N=1 < 3) -> WARNING
        obs1 = make_landmark_observation(
            landmark_id="lm-nasion", session_id="session-01",
            observed_point_mm=(100.2, 0.0, 0.0), sequence_number=1,
            timestamp_utc=t0.isoformat(),
        )
        rec1 = svc.evaluate(obs1, now_utc=t0.isoformat())
        assert rec1.state == DriftState.WARNING
        assert rec1.passed is False

        # Sample 2 (N=2 < 3) -> WARNING
        obs2 = make_landmark_observation(
            landmark_id="lm-nasion", session_id="session-01",
            observed_point_mm=(100.2, 0.0, 0.0), sequence_number=2,
            timestamp_utc=t1.isoformat(),
        )
        rec2 = svc.evaluate(obs2, now_utc=t1.isoformat())
        assert rec2.state == DriftState.WARNING
        assert rec2.passed is False

        # Sample 3 (N=3 >= 3, duration >= 150 ms, jitter <= 0.5 mm) -> STABLE!
        obs3 = make_landmark_observation(
            landmark_id="lm-nasion", session_id="session-01",
            observed_point_mm=(100.2, 0.0, 0.0), sequence_number=3,
            timestamp_utc=t2.isoformat(),
        )
        rec3 = svc.evaluate(obs3, now_utc=t2.isoformat())
        assert rec3.state == DriftState.STABLE
        assert rec3.passed is True

        status = svc.get_drift_status("session-01")
        assert status.state == DriftState.STABLE
        assert status.verified_landmark_count == 1

    def test_drift_exceeded_transitions_to_drift_exceeded_and_emits_only_breached(
        self, mock_dispatcher, mock_registration_service, secret_filter, logger, runtime_context, default_landmarks
    ) -> None:
        svc = _make_started_service(mock_dispatcher, mock_registration_service, secret_filter, logger, runtime_context)
        svc.bind_landmarks("session-01", default_landmarks)
        mock_dispatcher.dispatch.reset_mock()

        obs = make_landmark_observation(
            landmark_id="lm-nasion", session_id="session-01",
            observed_point_mm=(103.0, 0.0, 0.0), sequence_number=1,  # 3.0 mm > 2.0 mm
        )
        rec = svc.evaluate(obs)
        assert rec.state == DriftState.DRIFT_EXCEEDED
        assert rec.passed is False

        status = svc.get_drift_status("session-01")
        assert status.state == DriftState.DRIFT_EXCEEDED
        assert status.is_safe is False

        # Verify ONLY drift.breached is emitted (ZERO drift.interlock.triggered)
        assert mock_dispatcher.dispatch.call_count == 1
        assert mock_dispatcher.dispatch.call_args[0][0].message_name == "drift.breached"

    def test_drift_exceeded_is_latching_and_cannot_auto_recover(
        self, mock_dispatcher, mock_registration_service, secret_filter, logger, runtime_context, default_landmarks
    ) -> None:
        """Once in DRIFT_EXCEEDED, ordinary evaluations cannot automatically recover state to STABLE."""
        svc = _make_started_service(mock_dispatcher, mock_registration_service, secret_filter, logger, runtime_context)
        svc.bind_landmarks("session-01", default_landmarks)

        # Breach drift
        obs1 = make_landmark_observation(
            landmark_id="lm-nasion", session_id="session-01",
            observed_point_mm=(103.0, 0.0, 0.0), sequence_number=1,
        )
        svc.evaluate(obs1)
        assert svc.get_drift_status("session-01").state == DriftState.DRIFT_EXCEEDED

        # Attempt recovery with accurate point
        t_now = datetime.now(timezone.utc)
        obs2 = make_landmark_observation(
            landmark_id="lm-nasion", session_id="session-01",
            observed_point_mm=(100.0, 0.0, 0.0), sequence_number=2,
            timestamp_utc=t_now.isoformat(),
        )
        svc.evaluate(obs2, now_utc=t_now.isoformat())

        # Session state MUST REMAIN DRIFT_EXCEEDED (latching)
        assert svc.get_drift_status("session-01").state == DriftState.DRIFT_EXCEEDED

        # Recovery requires re-binding
        svc.bind_landmarks("session-01", default_landmarks)
        assert svc.get_drift_status("session-01").state == DriftState.READY

    def test_sequence_number_monotonicity(
        self, mock_dispatcher, mock_registration_service, secret_filter, logger, runtime_context, default_landmarks
    ) -> None:
        svc = _make_started_service(mock_dispatcher, mock_registration_service, secret_filter, logger, runtime_context)
        svc.bind_landmarks("session-01", default_landmarks)

        obs1 = make_landmark_observation(session_id="session-01", sequence_number=5)
        svc.evaluate(obs1)

        obs2 = make_landmark_observation(session_id="session-01", sequence_number=3)
        with pytest.raises(DriftSequenceError, match="Non-monotonic"):
            svc.evaluate(obs2)

    def test_registration_invalidation_interlocks_session(
        self, mock_dispatcher, mock_registration_service, secret_filter, logger, runtime_context, default_landmarks
    ) -> None:
        svc = _make_started_service(mock_dispatcher, mock_registration_service, secret_filter, logger, runtime_context)
        svc.bind_landmarks("session-01", default_landmarks)

        # Invalidate registration
        inv_record = make_registration_record(session_id="session-01", state=RegistrationState.INVALIDATED)
        mock_registration_service.get_registration.return_value = inv_record

        obs = make_landmark_observation(session_id="session-01", sequence_number=1)
        with pytest.raises(DriftRegistrationError, match="not verified"):
            svc.evaluate(obs)

        status = svc.get_drift_status("session-01")
        assert status.state == DriftState.INTERLOCKED


# ===========================================================================
# Dispatcher Route Handlers
# ===========================================================================

class TestDriftServiceRoutes:
    """Tests for dispatcher route handlers."""

    def test_evaluate_command_route(
        self, mock_dispatcher, mock_registration_service, secret_filter, logger, runtime_context, default_landmarks
    ) -> None:
        from holomed.protocol.builders import create_command

        svc = _make_started_service(mock_dispatcher, mock_registration_service, secret_filter, logger, runtime_context)
        svc.bind_landmarks("session-01", default_landmarks)

        # 3 fresh observations within dwell buffer
        t0 = datetime.now(timezone.utc) - timedelta(milliseconds=200)
        for i in range(3):
            t_sample = t0 + timedelta(milliseconds=i * 80)
            cmd = create_command(
                message_name="drift.evaluate",
                source="test",
                target="drift_service",
                payload={
                    "session_id": "session-01",
                    "landmark_id": "lm-nasion",
                    "observed_point_mm": [100.2, 0.0, 0.0],
                    "confidence": 0.95,
                    "uncertainty_mm": 0.5,
                    "sequence_number": i + 1,
                    "timestamp_utc": t_sample.isoformat(),
                },
            )
            response = svc.handle_evaluate_command(cmd)

        assert response.message_name == "drift.evaluate.response"
        assert response.payload["state"] == "STABLE"
        assert response.payload["passed"] is True
        assert "xr_presentation" in response.payload

    def test_status_get_query_route(
        self, mock_dispatcher, mock_registration_service, secret_filter, logger, runtime_context, default_landmarks
    ) -> None:
        from holomed.protocol.builders import create_query

        svc = _make_started_service(mock_dispatcher, mock_registration_service, secret_filter, logger, runtime_context)
        svc.bind_landmarks("session-01", default_landmarks)

        qry = create_query(
            message_name="drift.status.get",
            source="test",
            target="drift_service",
            payload={"session_id": "session-01"},
        )
        response = svc.handle_get_status_query(qry)
        assert response.payload["state"] == "READY"
        assert response.payload["bound_landmark_count"] == 3

    def test_landmarks_get_query_route(
        self, mock_dispatcher, mock_registration_service, secret_filter, logger, runtime_context, default_landmarks
    ) -> None:
        from holomed.protocol.builders import create_query

        svc = _make_started_service(mock_dispatcher, mock_registration_service, secret_filter, logger, runtime_context)
        svc.bind_landmarks("session-01", default_landmarks)

        qry = create_query(
            message_name="drift.landmarks.get",
            source="test",
            target="drift_service",
            payload={"session_id": "session-01"},
        )
        response = svc.handle_get_landmarks_query(qry)
        assert response.payload["landmark_count"] == 3
        assert len(response.payload["landmarks"]) == 3

    def test_evaluate_missing_args_error_response(
        self, mock_dispatcher, mock_registration_service, secret_filter, logger, runtime_context
    ) -> None:
        from holomed.protocol.builders import create_command

        svc = _make_started_service(mock_dispatcher, mock_registration_service, secret_filter, logger, runtime_context)
        cmd = create_command(
            message_name="drift.evaluate",
            source="test",
            target="drift_service",
            payload={"session_id": "session-01"},  # Missing landmark_id and observed_point_mm
        )
        response = svc.handle_evaluate_command(cmd)
        assert response.message_name == "drift.evaluate.error"

    def test_get_status_missing_args_error_response(
        self, mock_dispatcher, mock_registration_service, secret_filter, logger, runtime_context
    ) -> None:
        from holomed.protocol.builders import create_query

        svc = _make_started_service(mock_dispatcher, mock_registration_service, secret_filter, logger, runtime_context)
        qry = create_query(
            message_name="drift.status.get",
            source="test",
            target="drift_service",
            payload={},
        )
        response = svc.handle_get_status_query(qry)
        assert response.message_name == "drift.status.get.error"

    def test_get_landmarks_missing_args_error_response(
        self, mock_dispatcher, mock_registration_service, secret_filter, logger, runtime_context
    ) -> None:
        from holomed.protocol.builders import create_query

        svc = _make_started_service(mock_dispatcher, mock_registration_service, secret_filter, logger, runtime_context)
        qry = create_query(
            message_name="drift.landmarks.get",
            source="test",
            target="drift_service",
            payload={},
        )
        response = svc.handle_get_landmarks_query(qry)
        assert response.message_name == "drift.landmarks.get.error"


# ===========================================================================
# Capacity & Session Isolation Tests
# ===========================================================================

class TestDriftServiceCapacity:
    """Tests for capacity limits and session isolation."""

    def test_session_capacity_limit(
        self, mock_dispatcher, mock_registration_service, secret_filter, logger, runtime_context, default_landmarks
    ) -> None:
        svc = _make_started_service(mock_dispatcher, mock_registration_service, secret_filter, logger, runtime_context)
        for i in range(MAX_ACTIVE_DRIFT_SESSIONS):
            svc.bind_landmarks(f"session-{i:03d}", default_landmarks)

        with pytest.raises(DriftCapacityError, match="Max active"):
            svc.bind_landmarks("session-overflow", default_landmarks)

    def test_landmark_capacity_limit(
        self, mock_dispatcher, mock_registration_service, secret_filter, logger, runtime_context
    ) -> None:
        svc = _make_started_service(mock_dispatcher, mock_registration_service, secret_filter, logger, runtime_context)
        too_many = tuple(
            make_landmark_definition(f"lm-{i:03d}", f"Landmark {i}")
            for i in range(MAX_LANDMARKS_PER_SESSION + 1)
        )
        with pytest.raises(DriftCapacityError, match="Max landmarks"):
            svc.bind_landmarks("session-01", too_many)

    def test_session_mismatch_fails_evaluation(
        self, mock_dispatcher, mock_registration_service, secret_filter, logger, runtime_context, default_landmarks
    ) -> None:
        svc = _make_started_service(mock_dispatcher, mock_registration_service, secret_filter, logger, runtime_context)
        svc.bind_landmarks("session-01", default_landmarks)

        obs = make_landmark_observation(session_id="unbound-session")
        with pytest.raises(DriftLifecycleError, match="No landmarks bound"):
            svc.evaluate(obs)

    def test_unknown_landmark_fails_evaluation(
        self, mock_dispatcher, mock_registration_service, secret_filter, logger, runtime_context, default_landmarks
    ) -> None:
        svc = _make_started_service(mock_dispatcher, mock_registration_service, secret_filter, logger, runtime_context)
        svc.bind_landmarks("session-01", default_landmarks)

        obs = make_landmark_observation(session_id="session-01", landmark_id="lm-unknown")
        with pytest.raises(DriftValidationError, match="not found"):
            svc.evaluate(obs)

    def test_health_reporting(
        self, mock_dispatcher, mock_registration_service, secret_filter, logger, runtime_context, default_landmarks
    ) -> None:
        svc = _make_started_service(mock_dispatcher, mock_registration_service, secret_filter, logger, runtime_context)
        health = svc.health()
        assert health.status == HealthStatus.HEALTHY
        assert "Active drift sessions: 0" in health.message

        svc.bind_landmarks("session-01", default_landmarks)
        health = svc.health()
        assert "Active drift sessions: 1" in health.message

    def test_health_when_uninitialized(self, mock_dispatcher, mock_registration_service, secret_filter, logger) -> None:
        svc = DriftService(mock_dispatcher, mock_registration_service, secret_filter, logger)
        health = svc.health()
        assert health.status == HealthStatus.UNHEALTHY

    def test_stop_idempotent(
        self, mock_dispatcher, mock_registration_service, secret_filter, logger, runtime_context
    ) -> None:
        svc = _make_started_service(mock_dispatcher, mock_registration_service, secret_filter, logger, runtime_context)
        svc.stop()
        svc.stop()  # Second call must be safe no-op
        assert svc.state == ServiceState.STOPPED

    def test_cannot_start_when_uninitialized(
        self, mock_dispatcher, mock_registration_service, secret_filter, logger
    ) -> None:
        svc = DriftService(mock_dispatcher, mock_registration_service, secret_filter, logger)
        with pytest.raises(DriftLifecycleError, match="Cannot start"):
            svc.start()

    def test_cannot_initialize_when_started(
        self, mock_dispatcher, mock_registration_service, secret_filter, logger, runtime_context
    ) -> None:
        svc = _make_started_service(mock_dispatcher, mock_registration_service, secret_filter, logger, runtime_context)
        with pytest.raises(DriftLifecycleError, match="Cannot initialize"):
            svc.initialize(runtime_context)

    def test_resources_property_before_init_raises(
        self, mock_dispatcher, mock_registration_service, secret_filter, logger
    ) -> None:
        svc = DriftService(mock_dispatcher, mock_registration_service, secret_filter, logger)
        with pytest.raises(DriftLifecycleError, match="resources unavailable"):
            _ = svc.resources

