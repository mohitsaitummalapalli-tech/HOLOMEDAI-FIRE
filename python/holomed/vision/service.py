"""Master spatial vision domain service implementing IService for M01."""

from __future__ import annotations

from datetime import datetime, timezone
import time
from typing import Any, Mapping, Optional
from holomed.core.dispatcher import MessageDispatcher
from holomed.devices.exceptions import DeviceNotFoundError
from holomed.devices.manager import DeviceManager
from holomed.devices.models import DeviceShutdownFailureRecord
from holomed.devices.registry import DeviceRegistry
from holomed.protocol.builders import create_error_response, create_event, create_response
from holomed.protocol.models import MessageEnvelope
from holomed.runtime.context import RuntimeContext
from holomed.runtime.logging import SecretFilter, StructuredLogger
from holomed.runtime.models import HealthStatus, OwnedResourceSet, ServiceHealth
from holomed.runtime.service import IService, ServiceState
from holomed.vision.exceptions import (
    VisionCapacityError,
    VisionLifecycleError,
    VisionResourceIntegrityError,
    VisionShutdownError,
    VisionValidationError,
)
from holomed.vision.models import (
    FrameDescriptor,
    SlotState,
    VisionProcessingResult,
    VisionQuality,
    serialize_vision_payload,
)
from holomed.vision.pipeline import VisionPipeline
from holomed.vision.store import VisionFrameStore
from holomed.vision.tracker import TemporalLandmarkTracker

STRUCTURAL_RESOURCE_IDS = (
    "vision.store",
    "vision.pipeline",
    "vision.tracker",
    "vision.metrics",
)


class VisionService(IService):
    """Spatial Vision & Tracking Service implementing IService.
    
    Responsibilities:
    - Minimal dependency declaration: depends strictly on ("device_manager",).
    - Owns 4 structural resource handles under OwnedResourceSet.
    - Validates incoming frame device_id and physical_id against DeviceRegistry.
    - Manages in-memory VisionFrameStore with read-only memoryviews.
    - Observes processing budget and emits overage events.
    - Registers static dispatcher query/command routes in INITIALIZED.
    """

    def __init__(
        self,
        device_manager: Optional[DeviceManager] = None,
        dispatcher: Optional[MessageDispatcher] = None,
        secret_filter: Optional[SecretFilter] = None,
        logger: Optional[StructuredLogger] = None,
    ) -> None:
        self._device_manager: Optional[DeviceManager] = device_manager
        self._dispatcher: Optional[MessageDispatcher] = dispatcher
        self._secret_filter: Optional[SecretFilter] = secret_filter
        self._logger: Optional[StructuredLogger] = logger

        self._state: ServiceState = ServiceState.UNINITIALIZED
        self._context: Optional[RuntimeContext] = None
        self._epoch_id: int = 1
        self._resources: Optional[OwnedResourceSet] = None

        self._frame_store: Optional[VisionFrameStore] = None
        self._tracker: Optional[TemporalLandmarkTracker] = None
        self._pipeline: Optional[VisionPipeline] = None

        self._processed_frames: int = 0
        self._dropped_frames: int = 0
        self._budget_overages: int = 0
        self._in_transaction: bool = False

    @property
    def name(self) -> str:
        return "vision_service"

    @property
    def dependencies(self) -> tuple[str, ...]:
        return ("device_manager",)

    @property
    def resources(self) -> OwnedResourceSet:
        if self._resources is None:
            raise VisionLifecycleError("Service has not been initialized; resources unavailable")
        return self._resources

    def initialize(self, context: RuntimeContext) -> None:
        """Acquire structural resources and wire static message routes."""
        if self._state != ServiceState.UNINITIALIZED:
            raise VisionLifecycleError(f"Cannot initialize VisionService in state {self._state.name}")

        self._context = context
        self._epoch_id = context.epoch_id

        # Acquire 4 structural resources
        self._resources = OwnedResourceSet(self.name, context.epoch_id)
        for res_id in STRUCTURAL_RESOURCE_IDS:
            self._resources.acquire(res_id)

        # Initialize internal subsystems
        self._frame_store = VisionFrameStore()
        self._tracker = TemporalLandmarkTracker()
        self._pipeline = VisionPipeline(
            tracker=self._tracker,
            secret_filter=self._secret_filter,
        )

        # Register static dispatcher query and command routes (strictly during INITIALIZED)
        if self._dispatcher is not None:
            self._dispatcher.register_query_handler(
                "vision.pipeline.status",
                self.handle_status_query,
                self.name,
            )
            self._dispatcher.register_query_handler(
                "vision.pipeline.audit",
                self.handle_audit_query,
                self.name,
            )
            self._dispatcher.register_query_handler(
                "vision.tracker.tracks",
                self.handle_tracks_query,
                self.name,
            )
            self._dispatcher.register_command_handler(
                "vision.pipeline.reset",
                self.handle_reset_command,
                self.name,
            )

        self._state = ServiceState.INITIALIZED

    def start(self) -> None:
        """Transition service to STARTED."""
        if self._state != ServiceState.INITIALIZED:
            raise VisionLifecycleError(f"Cannot start VisionService in state {self._state.name}")
        self._state = ServiceState.STARTED

    def stop(self) -> None:
        """Teardown frame buffers and release structural resources."""
        if self._state in (ServiceState.STOPPED, ServiceState.UNINITIALIZED):
            return

        if self._in_transaction:
            raise VisionLifecycleError("Cannot stop VisionService during an active transaction")

        self._in_transaction = True
        failures: list[DeviceShutdownFailureRecord] = []
        try:
            # Clear frame buffers and tracker
            if self._frame_store is not None:
                self._frame_store.clear()
            if self._tracker is not None:
                self._tracker.reset()

            # Release all structural resources
            if self._resources is not None:
                for handle in list(self._resources.outstanding_handles):
                    try:
                        self._resources.release(handle.resource_id)
                    except Exception as e:
                        self._resources.mark_release_failed(handle.resource_id, str(e))
                        failures.append(
                            DeviceShutdownFailureRecord(
                                device_id=self.name,
                                error_type=type(e).__name__,
                                error_message=self._secret_filter.redact(str(e)) if self._secret_filter else str(e),
                                execution_index=len(failures),
                                unreleased_resources=(handle.resource_id,),
                            )
                        )

            if failures:
                self._state = ServiceState.FAILED
                raise VisionShutdownError(
                    f"VisionService teardown encountered {len(failures)} resource failure(s)",
                    failures,
                )

            self._state = ServiceState.STOPPED
        finally:
            self._in_transaction = False

    def health(self) -> ServiceHealth:
        """Return synchronous health assessment."""
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

        status = HealthStatus.HEALTHY
        msg = f"Processed {self._processed_frames} frames, {self._dropped_frames} dropped"
        if self._budget_overages > 5:
            status = HealthStatus.DEGRADED
            msg += f", {self._budget_overages} budget overages"

        return ServiceHealth(
            name=self.name,
            status=status,
            message=msg,
            timestamp_utc=now_utc,
        )

    def ingest_frame(
        self,
        descriptor: FrameDescriptor,
        data_bytes: bytes,
    ) -> VisionProcessingResult:
        """Ingest frame, execute pipeline synchronously, and return processing result."""
        if self._state != ServiceState.STARTED:
            raise VisionLifecycleError(f"Cannot ingest frame in state {self._state.name}")

        # 1. Validate physical_id against DeviceRegistry
        self._verify_device_identity(descriptor)

        # 2. Allocate buffer slot in frame store
        handle_id: Optional[str] = None
        try:
            handle_id = self._frame_store.allocate_slot(descriptor, data_bytes)
        except VisionCapacityError:
            self._dropped_frames += 1
            if self._dispatcher is not None:
                self._emit_event(
                    "vision.frame.rejected",
                    {
                        "device_id": descriptor.device_id,
                        "frame_id": descriptor.frame_id,
                        "reason": "BUFFER_POOL_FULL",
                    },
                )
            raise

        try:
            # 3. Obtain read-only view and process
            view = self._frame_store.get_readonly_view(handle_id)
            result = self._pipeline.process(descriptor, view)

            self._processed_frames += 1
            if result.budget_exceeded:
                self._budget_overages += 1
                if self._dispatcher is not None:
                    self._emit_event(
                        "vision.pipeline.budget_exceeded",
                        {
                            "device_id": descriptor.device_id,
                            "frame_id": descriptor.frame_id,
                            "execution_time_ms": result.execution_time_ms,
                        },
                    )

            return result
        finally:
            # 4. Release slot memory back to store
            if handle_id is not None:
                self._frame_store.release_slot(handle_id)

    def handle_status_query(self, query_envelope: MessageEnvelope) -> MessageEnvelope:
        """Handle vision.pipeline.status query."""
        payload = serialize_vision_payload(
            {
                "service_name": self.name,
                "state": self._state.name,
                "processed_frames": self._processed_frames,
                "dropped_frames": self._dropped_frames,
                "budget_overages": self._budget_overages,
                "buffer_states": [s.value for s in (self._frame_store.get_slot_states() if self._frame_store else ())],
            }
        )
        return create_response(query_envelope, self.name, payload=dict(payload))

    def handle_audit_query(self, query_envelope: MessageEnvelope) -> MessageEnvelope:
        """Handle vision.pipeline.audit query."""
        is_consistent = True
        findings: list[str] = []

        if self._resources is None or len(self._resources.outstanding_handles) != len(STRUCTURAL_RESOURCE_IDS):
            is_consistent = False
            findings.append("Structural resource count mismatch")

        if self._frame_store is not None:
            # Free slots check
            states = self._frame_store.get_slot_states()
            if len(states) != 8:
                is_consistent = False
                findings.append(f"Unexpected slot count: {len(states)}")

        payload = serialize_vision_payload(
            {
                "is_consistent": is_consistent,
                "findings": findings,
                "timestamp_utc": datetime.now(timezone.utc).isoformat(),
            }
        )
        return create_response(query_envelope, self.name, payload=dict(payload))

    def handle_tracks_query(self, query_envelope: MessageEnvelope) -> MessageEnvelope:
        """Handle vision.tracker.tracks query."""
        tracks = self._tracker._export_sorted_tracks() if self._tracker else ()
        payload = serialize_vision_payload(
            {
                "active_tracks_count": len(tracks),
                "tracks": [
                    {
                        "entity_id": t.entity_id,
                        "state": t.state.value,
                        "pose": (
                            {
                                "tx": t.pose.tx,
                                "ty": t.pose.ty,
                                "tz": t.pose.tz,
                                "qw": t.pose.qw,
                                "qx": t.pose.qx,
                                "qy": t.pose.qy,
                                "qz": t.pose.qz,
                            }
                            if t.pose
                            else None
                        ),
                    }
                    for t in tracks
                ],
            }
        )
        return create_response(query_envelope, self.name, payload=dict(payload))

    def handle_reset_command(self, command_envelope: MessageEnvelope) -> MessageEnvelope:
        """Handle vision.pipeline.reset command."""
        if self._frame_store:
            self._frame_store.clear()
        if self._tracker:
            self._tracker.reset()

        payload = serialize_vision_payload(
            {
                "reset_completed": True,
                "timestamp_utc": datetime.now(timezone.utc).isoformat(),
            }
        )
        return create_response(command_envelope, self.name, payload=dict(payload))

    def _verify_device_identity(self, descriptor: FrameDescriptor) -> None:
        """Validate that descriptor device_id and physical_id match active DeviceRegistry."""
        if self._device_manager is None:
            return

        registry = getattr(self._device_manager, "registry", None) or getattr(
            self._device_manager, "_registry", None
        )
        if registry is None:
            return

        try:
            device = registry.get(descriptor.device_id)
            if device.physical_id != descriptor.physical_id:
                raise VisionValidationError(
                    f"Physical ID mismatch for {descriptor.device_id}: "
                    f"expected {device.physical_id!r}, got {descriptor.physical_id!r}"
                )
        except (KeyError, DeviceNotFoundError):
            raise VisionValidationError(f"Device {descriptor.device_id!r} is not registered in DeviceRegistry")

    def _emit_event(self, topic: str, payload: Mapping[str, Any]) -> None:
        """Emit a validated event envelope over dispatcher."""
        if self._dispatcher is None:
            return
        frozen_payload = serialize_vision_payload(payload)
        env = create_event(
            message_name=topic,
            source=self.name,
            payload=dict(frozen_payload),
        )
        self._dispatcher.dispatch(env)
