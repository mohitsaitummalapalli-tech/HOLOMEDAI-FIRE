# -*- coding: utf-8 -*-
"""End-to-End Pipeline and Gateway Authorization Tests for M14 Navigation."""

from __future__ import annotations

import pytest

from holomed.core.dispatcher import MessageDispatcher
from holomed.navigation.constants import DEFAULT_PATIENT_TRACKER_FRAME
from holomed.navigation.models import NavigationState
from holomed.navigation.service import NavigationService
from holomed.planning.models import TrajectoryPlan
from holomed.protocol.builders import create_command, create_query
from holomed.registration.service import RegistrationService
from holomed.runtime.context import RuntimeContext
from holomed.runtime.logging import SecretFilter


def test_e2e_complete_navigation_pipeline(
    runtime_context: RuntimeContext,
    message_dispatcher: MessageDispatcher,
    secret_filter: SecretFilter,
    verified_registration_service: RegistrationService,
    sample_plan_trajectory: TrajectoryPlan,
) -> None:
    """Verify complete plan -> registration -> navigation -> evaluation pipeline."""
    nav_srv = NavigationService(
        dispatcher=message_dispatcher,
        registration_service=verified_registration_service,
        secret_filter=secret_filter,
    )
    nav_srv.initialize(runtime_context)
    message_dispatcher.start()
    nav_srv.start()

    # 1. Bind registered trajectory to session
    # Plan entry was (0, 0, 0), target was (0, 0, 100)
    # Registration transform added translation (+10 X, +20 Y, +30 Z)
    # Physical entry is (10, 20, 30), target is (10, 20, 130)
    nav_srv.bind_trajectory("sess_nav_01", sample_plan_trajectory.trajectory_id, sample_plan_trajectory)
    status_init = nav_srv.get_navigation_status("sess_nav_01")
    assert status_init.state == NavigationState.IDLE

    # 2. Submit initial pose along trajectory at z=80 (physical: (10, 20, 80))
    submit_cmd = create_command(
        "navigation.pose.submit",
        "surgeon_console",
        payload={
            "instrument_id": "pedicle_probe_01",
            "session_id": "sess_nav_01",
            "sequence_number": 1,
            "tip_position_mm": [10.0, 20.0, 80.0],
            "orientation_quaternion": [1.0, 0.0, 0.0, 0.0],  # +Z pointing
            "coordinate_frame": DEFAULT_PATIENT_TRACKER_FRAME,
            "confidence": 0.98,
            "uncertainty": 0.02,
            "timestamp_utc": "2026-09-01T12:00:00.000Z",
        },
    )
    resp_sub = message_dispatcher.dispatch(submit_cmd)
    assert resp_sub.message_type.value == "RESPONSE"
    assert resp_sub.payload["status"] == "STORED"

    # 3. Evaluate navigation pose
    eval_cmd = create_command(
        "navigation.evaluate",
        "surgeon_console",
        payload={"session_id": "sess_nav_01", "now_utc": "2026-09-01T12:00:00.040Z"},
    )
    resp_eval = message_dispatcher.dispatch(eval_cmd)
    assert resp_eval.message_type.value == "RESPONSE"
    assert resp_eval.payload["state"] == "ALIGNED"
    assert resp_eval.payload["is_aligned"] is True
    assert pytest.approx(resp_eval.payload["lateral_deviation_mm"], abs=1e-6) == 0.0
    assert pytest.approx(resp_eval.payload["axial_distance_mm"], abs=1e-6) == 50.0  # 80 - 30 = 50 mm along trajectory

    # 4. Check status query with XR presentation coordinates in meters
    status_query = create_query("navigation.status.get", "xr_client", payload={"session_id": "sess_nav_01"})
    resp_status = message_dispatcher.dispatch(status_query)
    assert resp_status.message_type.value == "RESPONSE"
    assert resp_status.payload["state"] == "ALIGNED"
    # tip position (10, 20, 80) mm -> (0.01, 0.02, 0.08) meters
    assert resp_status.payload["xr_presentation"]["tip_position_m"] == [0.01, 0.02, 0.08]

    # 5. Drift tool laterally by 4mm (tolerance is 2mm)
    submit_drift = create_command(
        "navigation.pose.submit",
        "surgeon_console",
        payload={
            "instrument_id": "pedicle_probe_01",
            "session_id": "sess_nav_01",
            "sequence_number": 2,
            "tip_position_mm": [14.0, 20.0, 80.0],  # 4mm lateral drift in X
            "orientation_quaternion": [1.0, 0.0, 0.0, 0.0],
            "coordinate_frame": DEFAULT_PATIENT_TRACKER_FRAME,
            "confidence": 0.98,
            "uncertainty": 0.02,
            "timestamp_utc": "2026-09-01T12:00:00.100Z",
        },
    )
    message_dispatcher.dispatch(submit_drift)

    eval_drift = create_command(
        "navigation.evaluate",
        "surgeon_console",
        payload={"session_id": "sess_nav_01", "now_utc": "2026-09-01T12:00:00.140Z"},
    )
    resp_eval_drift = message_dispatcher.dispatch(eval_drift)
    assert resp_eval_drift.message_type.value == "RESPONSE"
    assert resp_eval_drift.payload["state"] == "DEVIATED"
    assert resp_eval_drift.payload["is_safe"] is False

    nav_srv.stop()


def test_secret_filter_redacts_navigation_errors(
    runtime_context: RuntimeContext,
    message_dispatcher: MessageDispatcher,
    secret_filter: SecretFilter,
) -> None:
    """Verify secrets are redacted from navigation error responses."""
    srv = NavigationService(dispatcher=message_dispatcher, secret_filter=secret_filter)
    srv.initialize(runtime_context)
    message_dispatcher.start()
    srv.start()

    # Submit with secret token in session_id
    cmd = create_command(
        "navigation.evaluate",
        "surgeon_console",
        payload={"session_id": "TOP_SECRET_PATIENT_TOKEN"},
    )
    resp = message_dispatcher.dispatch(cmd)
    assert resp.message_type.value == "ERROR"
    assert "TOP_SECRET_PATIENT_TOKEN" not in resp.payload["error_message"]

    srv.stop()
