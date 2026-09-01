# -*- coding: utf-8 -*-
"""Unit tests for XRService Lifecycle, Dispatcher Routes, and Operations."""

from __future__ import annotations

import pytest

from holomed.core.dispatcher import MessageDispatcher
from holomed.devices.manager import DeviceManager
from holomed.protocol.builders import create_command, create_query
from holomed.runtime.context import RuntimeContext
from holomed.runtime.service import ServiceState
from holomed.xr.exceptions import (
    XREpochMismatchError,
    XRLifecycleError,
    XRShutdownError,
)
from holomed.xr.models import (
    CameraViewport,
    VisualNode,
)
from holomed.xr.service import STRUCTURAL_RESOURCE_IDS, XRService


def test_xr_service_lifecycle_full_flow(
    runtime_context: RuntimeContext,
    device_manager: DeviceManager,
    message_dispatcher: MessageDispatcher,
) -> None:
    """Verify UNINITIALIZED -> INITIALIZED -> STARTED -> STOPPED lifecycle with 5 handles."""
    service = XRService(device_manager=device_manager, dispatcher=message_dispatcher)
    assert service.state == ServiceState.UNINITIALIZED

    # Stop before initialize is safe no-op
    service.stop()
    assert service.state == ServiceState.UNINITIALIZED

    # Initialize acquires exactly 5 structural handles
    service.initialize(runtime_context)
    assert service.state == ServiceState.INITIALIZED
    assert len(service.resources.outstanding_handles) == len(STRUCTURAL_RESOURCE_IDS)

    # Start service (acquires zero new handles)
    service.start()
    assert service.state == ServiceState.STARTED
    assert len(service.resources.outstanding_handles) == 5

    # Double start raises XRLifecycleError
    with pytest.raises(XRLifecycleError):
        service.start()

    # Health check
    h = service.health()
    assert h.status.name == "HEALTHY"

    # Stop releases all 5 handles
    service.stop()
    assert service.state == ServiceState.STOPPED
    assert service.resources.is_empty is True

    # Idempotent stop
    service.stop()
    assert service.state == ServiceState.STOPPED


def test_xr_service_teardown_failure_handling(runtime_context: RuntimeContext) -> None:
    """Verify resource release failure transitions service to FAILED and raises XRShutdownError."""
    service = XRService()
    service.initialize(runtime_context)
    service.start()

    def failing_release(res_id: str) -> None:
        raise RuntimeError(f"Cleanup failed for {res_id}")

    service.resources.release = failing_release  # type: ignore

    with pytest.raises(XRShutdownError) as exc_info:
        service.stop()

    assert service.state == ServiceState.FAILED
    assert len(exc_info.value.failures) == 5


def test_xr_service_public_operations(
    runtime_context: RuntimeContext,
    sample_visual_node: VisualNode,
    sample_viewport: CameraViewport,
) -> None:
    """Verify add_visual_node, configure_viewport, generate_frame, reset."""
    service = XRService()
    service.initialize(runtime_context)
    service.start()

    # Add visual node & viewport
    service.add_visual_node(sample_visual_node)
    service.configure_viewport(sample_viewport)

    retrieved = service.get_visual_node(sample_visual_node.node_id)
    assert retrieved.node_id == sample_visual_node.node_id

    # Generate frame
    desc = service.generate_frame()
    assert desc.frame_sequence == 1
    assert len(desc.nodes) == 1
    assert len(desc.viewports) == 1

    # Reset
    service.reset(epoch_id=1)
    assert service.scene_graph.node_count == 0

    with pytest.raises(XREpochMismatchError):
        service.reset(epoch_id=999)

    service.stop()


def test_xr_service_dispatcher_routes(
    runtime_context: RuntimeContext,
    device_manager: DeviceManager,
    message_dispatcher: MessageDispatcher,
    sample_visual_node: VisualNode,
    sample_viewport: CameraViewport,
) -> None:
    """Verify dispatcher query and command routes."""
    service = XRService(device_manager=device_manager, dispatcher=message_dispatcher)
    service.initialize(runtime_context)
    service.start()
    message_dispatcher.start()

    service.add_visual_node(sample_visual_node)
    service.configure_viewport(sample_viewport)

    # 1. xr.status query
    q_status = create_query("xr.status", "client", target="xr_service", payload={})
    res_status = service.handle_status_query(q_status)
    assert res_status.payload["service_name"] == "xr_service"
    assert res_status.payload["node_count"] == 1

    # 2. xr.node query
    q_node = create_query(
        "xr.node",
        "client",
        target="xr_service",
        payload={"node_id": sample_visual_node.node_id},
    )
    res_node = service.handle_node_query(q_node)
    assert res_node.payload["node_id"] == sample_visual_node.node_id

    # 3. xr.viewport.status query
    q_vp = create_query("xr.viewport.status", "client", target="xr_service", payload={})
    res_vp = service.handle_viewport_query(q_vp)
    assert len(res_vp.payload["viewports"]) == 1

    # 4. xr.frame query
    q_frame = create_query("xr.frame", "client", target="xr_service", payload={})
    res_frame = service.handle_frame_query(q_frame)
    assert res_frame.payload["frame_sequence"] == 1

    # 5. xr.reset command
    c_rst = create_command("xr.reset", "client", target="xr_service", payload={"epoch_id": 1})
    res_rst = service.handle_reset_command(c_rst)
    assert res_rst.payload["reset_completed"] is True

    service.stop()
