# -*- coding: utf-8 -*-
"""End-to-End Tests for M16 Registration Drift — Full Surgical Workflow Scenarios."""

from __future__ import annotations

import math
from datetime import datetime, timezone, timedelta
from unittest.mock import MagicMock

import pytest

from holomed.drift.models import DriftState
from holomed.drift.service import DriftService
from holomed.protocol.builders import create_command, create_query
from holomed.runtime.service import ServiceState

from tests.unit.drift.conftest import (
    make_landmark_definition,
    make_landmark_observation,
    make_registration_record,
)


class TestDriftE2E:
    """End-to-end integration scenarios for registration drift monitoring."""

    def test_full_surgical_drift_detection_scenario(
        self, mock_dispatcher, mock_registration_service, secret_filter, logger, runtime_context, default_landmarks
    ) -> None:
        """Full scenario:
        1. Initialize & Start DriftService.
        2. Bind 3 anatomical landmarks (Nasion, R Tragus, L Tragus) -> State = READY.
        3. Sample Nasion accurately (residual = 0.4 mm) -> State transitions to STABLE.
        4. Sample R Tragus with minor drift (residual = 1.3 mm) -> State transitions to WARNING.
        5. Tracker physically bumped! Sample L Tragus with large shift (residual = 3.2 mm) -> State transitions to DRIFT_EXCEEDED.
        6. Interlock triggered & published.
        7. Stop service cleanly.
        """
        svc = DriftService(mock_dispatcher, mock_registration_service, secret_filter, logger)
        svc.initialize(runtime_context)
        svc.start()

        # Step 2: Bind landmarks
        svc.bind_landmarks("session-01", default_landmarks)
        status = svc.get_drift_status("session-01")
        assert status.state == DriftState.READY
        assert status.verified_landmark_count == 0

        # Step 3: Sample Nasion accurately with dwell (3 samples)
        t0 = datetime(2025, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
        for i in range(3):
            t_sample = t0 + timedelta(milliseconds=i * 80)
            obs = make_landmark_observation(
                landmark_id="lm-nasion", session_id="session-01",
                observed_point_mm=(100.4, 0.0, 0.0), sequence_number=i + 1,
                timestamp_utc=t_sample.isoformat(),
            )
            rec1 = svc.evaluate(obs, now_utc=t_sample.isoformat())

        assert rec1.state == DriftState.STABLE
        assert rec1.passed is True

        status = svc.get_drift_status("session-01")
        assert status.state == DriftState.STABLE
        assert status.verified_landmark_count == 1

        # Step 4: Sample Right Tragus with minor drift (planned at (0, 100, 0), observed at (0, 101.3, 0))
        obs2 = make_landmark_observation(
            landmark_id="lm-tragus-r", session_id="session-01",
            observed_point_mm=(0.0, 101.3, 0.0), sequence_number=4,
        )
        rec2 = svc.evaluate(obs2)
        assert rec2.state == DriftState.WARNING
        assert rec2.passed is False

        status = svc.get_drift_status("session-01")
        assert status.state == DriftState.WARNING

        # Step 5: Tracker bumped! Sample Left Tragus (planned at (0, -100, 0), observed at (0, -96.8, 0) -> residual = 3.2 mm)
        obs3 = make_landmark_observation(
            landmark_id="lm-tragus-l", session_id="session-01",
            observed_point_mm=(0.0, -96.8, 0.0), sequence_number=5,
        )
        rec3 = svc.evaluate(obs3)
        assert rec3.state == DriftState.DRIFT_EXCEEDED
        assert rec3.passed is False

        status = svc.get_drift_status("session-01")
        assert status.state == DriftState.DRIFT_EXCEEDED
        assert status.is_safe is False

        # Step 6: Verify stop releases all 4 handles
        svc.stop()
        assert svc.state == ServiceState.STOPPED
        assert svc.resources.is_empty

    def test_clear_and_rebind(
        self, mock_dispatcher, mock_registration_service, secret_filter, logger, runtime_context, default_landmarks
    ) -> None:
        """Clear transient state and bind new session."""
        svc = DriftService(mock_dispatcher, mock_registration_service, secret_filter, logger)
        svc.initialize(runtime_context)
        svc.start()

        svc.bind_landmarks("session-01", default_landmarks)
        assert svc.active_sessions_count == 1

        svc.clear()
        assert svc.active_sessions_count == 0

        # Re-bind session 02
        svc.bind_landmarks("session-02", default_landmarks)
        assert svc.active_sessions_count == 1

        svc.stop()
