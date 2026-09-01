"""Unit and lifecycle tests for VisionService."""

from __future__ import annotations

import pytest

from holomed.core.dispatcher import MessageDispatcher
from holomed.protocol.builders import create_command, create_query
from holomed.runtime.models import HealthStatus
from holomed.runtime.service import ServiceState
from holomed.vision.exceptions import (
    VisionLifecycleError,
    VisionValidationError,
)
from holomed.vision.service import STRUCTURAL_RESOURCE_IDS, VisionService
from tests.unit.vision.conftest import make_test_frame


def test_vision_service_lifecycle_and_resource_ownership(
    test_runtime_context, device_registry_with_camera
) -> None:
    """Verifies standard IService transitions and exact 4 structural resource handles."""
    _, dm = device_registry_with_camera
    service = VisionService(device_manager=dm)
    assert service.name == "vision_service"
    assert service.dependencies == ("device_manager",)
    assert service._state == ServiceState.UNINITIALIZED

    # Cannot get resources before initialize
    with pytest.raises(VisionLifecycleError):
        _ = service.resources

    # Initialize
    service.initialize(test_runtime_context)
    assert service._state == ServiceState.INITIALIZED
    assert len(service.resources.outstanding_handles) == 4
    handles = {h.resource_id for h in service.resources.outstanding_handles}
    assert handles == set(STRUCTURAL_RESOURCE_IDS)

    # Start
    service.start()
    assert service._state == ServiceState.STARTED

    # Double start raises
    with pytest.raises(VisionLifecycleError):
        service.start()

    # Health check in STARTED
    health = service.health()
    assert health.status == HealthStatus.HEALTHY
    assert "0 frames" in health.message

    # Stop
    service.stop()
    assert service._state == ServiceState.STOPPED
    assert service.resources.is_empty is True

    # Idempotent stop
    service.stop()
    assert service._state == ServiceState.STOPPED


def test_vision_service_stop_in_uninitialized_is_safe() -> None:
    """Calling stop in UNINITIALIZED is safe no-op."""
    service = VisionService()
    assert service._state == ServiceState.UNINITIALIZED
    service.stop()
    assert service._state == ServiceState.UNINITIALIZED


def test_vision_service_ingest_frame_and_physical_id_validation(
    test_runtime_context, device_registry_with_camera
) -> None:
    """Assert frame ingestion validates physical_id against DeviceRegistry."""
    _, dm = device_registry_with_camera
    service = VisionService(device_manager=dm)
    service.initialize(test_runtime_context)
    service.start()

    # Valid physical_id (usb://port1) succeeds
    desc_valid, data_valid = make_test_frame(device_id="cam_01", physical_id="usb://port1")
    result = service.ingest_frame(desc_valid, data_valid)
    assert result.frame_id == desc_valid.frame_id
    assert service._processed_frames == 1

    # Stale/mismatched physical_id (usb://hacked) raises VisionValidationError
    desc_invalid, data_invalid = make_test_frame(device_id="cam_01", physical_id="usb://hacked")
    with pytest.raises(VisionValidationError):
        service.ingest_frame(desc_invalid, data_invalid)

    # Unregistered device raises VisionValidationError
    desc_unknown, data_unknown = make_test_frame(device_id="unknown_sensor", physical_id="usb://port1")
    with pytest.raises(VisionValidationError):
        service.ingest_frame(desc_unknown, data_unknown)

    service.stop()


def test_vision_service_dispatcher_routes(
    test_runtime_context, device_registry_with_camera
) -> None:
    """Verifies static registration and response generation for all vision dispatcher routes."""
    _, dm = device_registry_with_camera
    dispatcher = MessageDispatcher()
    dispatcher.initialize(test_runtime_context)

    service = VisionService(device_manager=dm, dispatcher=dispatcher)
    service.initialize(test_runtime_context)
    service.start()
    dispatcher.start()

    # Query status
    q_status = create_query("vision.pipeline.status", "client", payload={})
    res_status = service.handle_status_query(q_status)
    assert res_status.payload["service_name"] == "vision_service"
    assert res_status.payload["state"] == "STARTED"

    # Query audit
    q_audit = create_query("vision.pipeline.audit", "client", payload={})
    res_audit = service.handle_audit_query(q_audit)
    assert res_audit.payload["is_consistent"] is True

    # Query tracks
    q_tracks = create_query("vision.tracker.tracks", "client", payload={})
    res_tracks = service.handle_tracks_query(q_tracks)
    assert res_tracks.payload["active_tracks_count"] == 0

    # Command reset
    c_reset = create_command("vision.pipeline.reset", "client", payload={})
    res_reset = service.handle_reset_command(c_reset)
    assert res_reset.payload["reset_completed"] is True

    service.stop()
