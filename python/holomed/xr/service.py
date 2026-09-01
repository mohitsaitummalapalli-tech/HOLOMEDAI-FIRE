# -*- coding: utf-8 -*-
"""Master Headless XR Visualization & Presentation Service for M06."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Mapping, Optional

from holomed.core.dispatcher import MessageDispatcher
from holomed.devices.manager import DeviceManager
from holomed.devices.models import DeviceShutdownFailureRecord
from holomed.protocol.builders import (
    create_error_response,
    create_event,
    create_response,
)
from holomed.protocol.models import MessageEnvelope
from holomed.runtime.context import RuntimeContext
from holomed.runtime.logging import SecretFilter, StructuredLogger
from holomed.runtime.models import (
    HealthStatus,
    OwnedResourceSet,
    ResourceStatus,
    ServiceHealth,
)
from holomed.runtime.service import IService, ServiceState
from holomed.xr.camera import ViewportManager
from holomed.xr.events import RecordingXREventSink
from holomed.xr.exceptions import (
    XRCapacityError,
    XREpochMismatchError,
    XRHierarchyError,
    XRLifecycleError,
    XRResourceIntegrityError,
    XRShutdownError,
    XRValidationError,
)
from holomed.xr.models import (
    AnnotationItem,
    CameraViewport,
    InteractionRay,
    PresentationDescription,
    VisualNode,
)
from holomed.xr.presentation import PresentationEngine
from holomed.xr.renderer import DefaultExternalRendererAdapter, IExternalRendererAdapter
from holomed.xr.scene import SceneGraph
from holomed.xr.serialization import serialize_xr_payload

STRUCTURAL_RESOURCE_IDS: tuple[str, ...] = (
    "xr.scenegraph",
    "xr.viewport",
    "xr.presentation",
    "xr.renderer",
    "xr.metrics",
)


class XRService(IService):
    """Headless XR Visualization & Spatial Presentation Service implementing IService."""

    def __init__(
        self,
        device_manager: Optional[DeviceManager] = None,
        dispatcher: Optional[MessageDispatcher] = None,
        renderer_adapter: Optional[IExternalRendererAdapter] = None,
        secret_filter: Optional[SecretFilter] = None,
        logger: Optional[StructuredLogger] = None,
    ) -> None:
        self._device_manager = device_manager
        self._dispatcher = dispatcher
        self._renderer_adapter = renderer_adapter or DefaultExternalRendererAdapter()
        self._secret_filter = secret_filter
        self._logger = logger or StructuredLogger("xr_service", secret_filter=secret_filter)

        self._state: ServiceState = ServiceState.UNINITIALIZED
        self._context: Optional[RuntimeContext] = None
        self._resources: Optional[OwnedResourceSet] = None
        self._epoch_id: int = 0

        # Subsystems
        self._scene_graph: Optional[SceneGraph] = None
        self._viewport_manager: Optional[ViewportManager] = None
        self._presentation_engine: Optional[PresentationEngine] = None
        self._event_sink: Optional[RecordingXREventSink] = None

        # Metrics
        self._frame_sequence: int = 0
        self._in_transaction: bool = False

    @property
    def name(self) -> str:
        return "xr_service"

    @property
    def dependencies(self) -> tuple[str, ...]:
        return (
            "device_manager",
            "anatomy_service",
        )

    @property
    def state(self) -> ServiceState:
        return self._state

    @property
    def resources(self) -> OwnedResourceSet:
        if self._resources is None:
            raise XRLifecycleError("Service has not been initialized; resources unavailable")
        return self._resources

    @property
    def scene_graph(self) -> Optional[SceneGraph]:
        return self._scene_graph

    @property
    def viewport_manager(self) -> Optional[ViewportManager]:
        return self._viewport_manager

    @property
    def presentation_engine(self) -> Optional[PresentationEngine]:
        return self._presentation_engine

    @property
    def event_sink(self) -> Optional[RecordingXREventSink]:
        return self._event_sink

    def initialize(self, context: RuntimeContext) -> None:
        """Initialize service state and acquire 5 structural handles."""
        if self._state not in (ServiceState.UNINITIALIZED, ServiceState.STOPPED):
            raise XRLifecycleError(f"Cannot initialize XRService in state {self._state.name}")

        self._context = context
        self._epoch_id = context.epoch_id
        self._resources = OwnedResourceSet(self.name, self._epoch_id)

        # Acquire 5 structural handles
        for res_id in STRUCTURAL_RESOURCE_IDS:
            self._resources.acquire(res_id)

        # Instantiate components
        self._scene_graph = SceneGraph()
        self._viewport_manager = ViewportManager()
        self._presentation_engine = PresentationEngine()
        self._event_sink = RecordingXREventSink()

        # Register Dispatcher Routes strictly during INITIALIZED
        if self._dispatcher is not None:
            self._dispatcher.register_query_handler(
                "xr.status",
                self.handle_status_query,
                self.name,
            )
            self._dispatcher.register_query_handler(
                "xr.node",
                self.handle_node_query,
                self.name,
            )
            self._dispatcher.register_query_handler(
                "xr.viewport.status",
                self.handle_viewport_query,
                self.name,
            )
            self._dispatcher.register_query_handler(
                "xr.frame",
                self.handle_frame_query,
                self.name,
            )
            self._dispatcher.register_command_handler(
                "xr.reset",
                self.handle_reset_command,
                self.name,
            )

        self._state = ServiceState.INITIALIZED

    def start(self) -> None:
        """Transition service to STARTED. Acquires zero new resources."""
        if self._state != ServiceState.INITIALIZED:
            raise XRLifecycleError(f"Cannot start XRService in state {self._state.name}")
        self._state = ServiceState.STARTED

    def stop(self) -> None:
        """Release all acquired resources and teardown state cleanly."""
        if self._state in (ServiceState.STOPPED, ServiceState.UNINITIALIZED):
            return

        if self._in_transaction:
            raise XRLifecycleError("Cannot stop XRService during an active transaction")

        self._in_transaction = True
        failures: list[DeviceShutdownFailureRecord] = []
        try:
            self.clear()

            # Release all 5 structural handles
            if self._resources is not None:
                for handle in list(self._resources.outstanding_handles):
                    try:
                        self._resources.release(handle.resource_id)
                    except Exception as e:
                        self._resources.mark_release_failed(handle.resource_id, str(e))
                        raw_err = str(e)
                        redacted_err = self._secret_filter.redact(raw_err) if self._secret_filter else raw_err
                        failures.append(
                            DeviceShutdownFailureRecord(
                                device_id=self.name,
                                error_type=type(e).__name__,
                                error_message=redacted_err,
                                execution_index=len(failures),
                                unreleased_resources=(handle.resource_id,),
                            )
                        )

            if failures:
                self._state = ServiceState.FAILED
                raise XRShutdownError(
                    f"XRService teardown encountered {len(failures)} resource failure(s)",
                    failures,
                )

            self._state = ServiceState.STOPPED
        finally:
            self._in_transaction = False

    def health(self) -> ServiceHealth:
        """Produce a synchronous in-process health snapshot."""
        now_utc = datetime.now(timezone.utc).isoformat()
        if self._state != ServiceState.STARTED:
            return ServiceHealth(
                name=self.name,
                status=HealthStatus.FAILED if self._state == ServiceState.FAILED else HealthStatus.UNHEALTHY,
                message=f"Service state is {self._state.name}",
                timestamp_utc=now_utc,
            )

        if self._resources is None or len(self._resources.outstanding_handles) != len(STRUCTURAL_RESOURCE_IDS):
            return ServiceHealth(
                name=self.name,
                status=HealthStatus.UNHEALTHY,
                message="Structural resource corruption detected",
                timestamp_utc=now_utc,
            )

        if any(rec.status == ResourceStatus.UNRELEASED_FAILURE for rec in self._resources.records.values()):
            return ServiceHealth(
                name=self.name,
                status=HealthStatus.UNHEALTHY,
                message="Resource release failure detected",
                timestamp_utc=now_utc,
            )

        nodes_count = self._scene_graph.node_count if self._scene_graph else 0
        return ServiceHealth(
            name=self.name,
            status=HealthStatus.HEALTHY,
            message=f"Active with {nodes_count} visual nodes, {self._frame_sequence} frames generated",
            timestamp_utc=now_utc,
        )

    # -------------------------------------------------------------------------
    # Public Core API
    # -------------------------------------------------------------------------

    def add_visual_node(self, node: VisualNode) -> None:
        """Register a visual node into the scene graph."""
        if self._state != ServiceState.STARTED:
            raise XRLifecycleError(f"Cannot add visual node in state {self._state.name}")

        if self._in_transaction:
            raise XRLifecycleError("Reentrant call to add_visual_node rejected by transaction guard")

        self._in_transaction = True
        try:
            if self._scene_graph is None:
                raise XRResourceIntegrityError("Scene graph is uninitialized")

            self._scene_graph.add_node(node)
            self._emit_event(
                "xr.node.added",
                {
                    "node_id": node.node_id,
                    "name": node.name,
                    "category": node.category.value,
                    "parent_id": node.parent_id,
                },
            )
        finally:
            self._in_transaction = False

    def get_visual_node(self, node_id: str) -> VisualNode:
        """Retrieve visual node by ID."""
        if self._scene_graph is None:
            raise XRResourceIntegrityError("Scene graph is uninitialized")
        return self._scene_graph.get_node(node_id)

    def remove_visual_node(self, node_id: str) -> None:
        """Remove a visual node by ID."""
        if self._state != ServiceState.STARTED:
            raise XRLifecycleError(f"Cannot remove node in state {self._state.name}")

        if self._scene_graph is None:
            raise XRResourceIntegrityError("Scene graph is uninitialized")
        self._scene_graph.remove_node(node_id)

    def configure_viewport(self, viewport: CameraViewport) -> None:
        """Configure a camera viewport."""
        if self._state != ServiceState.STARTED:
            raise XRLifecycleError(f"Cannot configure viewport in state {self._state.name}")

        if self._viewport_manager is None:
            raise XRResourceIntegrityError("Viewport manager is uninitialized")
        self._viewport_manager.add_viewport(viewport)

    def add_annotation(self, annotation: AnnotationItem) -> None:
        """Register a spatial annotation."""
        if self._state != ServiceState.STARTED:
            raise XRLifecycleError(f"Cannot add annotation in state {self._state.name}")

        if self._presentation_engine is None:
            raise XRResourceIntegrityError("Presentation engine is uninitialized")
        self._presentation_engine.add_annotation(annotation)

    def set_interaction_ray(self, ray: Optional[InteractionRay]) -> None:
        """Set gesture pointer ray."""
        if self._presentation_engine is None:
            raise XRResourceIntegrityError("Presentation engine is uninitialized")
        self._presentation_engine.set_interaction_ray(ray)

    def generate_frame(self) -> PresentationDescription:
        """Assemble and emit presentation description frame."""
        if self._state != ServiceState.STARTED:
            raise XRLifecycleError(f"Cannot generate frame in state {self._state.name}")

        if self._in_transaction:
            raise XRLifecycleError("Reentrant call to generate_frame rejected by transaction guard")

        self._in_transaction = True
        try:
            if self._scene_graph is None or self._viewport_manager is None or self._presentation_engine is None:
                raise XRResourceIntegrityError("Subsystems uninitialized")

            self._frame_sequence += 1
            desc = self._presentation_engine.build_description(
                epoch_id=self._epoch_id,
                frame_sequence=self._frame_sequence,
                scene_graph=self._scene_graph,
                viewport_manager=self._viewport_manager,
            )

            # Export over external renderer adapter
            frame_dict = {
                "epoch_id": desc.epoch_id,
                "frame_sequence": desc.frame_sequence,
                "timestamp_utc": desc.timestamp_utc,
                "nodes_count": len(desc.nodes),
                "viewports_count": len(desc.viewports),
                "annotations_count": len(desc.annotations),
                "is_simulated": desc.is_simulated,
            }
            env = self._renderer_adapter.export_presentation_frame(frame_dict)
            if self._dispatcher is not None:
                self._dispatcher.dispatch(env)
            if self._event_sink is not None:
                try:
                    self._event_sink.record_event(env)
                except XRCapacityError:
                    pass

            return desc
        finally:
            self._in_transaction = False

    def reset(self, epoch_id: int) -> None:
        """Reset service state for a new epoch."""
        if epoch_id != self._epoch_id:
            raise XREpochMismatchError(
                f"Reset epoch {epoch_id} does not match service epoch {self._epoch_id}"
            )
        self.clear()

    def clear(self) -> None:
        """Clear all scene nodes, viewports, annotations, and event sinks."""
        if self._scene_graph is not None:
            self._scene_graph.clear()
        if self._viewport_manager is not None:
            self._viewport_manager.clear()
        if self._presentation_engine is not None:
            self._presentation_engine.clear()
        if self._event_sink is not None:
            self._event_sink.clear()
        self._frame_sequence = 0

    # -------------------------------------------------------------------------
    # Dispatcher Handlers
    # -------------------------------------------------------------------------

    def handle_status_query(self, query_envelope: MessageEnvelope) -> MessageEnvelope:
        """Handle xr.status query."""
        node_count = self._scene_graph.node_count if self._scene_graph else 0
        vp_count = self._viewport_manager.viewport_count if self._viewport_manager else 0

        payload = serialize_xr_payload(
            {
                "service_name": self.name,
                "state": self._state.name,
                "epoch_id": self._epoch_id,
                "node_count": node_count,
                "viewport_count": vp_count,
                "frame_sequence": self._frame_sequence,
            }
        )
        return create_response(query_envelope, self.name, payload=dict(payload))

    def handle_node_query(self, query_envelope: MessageEnvelope) -> MessageEnvelope:
        """Handle xr.node query."""
        node_id = query_envelope.payload.get("node_id")
        if not node_id or self._scene_graph is None:
            return create_error_response(
                query_envelope,
                self.name,
                error_code="INVALID_NODE_QUERY",
                error_message="Missing or invalid node_id",
            )

        try:
            node = self._scene_graph.get_node(str(node_id))
            ancestry = self._scene_graph.get_ancestry(str(node_id))
            children = self._scene_graph.get_children(str(node_id))
            payload = serialize_xr_payload(
                {
                    "node_id": node.node_id,
                    "name": node.name,
                    "category": node.category.value,
                    "parent_id": node.parent_id,
                    "ancestry": list(ancestry),
                    "children": list(children),
                    "visibility": node.visibility.value,
                    "opacity": node.opacity,
                    "presentation_reference": node.presentation_reference,
                    "is_simulated": node.is_simulated,
                }
            )
            return create_response(query_envelope, self.name, payload=dict(payload))
        except XRHierarchyError as e:
            return create_error_response(
                query_envelope,
                self.name,
                error_code="NODE_NOT_FOUND",
                error_message=str(e),
            )

    def handle_viewport_query(self, query_envelope: MessageEnvelope) -> MessageEnvelope:
        """Handle xr.viewport.status query."""
        if self._viewport_manager is None:
            return create_error_response(
                query_envelope,
                self.name,
                error_code="VIEWPORT_UNINITIALIZED",
                error_message="Viewport manager uninitialized",
            )

        vps = [
            {
                "viewport_id": v.viewport_id,
                "orientation": v.orientation.value,
                "projection_type": v.projection_type.value,
                "fov_deg": v.field_of_view_deg,
                "aspect_ratio": v.aspect_ratio,
                "is_active": v.is_active,
            }
            for v in self._viewport_manager.viewports
        ]
        payload = serialize_xr_payload({"viewports": vps})
        return create_response(query_envelope, self.name, payload=dict(payload))

    def handle_frame_query(self, query_envelope: MessageEnvelope) -> MessageEnvelope:
        """Handle xr.frame query."""
        if self._state != ServiceState.STARTED:
            return create_error_response(
                query_envelope,
                self.name,
                error_code="SERVICE_NOT_STARTED",
                error_message="XRService is not started",
            )

        desc = self.generate_frame()
        payload = serialize_xr_payload(
            {
                "epoch_id": desc.epoch_id,
                "frame_sequence": desc.frame_sequence,
                "nodes_count": len(desc.nodes),
                "viewports_count": len(desc.viewports),
                "annotations_count": len(desc.annotations),
                "is_simulated": desc.is_simulated,
            }
        )
        return create_response(query_envelope, self.name, payload=dict(payload))

    def handle_reset_command(self, command_envelope: MessageEnvelope) -> MessageEnvelope:
        """Handle xr.reset command."""
        req_epoch = command_envelope.payload.get("epoch_id")
        if req_epoch != self._epoch_id:
            return create_error_response(
                command_envelope,
                self.name,
                error_code="EPOCH_MISMATCH",
                error_message=f"Command epoch {req_epoch} does not match service epoch {self._epoch_id}",
            )

        self.clear()
        payload = serialize_xr_payload({"reset_completed": True, "epoch_id": self._epoch_id})
        return create_response(command_envelope, self.name, payload=dict(payload))

    def _emit_event(self, topic: str, payload: Mapping[str, Any]) -> None:
        """Emit protocol event over dispatcher and record in event sink."""
        frozen_payload = serialize_xr_payload(payload)
        env = create_event(
            message_name=topic,
            source=self.name,
            payload=dict(frozen_payload),
        )

        if self._event_sink is not None:
            try:
                self._event_sink.record_event(env)
            except XRCapacityError:
                pass

        if self._dispatcher is not None:
            self._dispatcher.dispatch(env)
