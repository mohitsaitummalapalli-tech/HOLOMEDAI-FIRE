# -*- coding: utf-8 -*-
"""Unit and Integration Tests for NavigationService and Dispatcher Routes."""

from __future__ import annotations

import pytest

from holomed.core.dispatcher import MessageDispatcher
from holomed.core.exceptions import UnroutableMessageError
from holomed.execution._capability import _create_execution_capability
from holomed.navigation.constants import (
    DEFAULT_PATIENT_TRACKER_FRAME,
    MAX_ACTIVE_NAVIGATION_SESSIONS,
    MAX_TRACKED_INSTRUMENTS_PER_SESSION,
)
from holomed.navigation.exceptions import (
    NavigationAuthorizationError,
    NavigationCapacityError,
    NavigationLifecycleError,
    NavigationRegistrationMismatchError,
    NavigationSequenceError,
)
from holomed.navigation.models import (
    NavigationState,
    TrackedInstrumentPose,
)
from holomed.navigation.service import NavigationService
from holomed.planning.models import TrajectoryPlan
from holomed.protocol.builders import create_command, create_query
from holomed.registration.models import RegistrationState, RegistrationStatusRecord
from holomed.registration.service import RegistrationService
from holomed.runtime.context import RuntimeContext
from holomed.runtime.logging import SecretFilter
from holomed.runtime.models import HealthStatus
from holomed.runtime.service import ServiceState


def test_navigation_service_lifecycle_and_four_structural_handles(
    runtime_context: RuntimeContext,
    secret_filter: SecretFilter,
) -> None:
    """Verify NavigationService lifecycle and acquisition/release of 4 handles."""
    srv = NavigationService(secret_filter=secret_filter)
    assert srv.state == ServiceState.UNINITIALIZED
    assert srv.health().status == HealthStatus.UNHEALTHY

    srv.initialize(runtime_context)
    assert srv.state == ServiceState.INITIALIZED
    assert len(srv.resources.outstanding_handles) == 4

    srv.start()
    assert srv.state == ServiceState.STARTED
    assert srv.health().status == HealthStatus.HEALTHY

    srv.stop()
    assert srv.state == ServiceState.STOPPED
    assert len(srv.resources.outstanding_handles) == 0


def test_navigation_service_reentrancy_guard(
    runtime_context: RuntimeContext,
    secret_filter: SecretFilter,
    sample_plan_trajectory: TrajectoryPlan,
) -> None:
    """Verify synchronous transaction guard rejects reentrant invocations."""
    srv = NavigationService(secret_filter=secret_filter)
    srv.initialize(runtime_context)
    srv.start()

    srv._in_transaction = True
    with pytest.raises(NavigationLifecycleError):
        srv.bind_trajectory("s1", "t1", sample_plan_trajectory)

    with pytest.raises(NavigationLifecycleError):
        srv.stop()

    srv._in_transaction = False
    srv.stop()


def test_navigation_service_requires_verified_m13_registration(
    runtime_context: RuntimeContext,
    secret_filter: SecretFilter,
    sample_plan_trajectory: TrajectoryPlan,
) -> None:
    """Verify bind_trajectory fails closed when M13 registration is missing or unverified."""
    # Registration service with no verified sessions
    reg_srv = RegistrationService(secret_filter=secret_filter)
    reg_srv.initialize(runtime_context)
    reg_srv.start()

    srv = NavigationService(registration_service=reg_srv, secret_filter=secret_filter)
    srv.initialize(runtime_context)
    srv.start()

    with pytest.raises(NavigationRegistrationMismatchError) as exc:
        srv.bind_trajectory("sess_unverified", "traj_01", sample_plan_trajectory)
    assert "not have a verified M13 registration" in str(exc.value)

    srv.stop()
    reg_srv.stop()


def test_navigation_service_fails_closed_if_registration_invalidated(
    runtime_context: RuntimeContext,
    secret_filter: SecretFilter,
    verified_registration_service: RegistrationService,
    sample_plan_trajectory: TrajectoryPlan,
) -> None:
    """Verify evaluation fails closed to INTERLOCKED if M13 registration becomes invalidated."""
    srv = NavigationService(
        registration_service=verified_registration_service,
        secret_filter=secret_filter,
    )
    srv.initialize(runtime_context)
    srv.start()

    # 1. Bind trajectory to verified session
    srv.bind_trajectory("sess_nav_01", "t1", sample_plan_trajectory)

    # 2. Submit initial pose
    pose = TrackedInstrumentPose(
        instrument_id="probe_01",
        session_id="sess_nav_01",
        epoch_id=1,
        sequence_number=1,
        tip_position_mm=(10.0, 20.0, 50.0),  # In patient tracker space
        orientation_quaternion=(1.0, 0.0, 0.0, 0.0),
        coordinate_frame=DEFAULT_PATIENT_TRACKER_FRAME,
        confidence=0.95,
        uncertainty=0.05,
        timestamp_utc="2026-09-01T12:00:00.000Z",
    )
    # Direct call without capability fails
    with pytest.raises(NavigationAuthorizationError):
        srv.submit_pose(pose)

    cap1 = _create_execution_capability(id(srv), "sess_nav_01", "TOOL_NAVIGATION", 1)
    srv.submit_pose(pose, cap1)

    # 3. Invalidate registration in M13 (e.g. drift error > 1.5mm)
    reg = verified_registration_service.get_registration("sess_nav_01")
    invalid_reg = RegistrationStatusRecord(
        session_id=reg.session_id,
        plan_id=reg.plan_id,
        epoch_id=reg.epoch_id,
        state=RegistrationState.INVALIDATED,
        transform=None,
        quality_report=reg.quality_report,
        locked=False,
    )
    verified_registration_service._registrations["sess_nav_01"] = invalid_reg

    # 4. Evaluating now must raise NavigationRegistrationMismatchError and transition to INTERLOCKED!
    cap2 = _create_execution_capability(id(srv), "sess_nav_01", "TOOL_NAVIGATION", 1)
    with pytest.raises(NavigationRegistrationMismatchError):
        srv.evaluate("sess_nav_01", now_utc="2026-09-01T12:00:00.050Z", capability=cap2)

    status = srv.get_navigation_status("sess_nav_01")
    assert status.state == NavigationState.INTERLOCKED
    assert status.is_guidance_active is False

    srv.stop()


def test_navigation_service_sequence_monotonicity_rejection(
    runtime_context: RuntimeContext,
    secret_filter: SecretFilter,
    verified_registration_service: RegistrationService,
    sample_plan_trajectory: TrajectoryPlan,
) -> None:
    """Verify non-monotonic or duplicate sequence numbers are rejected without advancing state."""
    srv = NavigationService(
        registration_service=verified_registration_service,
        secret_filter=secret_filter,
    )
    srv.initialize(runtime_context)
    srv.start()

    srv.bind_trajectory("sess_nav_01", "t1", sample_plan_trajectory)

    pose1 = TrackedInstrumentPose(
        instrument_id="probe_01",
        session_id="sess_nav_01",
        epoch_id=1,
        sequence_number=10,
        tip_position_mm=(10.0, 20.0, 50.0),
        orientation_quaternion=(1.0, 0.0, 0.0, 0.0),
        coordinate_frame=DEFAULT_PATIENT_TRACKER_FRAME,
        confidence=0.95,
        uncertainty=0.05,
        timestamp_utc="2026-09-01T12:00:00.000Z",
    )
    cap1 = _create_execution_capability(id(srv), "sess_nav_01", "TOOL_NAVIGATION", 10)
    srv.submit_pose(pose1, cap1)

    # Replay sequence 10 -> rejected!
    cap_replay = _create_execution_capability(id(srv), "sess_nav_01", "TOOL_NAVIGATION", 10)
    with pytest.raises(NavigationSequenceError) as exc:
        srv.submit_pose(pose1, cap_replay)
    assert "Non-monotonic sequence number" in str(exc.value)

    # Older sequence 9 -> rejected!
    pose_old = TrackedInstrumentPose(
        instrument_id="probe_01",
        session_id="sess_nav_01",
        epoch_id=1,
        sequence_number=9,
        tip_position_mm=(10.0, 20.0, 50.0),
        orientation_quaternion=(1.0, 0.0, 0.0, 0.0),
        coordinate_frame=DEFAULT_PATIENT_TRACKER_FRAME,
        confidence=0.95,
        uncertainty=0.05,
        timestamp_utc="2026-09-01T12:00:00.000Z",
    )
    cap_old = _create_execution_capability(id(srv), "sess_nav_01", "TOOL_NAVIGATION", 9)
    with pytest.raises(NavigationSequenceError):
        srv.submit_pose(pose_old, cap_old)

    srv.stop()


def test_navigation_service_dispatcher_routes(
    runtime_context: RuntimeContext,
    message_dispatcher: MessageDispatcher,
    secret_filter: SecretFilter,
    verified_registration_service: RegistrationService,
    sample_plan_trajectory: TrajectoryPlan,
) -> None:
    """Verify navigation.pose.submit, navigation.evaluate, and navigation.status.get dispatcher routes."""
    srv = NavigationService(
        dispatcher=message_dispatcher,
        registration_service=verified_registration_service,
        secret_filter=secret_filter,
    )
    srv.initialize(runtime_context)
    message_dispatcher.start()
    srv.start()

    srv.bind_trajectory("sess_nav_01", "t1", sample_plan_trajectory)

    # 1. navigation.pose.submit is removed from dispatcher in M21 -> UnroutableMessageError
    pose_cmd = create_command(
        "navigation.pose.submit",
        "surgeon_console",
        payload={
            "instrument_id": "probe_01",
            "session_id": "sess_nav_01",
            "sequence_number": 1,
            "tip_position_mm": [10.0, 20.0, 50.0],
            "orientation_quaternion": [1.0, 0.0, 0.0, 0.0],
            "coordinate_frame": DEFAULT_PATIENT_TRACKER_FRAME,
            "confidence": 0.95,
            "uncertainty": 0.05,
            "timestamp_utc": "2026-09-01T12:00:00.000Z",
        },
    )
    with pytest.raises(UnroutableMessageError, match="navigation.pose.submit"):
        message_dispatcher.dispatch(pose_cmd)

    # 2. navigation.evaluate is removed from dispatcher in M21 -> UnroutableMessageError
    eval_cmd = create_command(
        "navigation.evaluate",
        "surgeon_console",
        payload={
            "session_id": "sess_nav_01",
            "now_utc": "2026-09-01T12:00:00.050Z",
        },
    )
    with pytest.raises(UnroutableMessageError, match="navigation.evaluate"):
        message_dispatcher.dispatch(eval_cmd)

    # 3. navigation.status.get remains active
    status_query = create_query(
        "navigation.status.get",
        "xr_client",
        payload={"session_id": "sess_nav_01"},
    )
    resp_status = message_dispatcher.dispatch(status_query)
    assert resp_status.message_type.value == "RESPONSE"
    assert resp_status.payload["state"] == "IDLE"

    srv.stop()


def test_navigation_service_capacity_limits(
    runtime_context: RuntimeContext,
    secret_filter: SecretFilter,
    verified_registration_service: RegistrationService,
    sample_plan_trajectory: TrajectoryPlan,
) -> None:
    """Verify session and instrument capacity limits."""
    srv = NavigationService(
        registration_service=verified_registration_service,
        secret_filter=secret_filter,
    )
    srv.initialize(runtime_context)
    srv.start()

    srv.bind_trajectory("sess_nav_01", "t1", sample_plan_trajectory)

    # Max 8 instruments per session
    for i in range(MAX_TRACKED_INSTRUMENTS_PER_SESSION):
        p = TrackedInstrumentPose(
            instrument_id=f"inst_{i:02d}",
            session_id="sess_nav_01",
            epoch_id=1,
            sequence_number=1,
            tip_position_mm=(0.0, 0.0, 50.0),
            orientation_quaternion=(1.0, 0.0, 0.0, 0.0),
            coordinate_frame=DEFAULT_PATIENT_TRACKER_FRAME,
            confidence=0.9,
            uncertainty=0.1,
            timestamp_utc="2026-09-01T12:00:00Z",
        )
        cap = _create_execution_capability(id(srv), "sess_nav_01", "TOOL_NAVIGATION", 1)
        srv.submit_pose(p, cap)

    # 9th instrument exceeds capacity
    p_overflow = TrackedInstrumentPose(
        instrument_id="inst_overflow",
        session_id="sess_nav_01",
        epoch_id=1,
        sequence_number=1,
        tip_position_mm=(0.0, 0.0, 50.0),
        orientation_quaternion=(1.0, 0.0, 0.0, 0.0),
        coordinate_frame=DEFAULT_PATIENT_TRACKER_FRAME,
        confidence=0.9,
        uncertainty=0.1,
        timestamp_utc="2026-09-01T12:00:00Z",
    )
    cap_over = _create_execution_capability(id(srv), "sess_nav_01", "TOOL_NAVIGATION", 1)
    with pytest.raises(NavigationCapacityError):
        srv.submit_pose(p_overflow, cap_over)

    srv.stop()
