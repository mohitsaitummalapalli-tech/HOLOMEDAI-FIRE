# -*- coding: utf-8 -*-
"""Master gesture domain service implementing IService for M03."""

from __future__ import annotations

from datetime import datetime, timezone
import time
from typing import Any, Mapping, Optional, Sequence

from holomed.core.dispatcher import MessageDispatcher
from holomed.devices.exceptions import DeviceNotFoundError
from holomed.devices.manager import DeviceManager
from holomed.devices.models import DeviceShutdownFailureRecord
from holomed.gesture.events import RecordingGestureEventSink
from holomed.gesture.exceptions import (
    GestureCapacityError,
    GestureEpochMismatchError,
    GestureLifecycleError,
    GestureResourceIntegrityError,
    GestureSequenceError,
    GestureShutdownError,
    GestureValidationError,
)
from holomed.gesture.models import (
    GestureProcessingResult,
    GestureQuality,
    HandObservation,
    serialize_gesture_payload,
)
from holomed.gesture.pipeline import GesturePipeline
from holomed.gesture.state_machine import GestureStateMachine
from holomed.gesture.tracker import HandTracker
from holomed.protocol.builders import create_event, create_response
from holomed.protocol.models import MessageEnvelope
from holomed.runtime.context import RuntimeContext
from holomed.runtime.logging import SecretFilter, StructuredLogger
from holomed.runtime.models import HealthStatus, OwnedResourceSet, ResourceStatus, ServiceHealth
from holomed.runtime.service import IService, ServiceState

STRUCTURAL_RESOURCE_IDS: tuple[str, ...] = (
    "gesture.store",
    "gesture.tracker",
    "gesture.pipeline",
    "gesture.metrics",
)


class GestureService(IService):
    """Gesture & Spatial Interaction Domain Service implementing IService for M03."""

    def __init__(
        self,
        device_manager: Optional[DeviceManager] = None,
        dispatcher: Optional[MessageDispatcher] = None,
        secret_filter: Optional[SecretFilter] = None,
        logger: Optional[StructuredLogger] = None,
    ) -> None:
        self._state: ServiceState = ServiceState.UNINITIALIZED
        self._device_manager: Optional[DeviceManager] = device_manager
        self._dispatcher: Optional[MessageDispatcher] = dispatcher
        self._secret_filter: Optional[SecretFilter] = secret_filter
        self._logger: Optional[StructuredLogger] = logger

        self._context: Optional[RuntimeContext] = None
        self._epoch_id: int = 1
        self._resources: Optional[OwnedResourceSet] = None

        self._tracker: Optional[HandTracker] = None
        self._state_machine: Optional[GestureStateMachine] = None
        self._pipeline: Optional[GesturePipeline] = None
        self._event_sink: Optional[RecordingGestureEventSink] = None

        self._in_transaction: bool = False
        self._processed_observations: int = 0
        self._budget_overruns: int = 0
        self._total_latency_ms: float = 0.0

        # Session sequence tracker: (device_id, physical_id, epoch_id) -> last_sequence_number
        self._session_sequences: dict[tuple[str, str, int], int] = {}

    @property
    def name(self) -> str:
        return "gesture_service"

    @property
    def dependencies(self) -> tuple[str, ...]:
        return ("vision_service",)

    @property
    def state(self) -> ServiceState:
        return self._state

    @property
    def resources(self) -> OwnedResourceSet:
        if self._resources is None:
            raise GestureLifecycleError("Service has not been initialized; resources unavailable")
        return self._resources

    @property
    def event_sink(self) -> Optional[RecordingGestureEventSink]:
        return self._event_sink

    def initialize(self, context: RuntimeContext) -> None:
        """Acquire owned resources and prepare service for operation."""
        if self._state != ServiceState.UNINITIALIZED:
            raise GestureLifecycleError(f"Cannot initialize GestureService in state {self._state.name}")

        self._context = context
        self._epoch_id = context.epoch_id

        # Acquire 4 structural resources
        self._resources = OwnedResourceSet(self.name, context.epoch_id)
        for res_id in STRUCTURAL_RESOURCE_IDS:
            self._resources.acquire(res_id)

        # Initialize internal subsystems
        self._event_sink = RecordingGestureEventSink()
        self._tracker = HandTracker()
        self._state_machine = GestureStateMachine(event_emitter=self._emit_event)
        self._pipeline = GesturePipeline(
            tracker=self._tracker,
            state_machine=self._state_machine,
            event_emitter=self._emit_event,
            secret_filter=self._secret_filter,
        )

        # Register static dispatcher query and command routes (strictly during INITIALIZED)
        if self._dispatcher is not None:
            self._dispatcher.register_query_handler(
                "gesture.pipeline.status",
                self.handle_status_query,
                self.name,
            )
            self._dispatcher.register_query_handler(
                "gesture.pipeline.audit",
                self.handle_audit_query,
                self.name,
            )
            self._dispatcher.register_query_handler(
                "gesture.tracks",
                self.handle_tracks_query,
                self.name,
            )
            self._dispatcher.register_command_handler(
                "gesture.pipeline.reset",
                self.handle_reset_command,
                self.name,
            )

        self._state = ServiceState.INITIALIZED

    def start(self) -> None:
        """Transition service to STARTED. Acquires no new resources."""
        if self._state != ServiceState.INITIALIZED:
            raise GestureLifecycleError(f"Cannot start GestureService in state {self._state.name}")
        self._state = ServiceState.STARTED

    def stop(self) -> None:
        """Release all acquired resources and teardown state cleanly."""
        if self._state in (ServiceState.STOPPED, ServiceState.UNINITIALIZED):
            return

        if self._in_transaction:
            raise GestureLifecycleError("Cannot stop GestureService during an active transaction")

        self._in_transaction = True
        failures: list[DeviceShutdownFailureRecord] = []
        try:
            # Clear internal state
            if self._pipeline is not None:
                self._pipeline.reset()
            if self._event_sink is not None:
                self._event_sink.clear()
            self._session_sequences.clear()

            # Release all structural resources
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
                raise GestureShutdownError(
                    f"GestureService teardown encountered {len(failures)} resource failure(s)",
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

        status = HealthStatus.HEALTHY
        msg = f"Processed {self._processed_observations} observations"
        if self._budget_overruns > 5:
            status = HealthStatus.DEGRADED
            msg += f", {self._budget_overruns} budget overruns"

        return ServiceHealth(
            name=self.name,
            status=status,
            message=msg,
            timestamp_utc=now_utc,
        )

    def ingest_observation(self, observation: HandObservation) -> GestureProcessingResult:
        """Synchronously ingest hand observation, update tracks, recognize gestures, and return result."""
        if self._state != ServiceState.STARTED:
            raise GestureLifecycleError(f"Cannot ingest observation in state {self._state.name}")

        if self._in_transaction:
            raise GestureLifecycleError("Reentrant call to ingest_observation rejected by transaction guard")

        self._in_transaction = True
        try:
            # 1. Epoch verification
            if observation.epoch_id != self._epoch_id:
                raise GestureEpochMismatchError(
                    f"Observation epoch {observation.epoch_id} does not match service epoch {self._epoch_id}"
                )

            # 2. Sequence monotonicity verification
            session_key = (observation.device_id, observation.physical_id, observation.epoch_id)
            if session_key in self._session_sequences:
                last_seq = self._session_sequences[session_key]
                if observation.sequence_number <= last_seq:
                    raise GestureSequenceError(
                        f"Non-monotonic sequence number {observation.sequence_number} "
                        f"(last observed: {last_seq}) for session {session_key}"
                    )
            self._session_sequences[session_key] = observation.sequence_number

            # 3. Device identity verification
            self._verify_device_identity(observation)

            # 4. Pipeline execution
            if self._pipeline is None:
                raise GestureResourceIntegrityError("Pipeline is uninitialized")

            result = self._pipeline.process(observation)

            self._processed_observations += 1
            self._total_latency_ms += result.execution_time_ms
            if result.budget_exceeded:
                self._budget_overruns += 1
                self._emit_event(
                    "gesture.pipeline.degraded",
                    {
                        "device_id": observation.device_id,
                        "frame_id": observation.frame_id,
                        "execution_time_ms": result.execution_time_ms,
                        "reason": "PROCESSING_BUDGET_EXCEEDED",
                    },
                )

            return result
        finally:
            self._in_transaction = False

    # Exact alias for canonical method
    process_observation = ingest_observation

    def handle_status_query(self, query_envelope: MessageEnvelope) -> MessageEnvelope:
        """Handle gesture.pipeline.status query."""
        avg_latency = (
            self._total_latency_ms / self._processed_observations
            if self._processed_observations > 0
            else 0.0
        )
        tracks = self._tracker.export_sorted_tracks() if self._tracker else ()
        active_gestures = len(self._pipeline.history) if self._pipeline else 0

        payload = serialize_gesture_payload(
            {
                "service_name": self.name,
                "state": self._state.name,
                "epoch_id": self._epoch_id,
                "tracked_hands": len(tracks),
                "active_gestures": active_gestures,
                "average_latency_ms": round(avg_latency, 2),
                "budget_overruns": self._budget_overruns,
            }
        )
        return create_response(query_envelope, self.name, payload=dict(payload))

    def handle_audit_query(self, query_envelope: MessageEnvelope) -> MessageEnvelope:
        """Handle gesture.pipeline.audit query."""
        is_consistent = True
        findings: list[str] = []

        if self._resources is None or len(self._resources.outstanding_handles) != len(STRUCTURAL_RESOURCE_IDS):
            is_consistent = False
            raw = "Structural resource count mismatch"
            findings.append(self._secret_filter.redact(raw) if self._secret_filter else raw)

        if self._resources is not None:
            for rec in self._resources.records.values():
                if rec.status == ResourceStatus.UNRELEASED_FAILURE:
                    is_consistent = False
                    raw = f"Resource {rec.handle.resource_id} unreleased failure: {rec.release_error}"
                    findings.append(self._secret_filter.redact(raw) if self._secret_filter else raw)

        tracks = self._tracker.export_sorted_tracks() if self._tracker else ()
        history_size = len(self._pipeline.history) if self._pipeline else 0

        payload = serialize_gesture_payload(
            {
                "is_consistent": is_consistent,
                "epoch_id": self._epoch_id,
                "tracked_hands": len(tracks),
                "gesture_history_size": history_size,
                "findings": findings,
            }
        )
        return create_response(query_envelope, self.name, payload=dict(payload))

    def handle_tracks_query(self, query_envelope: MessageEnvelope) -> MessageEnvelope:
        """Handle gesture.tracks query."""
        tracks = self._tracker.export_sorted_tracks() if self._tracker else ()
        payload = serialize_gesture_payload(
            {
                "active_tracks_count": len(tracks),
                "tracks": [
                    {
                        "track_id": t.track_id,
                        "state": t.state.value,
                        "handedness": t.handedness.value,
                        "consecutive_detections": t.consecutive_detections,
                        "coasting_frames": t.coasting_frames,
                    }
                    for t in tracks
                ],
            }
        )
        return create_response(query_envelope, self.name, payload=dict(payload))

    def handle_reset_command(self, command_envelope: MessageEnvelope) -> MessageEnvelope:
        """Handle gesture.pipeline.reset command."""
        req_epoch = command_envelope.payload.get("epoch_id")
        if req_epoch is not None and req_epoch != self._epoch_id:
            raise GestureEpochMismatchError(
                f"Reset epoch {req_epoch} does not match active epoch {self._epoch_id}"
            )

        if self._pipeline is not None:
            self._pipeline.reset()
        if self._event_sink is not None:
            self._event_sink.clear()

        payload = serialize_gesture_payload(
            {
                "reset_completed": True,
                "timestamp_utc": datetime.now(timezone.utc).isoformat(),
            }
        )
        return create_response(command_envelope, self.name, payload=dict(payload))

    def _verify_device_identity(self, observation: HandObservation) -> None:
        """Verify device existence and physical_id match against DeviceRegistry."""
        if self._device_manager is None:
            return

        registry = getattr(self._device_manager, "registry", None) or getattr(
            self._device_manager, "_registry", None
        )
        if registry is None:
            return

        try:
            device = registry.get(observation.device_id)
            if device.physical_id != observation.physical_id:
                raise GestureValidationError(
                    f"Physical ID mismatch for {observation.device_id}: "
                    f"expected {device.physical_id!r}, got {observation.physical_id!r}"
                )
        except (KeyError, DeviceNotFoundError):
            raise GestureValidationError(f"Device {observation.device_id!r} is not registered in DeviceRegistry")

    def _emit_event(self, topic: str, payload: Mapping[str, Any]) -> None:
        """Emit a validated protocol event envelope over dispatcher and record in event sink."""
        frozen_payload = serialize_gesture_payload(payload)
        env = create_event(
            message_name=topic,
            source=self.name,
            payload=dict(frozen_payload),
        )

        if self._event_sink is not None:
            try:
                self._event_sink.record_event(env)
            except GestureCapacityError:
                pass  # Event sink capacity exhaustion does not crash pipeline

        if self._dispatcher is not None:
            self._dispatcher.dispatch(env)
