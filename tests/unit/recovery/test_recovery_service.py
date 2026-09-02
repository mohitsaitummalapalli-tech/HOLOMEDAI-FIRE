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
        cloud = make_fiducial_cloud()
        auth = make_recovery_authorization()

        # Step 1: Stage candidate -> SOLVED
        cap1 = make_recovery_capability(svc, "session-01", seq=1)
        candidate = svc.stage_candidate("session-01", "plan-01", cloud, capability=cap1)
        assert candidate.plan_id == "plan-01"
        status = svc.get_recovery_status("session-01")
        assert status.state == RecoveryState.SOLVED

        # Step 2: Verify candidate -> VERIFIED
        cap2 = make_recovery_capability(svc, "session-01", seq=2)
        snapshot = svc.verify_candidate(
            session_id="session-01",
            authorization=auth,
            checkpoint_plan_mm=(100.0, 0.0, 0.0),
            checkpoint_measured_mm=(100.4, 0.0, 0.0),
            sequence_number=2,
            capability=cap2,
        )
        assert snapshot.passed is True
        status = svc.get_recovery_status("session-01")
        assert status.state == RecoveryState.VERIFIED

        # Step 3: Activate recovery -> ACTIVATED
        cap3 = make_recovery_capability(svc, "session-01", seq=3)
        status_rec = svc.activate_recovery("session-01", sequence_number=3, capability=cap3)
        assert status_rec.state == RecoveryState.ACTIVATED
        assert status_rec.registration_revision == 1

        # Second recovery advances registration_revision to 2
        cap4 = make_recovery_capability(svc, "session-01", seq=4)
        svc.stage_candidate("session-01", "plan-01", cloud, sequence_number=4, capability=cap4)
        cap5 = make_recovery_capability(svc, "session-01", seq=5)
        svc.verify_candidate(
            session_id="session-01",
            authorization=auth,
            checkpoint_plan_mm=(100.0, 0.0, 0.0),
            checkpoint_measured_mm=(100.4, 0.0, 0.0),
            sequence_number=5,
            capability=cap5,
        )
        cap6 = make_recovery_capability(svc, "session-01", seq=6)
        status_rec2 = svc.activate_recovery("session-01", sequence_number=6, capability=cap6)
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
        cloud = make_fiducial_cloud()
        cap1 = make_recovery_capability(svc, "session-01", seq=1)
        svc.stage_candidate("session-01", "plan-01", cloud, capability=cap1)
        auth = make_recovery_authorization()

        # Fail verification with large drift
        cap2 = make_recovery_capability(svc, "session-01", seq=2)
        with pytest.raises(Exception):
            svc.verify_candidate(
                session_id="session-01",
                authorization=auth,
                checkpoint_plan_mm=(100.0, 0.0, 0.0),
                checkpoint_measured_mm=(105.0, 0.0, 0.0),  # 5.0 mm drift > 1.5 mm
                sequence_number=2,
                capability=cap2,
            )

        status = svc.get_recovery_status("session-01")
        assert status.state == RecoveryState.FAILED

        # In FAILED state, subsequent calls raise RecoveryLifecycleError (latching!)
        cap3 = make_recovery_capability(svc, "session-01", seq=3)
        with pytest.raises(RecoveryLifecycleError, match="latching state FAILED"):
            svc.stage_candidate("session-01", "plan-01", cloud, sequence_number=3, capability=cap3)

        # Resetting session permits recovery restart
        svc.reset_session("session-01")
        assert svc.get_recovery_status("session-01").state == RecoveryState.IDLE
        cap4 = make_recovery_capability(svc, "session-01", seq=4)
        svc.stage_candidate("session-01", "plan-01", cloud, sequence_number=4, capability=cap4)
        assert svc.get_recovery_status("session-01").state == RecoveryState.SOLVED


class TestRecoveryServiceRoutes:
    """Tests for dispatcher route hardening and status query."""

    def test_routes_hardened_and_status_query(
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
        # Verify that initialize only registers recovery.status.get query
        from holomed.core.dispatcher import MessageDispatcher
        real_disp = MessageDispatcher()
        real_disp.initialize(runtime_context)
        svc = RecoveryService(
            dispatcher=real_disp,
            registration_service=mock_registration_service,
            drift_service=mock_drift_service,
            proximity_service=mock_proximity_service,
            navigation_service=mock_navigation_service,
            persistence_service=mock_persistence_service,
            secret_filter=secret_filter,
            logger=logger,
        )
        svc.initialize(runtime_context)
        real_disp.start()
        svc.start()

        # Check dispatcher registrations
        assert real_disp.subscription_registry.lookup_query("recovery.status.get") is not None
        assert real_disp.subscription_registry.lookup_command("recovery.stage") is None
        assert real_disp.subscription_registry.lookup_command("recovery.verify") is None
        assert real_disp.subscription_registry.lookup_command("recovery.activate") is None

        # recovery.status.get query works
        qry = create_query(
            message_name="recovery.status.get",
            source="test",
            target="recovery_service",
            payload={"session_id": "session-01"},
        )
        resp = real_disp.dispatch(qry)
        assert resp.payload["state"] == "IDLE"
