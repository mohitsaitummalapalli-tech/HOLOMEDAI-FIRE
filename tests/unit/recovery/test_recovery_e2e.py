# -*- coding: utf-8 -*-
"""End-to-End Tests for M17 Spatial Recovery — Full Intraoperative Scenarios."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from holomed.recovery.models import RecoveryState
from holomed.recovery.service import RecoveryService
from holomed.runtime.service import ServiceState

from tests.unit.recovery.conftest import (
    make_fiducial_cloud,
    make_recovery_authorization,
)


class TestRecoveryE2E:
    """End-to-end integration scenarios for intraoperative spatial re-registration."""

    def test_full_intraoperative_recovery_workflow(
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
        """Full surgical recovery scenario:
        1. Initialize & start RecoveryService.
        2. Stage candidate fiducials (solved & FRE validated).
        3. Verify candidate with physical checkpoint touch & operator authorization.
        4. Activate recovery (M13 update, M16/M15/M14 rebinds, consistency check).
        5. Verify state is ACTIVATED and revision = 1.
        6. Clean service stop.
        """
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
        assert svc.state == ServiceState.STARTED

        mock_dispatcher.dispatch.reset_mock()

        # Step 1: Stage candidate fiducials
        cloud = make_fiducial_cloud(offset_mm=(1.0, 2.0, 0.5), jitter_mm=0.1)
        cap1 = make_recovery_capability(svc, "session-01", seq=1)
        candidate = svc.stage_candidate("session-01", "plan-01", cloud, sequence_number=1, capability=cap1)
        assert candidate.quality_report.fre_rms_mm <= 1.5
        assert svc.get_recovery_status("session-01").state == RecoveryState.SOLVED

        # Step 2: Verify candidate with physical checkpoint
        auth = make_recovery_authorization(
            operator_id="dr_chen_lead",
            rationale="Intraoperative head frame readjustment",
            sequence_number=1,
            authorization_reference="REF-OR4-20250101",
        )
        cap2 = make_recovery_capability(svc, "session-01", seq=2)
        snapshot = svc.verify_candidate(
            session_id="session-01",
            authorization=auth,
            checkpoint_plan_mm=(100.0, 0.0, 0.0),
            checkpoint_measured_mm=(101.2, 2.0, 0.5),  # drift = 0.2 mm <= 1.5 mm
            sequence_number=2,
            capability=cap2,
        )
        assert snapshot.passed is True
        assert snapshot.measured_drift_error_mm <= 1.5
        assert svc.get_recovery_status("session-01").state == RecoveryState.VERIFIED

        # Step 3: Activate recovery
        cap3 = make_recovery_capability(svc, "session-01", seq=3)
        status_rec = svc.activate_recovery("session-01", sequence_number=3, capability=cap3)
        assert status_rec.state == RecoveryState.ACTIVATED
        assert status_rec.registration_revision == 1

        # Verify event stream
        event_names = [call[0][0].message_name for call in mock_dispatcher.dispatch.call_args_list]
        assert "recovery.started" in event_names
        assert "recovery.candidate.verified" in event_names
        assert "recovery.activation.started" in event_names
        assert "recovery.spatial.activated" in event_names

        # Step 4: Clean teardown
        svc.stop()
        assert svc.state == ServiceState.STOPPED
        assert svc.resources.is_empty
