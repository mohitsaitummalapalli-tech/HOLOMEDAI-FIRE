# -*- coding: utf-8 -*-
"""Unit Tests for M17 RecoveryService — Lifecycle, Handles, Routes & State Machine."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from holomed.protocol.builders import create_command, create_query
from holomed.recovery.constants import MAX_ACTIVE_RECOVERY_SESSIONS, STRUCTURAL_RESOURCE_IDS
from holomed.recovery.exceptions import (
    RecoveryCapacityError,
    RecoveryLifecycleError,
    RecoveryValidationError,
)
from holomed.recovery.models import RecoveryState
from holomed.recovery.service import RecoveryService
from holomed.runtime.models import HealthStatus
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


class TestRecoveryServiceLifecycle:
    """Tests for 4-handle lifecycle and service states."""

    def test_initialize_acquires_exactly_4_structural_handles(
        self, mock_dispatcher, mock_registration_service, secret_filter, logger, runtime_context
    ) -> None:
        svc = RecoveryService(
            dispatcher=mock_dispatcher,
            registration_service=mock_registration_service,
            secret_filter=secret_filter,
            logger=logger,
        )
        assert svc.state == ServiceState.UNINITIALIZED

        svc.initialize(runtime_context)
        assert svc.state == ServiceState.INITIALIZED
        assert len(svc.resources.outstanding_handles) == 4
        handle_ids = {h.resource_id for h in svc.resources.outstanding_handles}
        assert handle_ids == set(STRUCTURAL_RESOURCE_IDS)

    def test_start_and_stop_releases_handles_idempotently(
        self, mock_dispatcher, mock_registration_service, secret_filter, logger, runtime_context
    ) -> None:
        svc = RecoveryService(
            dispatcher=mock_dispatcher,
            registration_service=mock_registration_service,
            secret_filter=secret_filter,
            logger=logger,
        )
        svc.initialize(runtime_context)
        svc.start()
        assert svc.state == ServiceState.STARTED

        svc.stop()
        assert svc.state == ServiceState.STOPPED
        assert svc.resources.is_empty

        # Idempotent second stop
        svc.stop()
        assert svc.state == ServiceState.STOPPED

    def test_health_reporting(
        self, mock_dispatcher, mock_registration_service, secret_filter, logger, runtime_context
    ) -> None:
        svc = RecoveryService(
            dispatcher=mock_dispatcher,
            registration_service=mock_registration_service,
            secret_filter=secret_filter,
            logger=logger,
        )
        assert svc.health().status == HealthStatus.UNHEALTHY

        svc.initialize(runtime_context)
        svc.start()
        assert svc.health().status == HealthStatus.HEALTHY


class TestRecoveryServiceStateMachine:
    """Tests for recovery state machine transitions and latching behavior."""

    def test_stage_and_verify_transitions(
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
        cloud = make_fiducial_cloud()
        auth = make_recovery_authorization()

        # Step 1: Stage candidate -> SOLVED
        candidate = svc.stage_candidate("session-01", "plan-01", cloud)
        assert candidate.session_id == "session-01"
        status = svc.get_recovery_status("session-01")
        assert status.state == RecoveryState.SOLVED

        # Step 2: Verify candidate -> VERIFIED
        snapshot = svc.verify_candidate(
            session_id="session-01",
            authorization=auth,
            checkpoint_plan_mm=(100.0, 0.0, 0.0),
            checkpoint_measured_mm=(100.4, 0.0, 0.0),
        )
        assert snapshot.passed is True
        status = svc.get_recovery_status("session-01")
        assert status.state == RecoveryState.VERIFIED

        # Step 3: Activate recovery -> ACTIVATED
        status_rec = svc.activate_recovery("session-01")
        assert status_rec.state == RecoveryState.ACTIVATED
        assert status_rec.registration_revision == 1

        # Second recovery advances registration_revision to 2
        svc.stage_candidate("session-01", "plan-01", cloud)
        svc.verify_candidate(
            session_id="session-01",
            authorization=auth,
            checkpoint_plan_mm=(100.0, 0.0, 0.0),
            checkpoint_measured_mm=(100.4, 0.0, 0.0),
        )
        status_rec2 = svc.activate_recovery("session-01")
        assert status_rec2.registration_revision == 2

    def test_failed_state_is_latching(
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
        cloud = make_fiducial_cloud()
        svc.stage_candidate("session-01", "plan-01", cloud)
        auth = make_recovery_authorization()

        # Fail verification with large drift
        with pytest.raises(Exception):
            svc.verify_candidate(
                session_id="session-01",
                authorization=auth,
                checkpoint_plan_mm=(100.0, 0.0, 0.0),
                checkpoint_measured_mm=(105.0, 0.0, 0.0),  # 5.0 mm drift > 1.5 mm
            )

        status = svc.get_recovery_status("session-01")
        assert status.state == RecoveryState.FAILED

        # In FAILED state, subsequent calls raise RecoveryLifecycleError (latching!)
        with pytest.raises(RecoveryLifecycleError, match="latching state FAILED"):
            svc.stage_candidate("session-01", "plan-01", cloud)

        # Resetting session permits recovery restart
        svc.reset_session("session-01")
        assert svc.get_recovery_status("session-01").state == RecoveryState.IDLE
        svc.stage_candidate("session-01", "plan-01", cloud)
        assert svc.get_recovery_status("session-01").state == RecoveryState.SOLVED


class TestRecoveryServiceRoutes:
    """Tests for dispatcher command and query route handlers."""

    def test_stage_and_verify_command_routes(
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

        # 1. recovery.stage command
        stage_cmd = create_command(
            message_name="recovery.stage",
            source="test",
            target="recovery_service",
            payload={
                "session_id": "session-01",
                "plan_id": "plan-01",
                "fiducials": [
                    {"fiducial_id": "f1", "planned_point_mm": [100.0, 0.0, 0.0], "measured_point_mm": [100.2, 0.0, 0.0]},
                    {"fiducial_id": "f2", "planned_point_mm": [0.0, 100.0, 0.0], "measured_point_mm": [0.0, 100.2, 0.0]},
                    {"fiducial_id": "f3", "planned_point_mm": [0.0, -100.0, 0.0], "measured_point_mm": [0.0, -100.2, 0.0]},
                    {"fiducial_id": "f4", "planned_point_mm": [0.0, 0.0, 100.0], "measured_point_mm": [0.0, 0.0, 100.2]},
                ],
            },
        )
        resp = svc.handle_stage_command(stage_cmd)
        assert resp.payload["state"] == "SOLVED"

        # 2. recovery.verify command
        auth = make_recovery_authorization()
        verify_cmd = create_command(
            message_name="recovery.verify",
            source="test",
            target="recovery_service",
            payload={
                "session_id": "session-01",
                "authorization": {
                    "operator_id": auth.operator_id,
                    "rationale": auth.rationale,
                    "timestamp_utc": auth.timestamp_utc,
                    "sequence_number": auth.sequence_number,
                    "authorization_reference": auth.authorization_reference,
                },
                "checkpoint_plan_mm": [100.0, 0.0, 0.0],
                "checkpoint_measured_mm": [100.3, 0.0, 0.0],
            },
        )
        resp = svc.handle_verify_command(verify_cmd)
        assert resp.payload["state"] == "VERIFIED"

        # 3. recovery.activate command
        act_cmd = create_command(
            message_name="recovery.activate",
            source="test",
            target="recovery_service",
            payload={"session_id": "session-01"},
        )
        resp = svc.handle_activate_command(act_cmd)
        assert resp.payload["state"] == "ACTIVATED"
        assert resp.payload["registration_revision"] == 1

        # 4. recovery.status.get query
        qry = create_query(
            message_name="recovery.status.get",
            source="test",
            target="recovery_service",
            payload={"session_id": "session-01"},
        )
        resp = svc.handle_get_status_query(qry)
        assert resp.payload["state"] == "ACTIVATED"
        assert resp.payload["registration_revision"] == 1
