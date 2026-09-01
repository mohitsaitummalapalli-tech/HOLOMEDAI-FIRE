# -*- coding: utf-8 -*-
"""M02 Audio Subsystem AudioService Implementation.

Implements IService with strict 4-handle resource ownership,
dispatcher route registration during INITIALIZED, session sequence validation (D200),
device registry identity validation, and observational budget monitoring.
"""

from datetime import datetime, timezone
import time
from typing import Any, Mapping, Optional, Sequence, Union

from holomed.audio.events import RecordingAudioEventSink
from holomed.audio.exceptions import (
    AudioCapacityError,
    AudioDeviceIdentityError,
    AudioEpochMismatchError,
    AudioLifecycleError,
    AudioResourceIntegrityError,
    AudioSequenceError,
    AudioShutdownError,
    AudioValidationError,
)
from holomed.audio.models import (
    AudioChunk,
    AudioProcessingResult,
    AudioQuality,
    AudioTrack,
    MicrophonePosition,
    serialize_audio_payload,
)
from holomed.audio.pipeline import AudioPipeline
from holomed.audio.store import AudioBufferStore
from holomed.core.dispatcher import MessageDispatcher
from holomed.devices.exceptions import DeviceNotFoundError
from holomed.devices.manager import DeviceManager
from holomed.devices.models import DeviceShutdownFailureRecord, DeviceState
from holomed.devices.registry import DeviceRegistry
from holomed.protocol.builders import create_event, create_response
from holomed.protocol.models import MessageEnvelope
from holomed.runtime.context import RuntimeContext
from holomed.runtime.logging import SecretFilter, StructuredLogger
from holomed.runtime.models import HealthStatus, OwnedResourceSet, ServiceHealth
from holomed.runtime.service import IService, ServiceState

STRUCTURAL_RESOURCE_IDS: tuple[str, ...] = (
    "audio.store",
    "audio.pipeline",
    "audio.tracker",
    "audio.metrics",
)


class AudioService(IService):
    """Spatial audio and acoustic processing domain service."""

    def __init__(
        self,
        device_manager: Optional[DeviceManager] = None,
        dispatcher: Optional[MessageDispatcher] = None,
        microphones: Optional[Sequence[MicrophonePosition]] = None,
    ) -> None:
        self._state: ServiceState = ServiceState.UNINITIALIZED
        self._device_manager: Optional[DeviceManager] = device_manager
        self._dispatcher: Optional[MessageDispatcher] = dispatcher
        self._initial_microphones: Optional[Sequence[MicrophonePosition]] = microphones

        self._context: Optional[RuntimeContext] = None
        self._logger: Optional[StructuredLogger] = None
        self._secret_filter: Optional[SecretFilter] = None
        self._resources: Optional[OwnedResourceSet] = None

        self._frame_store: Optional[AudioBufferStore] = None
        self._pipeline: Optional[AudioPipeline] = None
        self._event_sink: Optional[RecordingAudioEventSink] = None

        self._in_transaction: bool = False
        self._processed_chunks: int = 0
        self._dropped_chunks: int = 0
        self._budget_overruns: int = 0
        self._total_latency_ms: float = 0.0

        # Session sequence tracker: (device_id, physical_id, epoch_id) -> last_sequence_number
        self._session_sequences: dict[tuple[str, str, int], int] = {}
        self._previous_track_states: dict[int, str] = {}

    @property
    def name(self) -> str:
        return "audio_service"

    @property
    def dependencies(self) -> tuple[str, ...]:
        return ("device_manager",)

    @property
    def state(self) -> ServiceState:
        return self._state

    @property
    def resources(self) -> Optional[OwnedResourceSet]:
        return self._resources

    @property
    def frame_store(self) -> Optional[AudioBufferStore]:
        return self._frame_store

    @property
    def pipeline(self) -> Optional[AudioPipeline]:
        return self._pipeline

    @property
    def event_sink(self) -> Optional[RecordingAudioEventSink]:
        return self._event_sink

    def initialize(self, context: RuntimeContext) -> None:
        """Initialize service and acquire exactly 4 structural resources."""
        if self._state != ServiceState.UNINITIALIZED:
            raise AudioLifecycleError(
                f"Cannot initialize {self.name}: current state is {self._state.name}"
            )

        self._context = context
        self._secret_filter = getattr(context, "secret_filter", None)
        self._logger = StructuredLogger(self.name, secret_filter=self._secret_filter) if self._secret_filter else None

        # 1. Acquire exactly 4 structural resource handles
        self._resources = OwnedResourceSet(self.name, context.epoch_id)
        for res_id in STRUCTURAL_RESOURCE_IDS:
            self._resources.acquire(res_id)

        # 2. Instantiate memory plane & processing components
        self._frame_store = AudioBufferStore()
        self._pipeline = AudioPipeline(
            microphones=self._initial_microphones,
            secret_filter=self._secret_filter,
        )
        self._event_sink = RecordingAudioEventSink()

        # 3. Register static routes on dispatcher if provided
        if self._dispatcher is not None:
            self._dispatcher.register_query_handler("audio.pipeline.status", self.handle_status_query, self.name)
            self._dispatcher.register_query_handler("audio.pipeline.audit", self.handle_audit_query, self.name)
            self._dispatcher.register_query_handler("audio.tracker.tracks", self.handle_tracks_query, self.name)
            self._dispatcher.register_command_handler("audio.pipeline.reset", self.handle_reset_command, self.name)

        self._state = ServiceState.INITIALIZED

    def start(self) -> None:
        """Transition service from INITIALIZED to STARTED acquiring zero new resources."""
        if self._state == ServiceState.STARTED:
            raise AudioLifecycleError(f"{self.name} is already STARTED (double start rejected)")
        if self._state != ServiceState.INITIALIZED:
            raise AudioLifecycleError(
                f"Cannot start {self.name}: must be in INITIALIZED state, current is {self._state.name}"
            )
        self._state = ServiceState.STARTED

    def stop(self) -> None:
        """Idempotently teardown service and release all structural resources."""
        if self._state in (ServiceState.UNINITIALIZED, ServiceState.STOPPED):
            return

        failures: list[DeviceShutdownFailureRecord] = []
        try:
            # Clear internal data structures
            if self._frame_store is not None:
                self._frame_store.clear()
            if self._pipeline is not None:
                self._pipeline.tracker.reset()
            self._session_sequences.clear()

            # Release all 4 structural resources
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
                raise AudioShutdownError(
                    f"AudioService teardown encountered {len(failures)} resource failure(s)",
                    failures,
                )

            self._state = ServiceState.STOPPED

        except AudioShutdownError:
            raise
        except Exception as e:
            self._state = ServiceState.FAILED
            rec = DeviceShutdownFailureRecord(
                device_id=self.name,
                error_type=type(e).__name__,
                error_message=self._secret_filter.redact(str(e)) if self._secret_filter else str(e),
                execution_index=0,
                unreleased_resources=tuple(
                    h.resource_id for h in (self._resources.outstanding_handles if self._resources else ())
                ),
            )
            raise AudioShutdownError(f"Unexpected teardown exception in {self.name}: {e}", [rec])

    def health(self) -> ServiceHealth:
        """Report component health."""
        if self._state == ServiceState.FAILED:
            status = HealthStatus.UNHEALTHY
            msg = "AudioService is in FAILED state"
        elif self._budget_overruns > 50:
            status = HealthStatus.DEGRADED
            msg = f"AudioService experiencing frequent budget overruns ({self._budget_overruns})"
        elif self._state == ServiceState.STARTED:
            status = HealthStatus.HEALTHY
            msg = "AudioService operational"
        else:
            status = HealthStatus.HEALTHY
            msg = f"AudioService in {self._state.name}"

        return ServiceHealth(
            name=self.name,
            status=status,
            message=self._secret_filter.redact(msg) if self._secret_filter else msg,
            timestamp_utc=datetime.now(timezone.utc).isoformat(),
        )

    def ingest_chunk(
        self,
        chunk: AudioChunk,
        raw_pcm: Union[bytes, bytearray, memoryview],
    ) -> AudioProcessingResult:
        """Ingest, store, process, and track an audio chunk."""
        if self._state != ServiceState.STARTED:
            raise AudioLifecycleError(
                f"Cannot ingest audio chunk: {self.name} is in {self._state.name} state (must be STARTED)"
            )
        if self._in_transaction:
            raise AudioLifecycleError("Reentrant invocation detected during audio ingestion")

        self._in_transaction = True
        try:
            # 1. Epoch Validation (§42)
            if self._context is not None and chunk.epoch_id != self._context.epoch_id:
                self._dropped_chunks += 1
                self._emit_event(
                    "audio.chunk.rejected",
                    {
                        "chunk_id": chunk.chunk_id,
                        "device_id": chunk.device_id,
                        "error_code": "ERR_EPOCH_MISMATCH",
                        "reason": f"Chunk epoch {chunk.epoch_id} != context epoch {self._context.epoch_id}",
                    },
                )
                raise AudioEpochMismatchError(
                    f"Chunk epoch_id {chunk.epoch_id} does not match active epoch {self._context.epoch_id}"
                )

            # 2. Device Identity & State Validation (§41)
            self._verify_device_identity(chunk)

            # 3. Session Sequence Validation (D200, §39, §40)
            session_key = (chunk.device_id, chunk.physical_id, chunk.epoch_id)
            if session_key in self._session_sequences:
                last_seq = self._session_sequences[session_key]
                if chunk.sequence_number <= last_seq:
                    self._dropped_chunks += 1
                    self._emit_event(
                        "audio.chunk.rejected",
                        {
                            "chunk_id": chunk.chunk_id,
                            "device_id": chunk.device_id,
                            "error_code": "ERR_SEQUENCE_ERROR",
                            "reason": f"Sequence {chunk.sequence_number} <= previous {last_seq}",
                        },
                    )
                    raise AudioSequenceError(
                        f"Non-monotonic sequence number {chunk.sequence_number} (last was {last_seq}) for session {session_key}"
                    )
            self._session_sequences[session_key] = chunk.sequence_number

            # 4. Allocate In-Memory Storage (§13, §17)
            if self._frame_store is None or self._pipeline is None:
                raise AudioResourceIntegrityError("Internal audio components not initialized")

            handle_id = self._frame_store.allocate_slot(chunk, raw_pcm)

            self._emit_event(
                "audio.chunk.accepted",
                {
                    "chunk_id": chunk.chunk_id,
                    "device_id": chunk.device_id,
                    "sequence_number": chunk.sequence_number,
                    "frame_count": chunk.frame_count,
                    "channels": chunk.channels,
                },
            )

            # 5. Process Chunk with Read-Only View
            try:
                self._frame_store.mark_processing(handle_id)
                view = self._frame_store.get_readonly_view(handle_id)
                result, tracks = self._pipeline.process(chunk, view)
            finally:
                # Guaranteed slot release
                self._frame_store.release_slot(handle_id)

            # 6. Post-processing Metrics & Events
            self._processed_chunks += 1
            self._total_latency_ms += result.processing_latency_ms
            if result.budget_exceeded:
                self._budget_overruns += 1
                self._emit_event(
                    "audio.pipeline.budget_exceeded",
                    {
                        "chunk_id": chunk.chunk_id,
                        "device_id": chunk.device_id,
                        "latency_ms": result.processing_latency_ms,
                    },
                )

            if result.quality == AudioQuality.FAILED:
                self._emit_event(
                    "audio.pipeline.failed",
                    {
                        "chunk_id": chunk.chunk_id,
                        "device_id": chunk.device_id,
                        "error_code": result.error_code,
                        "error_message": result.error_message,
                    },
                )
            else:
                self._emit_event(
                    "audio.pipeline.processed",
                    {
                        "chunk_id": chunk.chunk_id,
                        "device_id": chunk.device_id,
                        "rms_energy": result.features.rms_energy,
                        "peak_frequency_hz": result.features.peak_frequency_hz,
                        "quality": result.quality.value,
                    },
                )

            # Check track events
            current_states = {t.track_id: t.state.value for t in tracks}
            for t in tracks:
                prev = self._previous_track_states.get(t.track_id)
                if prev != "CONFIRMED" and t.state.value == "CONFIRMED":
                    self._emit_event(
                        "audio.track.confirmed",
                        {
                            "track_id": t.track_id,
                            "azimuth_deg": t.direction.azimuth_deg,
                            "elevation_deg": t.direction.elevation_deg,
                        },
                    )
            for old_id, old_st in self._previous_track_states.items():
                if old_st in ("CONFIRMED", "COASTING") and old_id not in current_states:
                    self._emit_event("audio.track.lost", {"track_id": old_id})
            self._previous_track_states = current_states

            return result

        finally:
            self._in_transaction = False

    def handle_status_query(self, query_envelope: MessageEnvelope) -> MessageEnvelope:
        """Handle audio.pipeline.status query."""
        avg_latency = (
            round(self._total_latency_ms / float(self._processed_chunks), 2)
            if self._processed_chunks > 0
            else 0.0
        )
        active_tracks = self._pipeline.tracker.active_track_count if self._pipeline else 0
        buffered = self._frame_store.allocated_count if self._frame_store else 0

        payload = serialize_audio_payload(
            {
                "service_name": self.name,
                "state": self._state.name,
                "epoch_id": self._context.epoch_id if self._context else 0,
                "buffered_chunks": buffered,
                "active_tracks": active_tracks,
                "processed_chunks": self._processed_chunks,
                "dropped_chunks": self._dropped_chunks,
                "average_latency_ms": avg_latency,
                "budget_overruns": self._budget_overruns,
            }
        )
        return create_response(query_envelope, self.name, payload=dict(payload))

    def handle_audit_query(self, query_envelope: MessageEnvelope) -> MessageEnvelope:
        """Handle audio.pipeline.audit query."""
        is_consistent = True
        findings: list[str] = []

        if self._resources is None or len(self._resources.outstanding_handles) != len(STRUCTURAL_RESOURCE_IDS):
            is_consistent = False
            findings.append("Structural resource count mismatch")

        if self._frame_store is not None:
            states = self._frame_store.get_slot_states()
            if len(states) != 16:
                is_consistent = False
                findings.append(f"Unexpected slot count: {len(states)}")

        payload = serialize_audio_payload(
            {
                "is_consistent": is_consistent,
                "epoch_id": self._context.epoch_id if self._context else 0,
                "buffer_slots": len(self._frame_store.get_slot_states()) if self._frame_store else 16,
                "active_tracks": self._pipeline.tracker.active_track_count if self._pipeline else 0,
                "findings": findings,
            }
        )
        return create_response(query_envelope, self.name, payload=dict(payload))

    def handle_tracks_query(self, query_envelope: MessageEnvelope) -> MessageEnvelope:
        """Handle audio.tracker.tracks query."""
        tracks = self._pipeline.tracker._export_sorted_tracks() if self._pipeline else ()
        payload = serialize_audio_payload(
            {
                "active_tracks_count": len(tracks),
                "tracks": [
                    {
                        "track_id": t.track_id,
                        "state": t.state.value,
                        "direction": {
                            "azimuth_deg": t.direction.azimuth_deg,
                            "elevation_deg": t.direction.elevation_deg,
                            "confidence": t.direction.confidence,
                        },
                        "observations_count": t.observations_count,
                        "coasting_frames": t.coasting_frames,
                    }
                    for t in tracks
                ],
            }
        )
        return create_response(query_envelope, self.name, payload=dict(payload))

    def handle_reset_command(self, command_envelope: MessageEnvelope) -> MessageEnvelope:
        """Handle audio.pipeline.reset command."""
        if self._frame_store:
            self._frame_store.clear()
        if self._pipeline:
            self._pipeline.tracker.reset()
        self._session_sequences.clear()
        self._previous_track_states.clear()

        payload = serialize_audio_payload(
            {
                "reset_completed": True,
                "timestamp_utc": datetime.now(timezone.utc).isoformat(),
            }
        )
        return create_response(command_envelope, self.name, payload=dict(payload))

    def _verify_device_identity(self, chunk: AudioChunk) -> None:
        """Validate that chunk device_id exists, is READY or ACTIVE, and matches physical_id."""
        if self._device_manager is None:
            return

        registry = getattr(self._device_manager, "registry", None) or getattr(
            self._device_manager, "_registry", None
        )
        if registry is None:
            return

        try:
            device = registry.get(chunk.device_id)
        except (KeyError, DeviceNotFoundError):
            raise AudioDeviceIdentityError(f"Device '{chunk.device_id}' is not registered in DeviceRegistry")

        # Verify device state in READY or ACTIVE (§41)
        if device.state not in (DeviceState.READY, DeviceState.ACTIVE):
            raise AudioDeviceIdentityError(
                f"Device '{chunk.device_id}' is in {device.state.name} state (must be READY or ACTIVE)"
            )

        if chunk.physical_id and device.physical_id != chunk.physical_id:
            raise AudioDeviceIdentityError(
                f"Physical ID mismatch for {chunk.device_id}: "
                f"expected {device.physical_id!r}, got {chunk.physical_id!r}"
            )

    def _emit_event(self, topic: str, payload: Mapping[str, Any]) -> None:
        """Helper to create and dispatch or record an audio event envelope."""
        clean_payload = serialize_audio_payload(payload)
        envelope = create_event(topic, self.name, payload=clean_payload)
        if self._event_sink is not None:
            try:
                self._event_sink.record_event(envelope)
            except AudioCapacityError:
                # Capacity error isolated from main flow
                pass
        if self._dispatcher is not None and self._dispatcher.state.name == "STARTED":
            self._dispatcher.dispatch(envelope)
