# -*- coding: utf-8 -*-
"""End-to-End Pipeline and Gateway Authorization Tests for M14 Navigation."""

from __future__ import annotations

import pytest

from holomed.core.dispatcher import MessageDispatcher
from holomed.core.exceptions import UnroutableMessageError
from holomed.execution._capability import _create_execution_capability
from holomed.navigation.constants import DEFAULT_PATIENT_TRACKER_FRAME
from holomed.navigation.exceptions import NavigationAuthorizationError
from holomed.navigation.models import NavigationState, TrackedInstrumentPose
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
    """Verify complete plan -> registration -> navigation -> evaluation pipeline under M21 capability gating."""
    nav_srv = NavigationService(
        dispatcher=message_dispatcher,
        registration_service=verified_registration_service,
        secret_filter=secret_filter,
    )
    nav_srv.initialize(runtime_context)
    message_dispatcher.start()
    nav_srv.start()

    # 1. Bind registered trajectory to session
    cap_bind = _create_execution_capability(
        service_instance_id=id(nav_srv),
        session_id="sess_nav_01",
        action="TRAJECTORY_ALIGNMENT",
        sequence_number=1,
    )
    nav_srv.bind_trajectory(
        "sess_nav_01",
        sample_plan_trajectory.trajectory_id,
        sample_plan_trajectory,
        sequence_number=1,
        capability=cap_bind,
    )
    status_init = nav_srv.get_navigation_status("sess_nav_01")
    assert status_init.state == NavigationState.IDLE

    # In M21: raw dispatcher routes are removed
    cmd_raw = create_command("navigation.pose.submit", "surgeon_console", payload={})
    with pytest.raises(UnroutableMessageError, match="navigation.pose.submit"):
        message_dispatcher.dispatch(cmd_raw)

    eval_raw = create_command("navigation.evaluate", "surgeon_console", payload={})
    with pytest.raises(UnroutableMessageError, match="navigation.evaluate"):
        message_dispatcher.dispatch(eval_raw)

    # 2. Direct calls without capability fail closed
    pose1 = TrackedInstrumentPose(
        instrument_id="pedicle_probe_01",
        session_id="sess_nav_01",
        epoch_id=1,
        sequence_number=1,
        tip_position_mm=(10.0, 20.0, 80.0),
        orientation_quaternion=(1.0, 0.0, 0.0, 0.0),
        coordinate_frame=DEFAULT_PATIENT_TRACKER_FRAME,
        confidence=0.98,
        uncertainty=0.02,
        timestamp_utc="2026-09-01T12:00:00.000Z",
    )
    with pytest.raises(NavigationAuthorizationError):
        nav_srv.submit_pose(pose1)
    with pytest.raises(NavigationAuthorizationError):
        nav_srv.evaluate("sess_nav_01")

    # 3. Direct calls with valid capability succeed
    cap1 = _create_execution_capability(
        service_instance_id=id(nav_srv),
        session_id="sess_nav_01",
        action="TOOL_NAVIGATION",
        sequence_number=1,
    )
    nav_srv.submit_pose(pose1, cap1)

    cap2 = _create_execution_capability(
        service_instance_id=id(nav_srv),
        session_id="sess_nav_01",
        action="TOOL_NAVIGATION",
        sequence_number=1,
    )
    eval_res = nav_srv.evaluate("sess_nav_01", now_utc="2026-09-01T12:00:00.040Z", capability=cap2)
    assert eval_res.state == NavigationState.ALIGNED
    assert eval_res.is_aligned is True
    assert pytest.approx(eval_res.lateral_deviation_mm, abs=1e-6) == 0.0

    # 4. Status query via dispatcher works cleanly
    status_query = create_query("navigation.status.get", "xr_client", payload={"session_id": "sess_nav_01"})
    resp_status = message_dispatcher.dispatch(status_query)
    assert resp_status.message_type.value == "RESPONSE"
    assert resp_status.payload["state"] == "ALIGNED"
    assert resp_status.payload["xr_presentation"]["tip_position_m"] == [0.01, 0.02, 0.08]

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

    # Submit with secret token in session_id to verify error redaction
    cmd = create_command(
        "navigation.evaluate",
        "surgeon_console",
        payload={"session_id": "TOP_SECRET_PATIENT_TOKEN"},
    )
    resp = srv.handle_evaluate_command(cmd)
    assert resp.message_type.value == "ERROR"
    assert "TOP_SECRET_PATIENT_TOKEN" not in resp.payload["error_message"]

    srv.stop()
