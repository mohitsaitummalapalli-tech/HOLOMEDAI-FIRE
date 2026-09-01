# -*- coding: utf-8 -*-
"""Unit tests for GestureService lifecycle, dispatcher routes, and security invariants."""

from __future__ import annotations

import pytest

from holomed.core.dispatcher import MessageDispatcher
from holomed.devices.manager import DeviceManager
from holomed.gesture.exceptions import (
    GestureEpochMismatchError,
    GestureLifecycleError,
    GestureSequenceError,
    GestureShutdownError,
    GestureValidationError,
)
from holomed.gesture.service import STRUCTURAL_RESOURCE_IDS, GestureService
from holomed.protocol.builders import create_command, create_query
from holomed.runtime.context import RuntimeContext
from holomed.runtime.service import ServiceState
from holomed.vision.models import SpatialLandmark
from tests.unit.gesture.conftest import make_observation


def test_service_lifecycle_full_flow(
    runtime_context: RuntimeContext,
    device_manager: DeviceManager,
    message_dispatcher: MessageDispatcher,
) -> None:
    """Verify UNINITIALIZED -> INITIALIZED -> STARTED -> STOPPED flow and 4 resources."""
    service = GestureService(
        device_manager=device_manager,
        dispatcher=message_dispatcher,
    )
    assert service.state == ServiceState.UNINITIALIZED

    # Stop before initialize is safe no-op
    service.stop()
    assert service.state == ServiceState.UNINITIALIZED

    # Initialize acquires 4 structural resources
    service.initialize(runtime_context)
    assert service.state == ServiceState.INITIALIZED
    assert len(service.resources.outstanding_handles) == 4
    for res_id in STRUCTURAL_RESOURCE_IDS:
        assert any(h.resource_id == res_id for h in service.resources.outstanding_handles)

    # Start
    service.start()
    assert service.state == ServiceState.STARTED

    # Double start raises GestureLifecycleError
    with pytest.raises(GestureLifecycleError, match="Cannot start"):
        service.start()

    # Health check in STARTED
    sh = service.health()
    assert sh.status.value in ("HEALTHY", "DEGRADED")

    # Stop releases all 4 resources
    service.stop()
    assert service.state == ServiceState.STOPPED
    assert len(service.resources.outstanding_handles) == 0

    # Stop when already STOPPED is safe no-op
    service.stop()
    assert service.state == ServiceState.STOPPED


def test_service_sequence_monotonicity(
    runtime_context: RuntimeContext,
    device_manager: DeviceManager,
    open_hand_landmarks: tuple[SpatialLandmark, ...],
) -> None:
    """Verify strictly increasing sequence numbers per session (D200 / Section 34)."""
    service = GestureService(device_manager=device_manager)
    service.initialize(runtime_context)
    service.start()

    # Seq 10: OK
    obs1 = make_observation(open_hand_landmarks, sequence_number=10)
    service.ingest_observation(obs1)

    # Seq 10 (duplicate): must raise GestureSequenceError
    obs_dup = make_observation(open_hand_landmarks, sequence_number=10)
    with pytest.raises(GestureSequenceError, match="Non-monotonic sequence number"):
        service.ingest_observation(obs_dup)

    # Seq 9 (decrease): must raise GestureSequenceError
    obs_dec = make_observation(open_hand_landmarks, sequence_number=9)
    with pytest.raises(GestureSequenceError, match="Non-monotonic sequence number"):
        service.ingest_observation(obs_dec)

    # Seq 11 (strictly greater): OK
    obs11 = make_observation(open_hand_landmarks, sequence_number=11)
    service.ingest_observation(obs11)


def test_service_epoch_mismatch(
    runtime_context: RuntimeContext,
    device_manager: DeviceManager,
    open_hand_landmarks: tuple[SpatialLandmark, ...],
) -> None:
    """Verify observation with mismatched epoch raises GestureEpochMismatchError."""
    service = GestureService(device_manager=device_manager)
    service.initialize(runtime_context)  # epoch_id = 1
    service.start()

    obs_wrong_epoch = make_observation(open_hand_landmarks, epoch_id=99)
    with pytest.raises(GestureEpochMismatchError, match="does not match service epoch"):
        service.ingest_observation(obs_wrong_epoch)


def test_service_device_identity_validation(
    runtime_context: RuntimeContext,
    device_manager: DeviceManager,
    open_hand_landmarks: tuple[SpatialLandmark, ...],
) -> None:
    """Verify unregistered device_id or physical_id mismatch raises GestureValidationError."""
    service = GestureService(device_manager=device_manager)
    service.initialize(runtime_context)
    service.start()

    # Unknown device
    obs_unknown = make_observation(open_hand_landmarks)
    object.__setattr__(obs_unknown, "device_id", "cam_unknown_999")
    with pytest.raises(GestureValidationError, match="not registered in DeviceRegistry"):
        service.ingest_observation(obs_unknown)

    # Physical ID mismatch
    obs_phys_mismatch = make_observation(open_hand_landmarks)
    object.__setattr__(obs_phys_mismatch, "physical_id", "phys_wrong_id")
    with pytest.raises(GestureValidationError, match="Physical ID mismatch"):
        service.ingest_observation(obs_phys_mismatch)


def test_service_reentrancy_guard(
    runtime_context: RuntimeContext,
    device_manager: DeviceManager,
    open_hand_landmarks: tuple[SpatialLandmark, ...],
) -> None:
    """Verify reentrant invocation of ingest_observation raises GestureLifecycleError."""
    service = GestureService(device_manager=device_manager)
    service.initialize(runtime_context)
    service.start()

    obs = make_observation(open_hand_landmarks)

    # Simulate transaction active
    service._in_transaction = True
    try:
        with pytest.raises(GestureLifecycleError, match="Reentrant call"):
            service.ingest_observation(obs)
    finally:
        service._in_transaction = False


def test_service_dispatcher_routes(
    runtime_context: RuntimeContext,
    device_manager: DeviceManager,
    message_dispatcher: MessageDispatcher,
    pointing_landmarks: tuple[SpatialLandmark, ...],
) -> None:
    """Verify static dispatcher queries and commands."""
    service = GestureService(
        device_manager=device_manager,
        dispatcher=message_dispatcher,
    )
    service.initialize(runtime_context)
    service.start()
    message_dispatcher.start()

    # Ingest one frame
    obs = make_observation(pointing_landmarks, sequence_number=1)
    service.ingest_observation(obs)

    # 1. gesture.pipeline.status
    q_status = create_query("gesture.pipeline.status", source="test", target="gesture_service", payload={})
    resp_status = message_dispatcher.dispatch(q_status)
    assert resp_status.payload["service_name"] == "gesture_service"
    assert resp_status.payload["tracked_hands"] == 1

    # 2. gesture.pipeline.audit
    q_audit = create_query("gesture.pipeline.audit", source="test", target="gesture_service", payload={})
    resp_audit = message_dispatcher.dispatch(q_audit)
    assert resp_audit.payload["is_consistent"] is True

    # 3. gesture.tracks
    q_tracks = create_query("gesture.tracks", source="test", target="gesture_service", payload={})
    resp_tracks = message_dispatcher.dispatch(q_tracks)
    assert resp_tracks.payload["active_tracks_count"] == 1

    # 4. gesture.pipeline.reset
    cmd_reset = create_command(
        "gesture.pipeline.reset",
        source="test",
        target="gesture_service",
        payload={"epoch_id": runtime_context.epoch_id},
    )
    resp_reset = message_dispatcher.dispatch(cmd_reset)
    assert resp_reset.payload["reset_completed"] is True


def test_service_teardown_failure_handling(
    runtime_context: RuntimeContext,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verify resource release failure sets FAILED and raises GestureShutdownError with tuple failures."""
    service = GestureService()
    service.initialize(runtime_context)
    service.start()

    # Monkeypatch release to fail
    def failing_release(res_id: str) -> None:
        raise RuntimeError(f"Simulated cleanup failure on {res_id}")

    monkeypatch.setattr(service.resources, "release", failing_release)

    with pytest.raises(GestureShutdownError) as exc_info:
        service.stop()

    assert service.state == ServiceState.FAILED
    assert isinstance(exc_info.value.failures, tuple)
    assert len(exc_info.value.failures) == 4
