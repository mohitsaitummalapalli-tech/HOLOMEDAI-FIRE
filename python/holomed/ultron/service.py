# -*- coding: utf-8 -*-
"""Master Ultron Domain Service implementing IService for M04 with M38 Session Isolation."""

from __future__ import annotations

from datetime import datetime, timezone
import time
from typing import Any, Mapping, Optional, Sequence
import uuid

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
from holomed.ultron.context import MultimodalContextStore
from holomed.ultron.events import RecordingUltronEventSink
from holomed.ultron.exceptions import (
    UltronCapacityError,
    UltronEpochMismatchError,
    UltronLifecycleError,
    UltronReasoningError,
    UltronResourceIntegrityError,
    UltronSequenceError,
    UltronSessionMismatchError,
    UltronShutdownError,
    UltronValidationError,
)
from holomed.ultron.fusion import MultimodalFusionEngine
from holomed.ultron.models import (
    MAX_ACTIVE_PERCEPTION_SESSIONS,
    SESSION_ID_REGEX,
    ActionIntent,
    ModalityObservation,
    MultimodalContext,
    ObservationConflict,
)
from holomed.ultron.observation import SessionSequenceTracker
from holomed.ultron.reasoning import DeterministicRuleEngine
from holomed.ultron.serialization import serialize_ultron_payload

STRUCTURAL_RESOURCE_IDS: tuple[str, ...] = (
    "ultron.context",
    "ultron.fusion",
    "ultron.reasoning",
    "ultron.intents",
    "ultron.metrics",
)


class UltronService(IService):
    """Multimodal Intelligence & Deterministic Reasoning Service (Ultron) with Session Isolation."""

    def __init__(
        self,
        device_manager: Optional[DeviceManager] = None,
        dispatcher: Optional[MessageDispatcher] = None,
        secret_filter: Optional[SecretFilter] = None,
        logger: Optional[StructuredLogger] = None,
    ) -> None:
        self._device_manager = device_manager
        self._dispatcher = dispatcher
        self._secret_filter = secret_filter
        self._logger = logger or StructuredLogger("ultron_service", secret_filter=secret_filter)

        self._state: ServiceState = ServiceState.UNINITIALIZED
        self._context: Optional[RuntimeContext] = None
        self._resources: Optional[OwnedResourceSet] = None
        self._epoch_id: int = 0

        # Session-Partitioned Subsystem Components
        self._session_stores: dict[str, MultimodalContextStore] = {}
        self._session_fusion_engines: dict[str, MultimodalFusionEngine] = {}
        self._session_rule_engines: dict[str, DeterministicRuleEngine] = {}
        self._session_sequence_trackers: dict[str, SessionSequenceTracker] = {}
        self._event_sink: Optional[RecordingUltronEventSink] = None

        self._default_session_id: str = "default_session"

        # Metrics
        self._processed_observations: int = 0
        self._total_latency_ms: float = 0.0
        self._budget_overruns: int = 0
        self._in_transaction: bool = False

    @property
    def name(self) -> str:
        return "ultron_service"

    @property
    def dependencies(self) -> tuple[str, ...]:
        return (
            "vision_service",
            "audio_service",
            "gesture_service",
        )

    @property
    def state(self) -> ServiceState:
        return self._state

    @property
    def resources(self) -> OwnedResourceSet:
        if self._resources is None:
            raise UltronLifecycleError("Service has not been initialized; resources unavailable")
        return self._resources

    @property
    def fusion_engine(self) -> Optional[MultimodalFusionEngine]:
        if self._session_fusion_engines:
            first_key = next(iter(self._session_fusion_engines.keys()))
            return self._session_fusion_engines[first_key]
        return None

    @property
    def context_store(self) -> Optional[MultimodalContextStore]:
        if self._session_stores:
            first_key = next(iter(self._session_stores.keys()))
            return self._session_stores[first_key]
        return None

    @property
    def rule_engine(self) -> Optional[DeterministicRuleEngine]:
        if self._session_rule_engines:
            first_key = next(iter(self._session_rule_engines.keys()))
            return self._session_rule_engines[first_key]
        return None

    @property
    def event_sink(self) -> Optional[RecordingUltronEventSink]:
        return self._event_sink

    def initialize(self, context: RuntimeContext) -> None:
        """Initialize Ultron components and acquire exactly 5 structural resource handles."""
        if self._state not in (ServiceState.UNINITIALIZED, ServiceState.STOPPED):
            raise UltronLifecycleError(f"Cannot initialize UltronService in state {self._state.name}")

        self._context = context
        self._epoch_id = context.epoch_id
        self._resources = OwnedResourceSet(self.name, self._epoch_id)

        # Acquire 5 structural handles
        for res_id in STRUCTURAL_RESOURCE_IDS:
            self._resources.acquire(res_id)

        self._event_sink = RecordingUltronEventSink()

        # Register Dispatcher Routes strictly in INITIALIZED state
        if self._dispatcher is not None:
            self._dispatcher.register_query_handler(
                "ultron.status",
                self.handle_status_query,
                self.name,
            )
            self._dispatcher.register_query_handler(
                "ultron.context",
                self.handle_context_query,
                self.name,
            )
            self._dispatcher.register_query_handler(
                "ultron.reasoning",
                self.handle_reasoning_query,
                self.name,
            )
            self._dispatcher.register_query_handler(
                "ultron.audit",
                self.handle_audit_query,
                self.name,
            )
            self._dispatcher.register_command_handler(
                "ultron.reset",
                self.handle_reset_command,
                self.name,
            )

            # Subscribe to session teardown broadcast events
            self._dispatcher.subscribe_event("workflow.session.purged", self.handle_session_purged_event, self.name)
            self._dispatcher.subscribe_event("execution.session.purged", self.handle_session_purged_event, self.name)
            self._dispatcher.subscribe_event("workflow.aborted", self.handle_session_purged_event, self.name)
            self._dispatcher.subscribe_event(
                "platform.session.stopped",
                self.handle_session_purged_event,
                self.name,
            )
            self._dispatcher.subscribe_event(
                "platform.session.evicted",
                self.handle_session_purged_event,
                self.name,
            )

        self._state = ServiceState.INITIALIZED

    def start(self) -> None:
        """Transition service to STARTED. Acquires zero new resources."""
        if self._state != ServiceState.INITIALIZED:
            raise UltronLifecycleError(f"Cannot start UltronService in state {self._state.name}")
        self._state = ServiceState.STARTED

    def stop(self) -> None:
        """Release all acquired resources and teardown state cleanly."""
        if self._state in (ServiceState.STOPPED, ServiceState.UNINITIALIZED):
            return

        if self._in_transaction:
            raise UltronLifecycleError("Cannot stop UltronService during an active transaction")

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
                raise UltronShutdownError(
                    f"UltronService teardown encountered {len(failures)} resource failure(s)",
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
        msg = f"Processed {self._processed_observations} observations across {len(self._session_stores)} active sessions"
        if self._budget_overruns > 5:
            status = HealthStatus.DEGRADED
            msg += f", {self._budget_overruns} budget overruns"

        return ServiceHealth(
            name=self.name,
            status=status,
            message=msg,
            timestamp_utc=now_utc,
        )

    # -------------------------------------------------------------------------
    # Core Helper: Session Component Resolution
    # -------------------------------------------------------------------------

    def _get_or_create_session_components(
        self, session_id: str
    ) -> tuple[MultimodalContextStore, MultimodalFusionEngine, DeterministicRuleEngine, SessionSequenceTracker]:
        """Resolve or instantiate isolated session perception components under capacity bounds."""
        if not isinstance(session_id, str) or not SESSION_ID_REGEX.match(session_id):
            raise UltronValidationError(f"Invalid session_id syntax: {session_id!r}")

        if session_id not in self._session_stores:
            if len(self._session_stores) >= MAX_ACTIVE_PERCEPTION_SESSIONS:
                raise UltronCapacityError(
                    f"Max active perception sessions ({MAX_ACTIVE_PERCEPTION_SESSIONS}) exceeded"
                )
            self._session_stores[session_id] = MultimodalContextStore(session_id=session_id)
            self._session_fusion_engines[session_id] = MultimodalFusionEngine()
            self._session_rule_engines[session_id] = DeterministicRuleEngine()
            self._session_sequence_trackers[session_id] = SessionSequenceTracker()

        return (
            self._session_stores[session_id],
            self._session_fusion_engines[session_id],
            self._session_rule_engines[session_id],
            self._session_sequence_trackers[session_id],
        )

    # -------------------------------------------------------------------------
    # Public Core API (D218 / M38 Session-Isolated)
    # -------------------------------------------------------------------------

    def ingest_observation(
        self,
        observation: ModalityObservation,
        session_id: Optional[str] = None,
    ) -> ModalityObservation:
        """Synchronously ingest a normalized modality observation into isolated session memory."""
        if self._state != ServiceState.STARTED:
            raise UltronLifecycleError(f"Cannot ingest observation in state {self._state.name}")

        if self._in_transaction:
            raise UltronLifecycleError("Reentrant call to ingest_observation rejected by transaction guard")

        self._in_transaction = True
        try:
            effective_session_id = session_id or observation.session_id or self._default_session_id

            if session_id is not None and observation.session_id is not None and session_id != observation.session_id:
                raise UltronSessionMismatchError(
                    f"Envelope session_id {session_id!r} does not match payload session_id {observation.session_id!r}"
                )

            if observation.session_id != effective_session_id:
                stamped_obs = ModalityObservation(
                    observation_id=observation.observation_id,
                    modality=observation.modality,
                    source_id=observation.source_id,
                    physical_id=observation.physical_id,
                    epoch_id=observation.epoch_id,
                    sequence_number=observation.sequence_number,
                    timestamp_utc=observation.timestamp_utc,
                    confidence=observation.confidence,
                    payload=observation.payload,
                    session_id=effective_session_id,
                )
            else:
                stamped_obs = observation

            store, fusion_engine, rule_engine, sequence_tracker = self._get_or_create_session_components(
                effective_session_id
            )

            # Sequence tracking per (session_id, source_id)
            sequence_tracker.validate_and_record(stamped_obs, self._epoch_id)

            # Record in session-partitioned context store
            store.record_observation(stamped_obs)

            self._processed_observations += 1
            self._emit_event(
                "ultron.observation.accepted",
                {
                    "observation_id": stamped_obs.observation_id,
                    "modality": stamped_obs.modality.value,
                    "source_id": stamped_obs.source_id,
                    "sequence_number": stamped_obs.sequence_number,
                    "session_id": effective_session_id,
                },
            )
            return stamped_obs
        finally:
            self._in_transaction = False

    def _fuse_session(self, effective_session_id: str) -> MultimodalContext:
        """Internal helper executing fusion for a session without transaction guard checks."""
        store, fusion_engine, rule_engine, sequence_tracker = self._get_or_create_session_components(
            effective_session_id
        )

        obs_snapshot = tuple(store._observations)
        entities, conflicts = fusion_engine.fuse_observations(obs_snapshot, self._epoch_id)

        for c in conflicts:
            stamped_c = ObservationConflict(
                conflict_id=c.conflict_id,
                entity_id=c.entity_id,
                modalities=c.modalities,
                conflict_type=c.conflict_type,
                severity=c.severity,
                selected_source=c.selected_source,
                explanation=c.explanation,
                session_id=effective_session_id,
            )
            store.record_conflict(stamped_c)
            self._emit_event(
                "ultron.conflict.detected",
                {
                    "conflict_id": c.conflict_id,
                    "conflict_type": c.conflict_type.value,
                    "severity": c.severity.value,
                    "selected_source": c.selected_source.value if c.selected_source else None,
                    "session_id": effective_session_id,
                },
            )

        # Extract active gestures from latest gesture observation in session
        active_gestures: list[str] = []
        for obs in reversed(obs_snapshot):
            if obs.modality.value == "GESTURE":
                active_gestures = list(obs.payload.get("active_gestures", []))
                break

        ctx = store.capture_snapshot(self._epoch_id, entities, active_gestures)
        return ctx

    def fuse(self, session_id: Optional[str] = None) -> MultimodalContext:
        """Run cross-modal fusion on active session observations and return updated MultimodalContext."""
        if self._state != ServiceState.STARTED:
            raise UltronLifecycleError(f"Cannot fuse in state {self._state.name}")

        if self._in_transaction:
            raise UltronLifecycleError("Reentrant call to fuse rejected by transaction guard")

        self._in_transaction = True
        try:
            effective_session_id = session_id or self._default_session_id
            return self._fuse_session(effective_session_id)
        finally:
            self._in_transaction = False

    def reason(self, session_id: Optional[str] = None, depth: int = 0) -> tuple[ActionIntent, ...]:
        """Evaluate deterministic rules over active session context."""
        if self._state != ServiceState.STARTED:
            raise UltronLifecycleError(f"Cannot reason in state {self._state.name}")

        if self._in_transaction:
            raise UltronLifecycleError("Reentrant call to reason rejected by transaction guard")

        self._in_transaction = True
        try:
            effective_session_id = session_id or self._default_session_id
            store, fusion_engine, rule_engine, sequence_tracker = self._get_or_create_session_components(
                effective_session_id
            )

            ctx = self._fuse_session(effective_session_id) if not store.history_count else store._history[-1]
            intents, trace = rule_engine.evaluate(ctx, depth=depth)

            self._total_latency_ms += trace.processing_time_ms
            if trace.degraded:
                self._budget_overruns += 1
                self._emit_event(
                    "ultron.reasoning.degraded",
                    {
                        "trace_id": trace.trace_id,
                        "processing_time_ms": trace.processing_time_ms,
                        "reason": "PROCESSING_BUDGET_EXCEEDED",
                        "session_id": effective_session_id,
                    },
                )
            else:
                self._emit_event(
                    "ultron.reasoning.completed",
                    {
                        "trace_id": trace.trace_id,
                        "fired_rules_count": len(trace.fired_rule_ids),
                        "intents_count": len(trace.intent_ids),
                        "processing_time_ms": trace.processing_time_ms,
                        "session_id": effective_session_id,
                    },
                )

            for intent in intents:
                self._emit_event(
                    "ultron.intent.created",
                    {
                        "intent_id": intent.intent_id,
                        "action_type": intent.action_type.value,
                        "confidence": intent.confidence,
                        "entity_id": intent.entity_id,
                        "session_id": effective_session_id,
                    },
                )

            return intents
        finally:
            self._in_transaction = False

    def step(
        self,
        observation: Optional[ModalityObservation] = None,
        session_id: Optional[str] = None,
    ) -> tuple[ActionIntent, ...]:
        """Perform one complete pipeline cycle for a session: ingest (optional) -> fuse -> reason."""
        effective_session_id = session_id or (observation.session_id if observation else self._default_session_id)
        if observation is not None:
            self.ingest_observation(observation, session_id=effective_session_id)
        self.fuse(effective_session_id)
        return self.reason(session_id=effective_session_id, depth=0)

    def purge_session(self, session_id: str) -> None:
        """Atomically purge all transient perception memory, context, and resources for a clinical session."""
        store = self._session_stores.pop(session_id, None)
        if store is not None:
            store.clear()
        fusion = self._session_fusion_engines.pop(session_id, None)
        if fusion is not None:
            fusion.clear()
        rule = self._session_rule_engines.pop(session_id, None)
        if rule is not None:
            rule.clear()
        seq = self._session_sequence_trackers.pop(session_id, None)
        if seq is not None:
            seq.clear()
        self._emit_event("ultron.session.purged", {"session_id": session_id, "epoch_id": self._epoch_id})

    def handle_session_purged_event(self, event_envelope: MessageEnvelope) -> None:
        """Handle session teardown broadcast event by purging perception memory."""
        sess_id = None
        if isinstance(event_envelope.payload, dict):
            sess_id = event_envelope.payload.get("session_id")
        if not sess_id and isinstance(event_envelope.metadata, dict):
            sess_id = event_envelope.metadata.get("session_id")
        if sess_id and isinstance(sess_id, str):
            self.purge_session(sess_id)

    def reset(self, epoch_id: int) -> None:
        """Reset internal state for a new epoch."""
        if epoch_id != self._epoch_id:
            raise UltronEpochMismatchError(
                f"Reset epoch {epoch_id} does not match active service epoch {self._epoch_id}"
            )
        self.clear()

    def clear(self) -> None:
        """Clear all active observations, entities, conflicts, and intents across all sessions."""
        for sess_id in list(self._session_stores.keys()):
            self.purge_session(sess_id)
        self._session_stores.clear()
        self._session_fusion_engines.clear()
        self._session_rule_engines.clear()
        self._session_sequence_trackers.clear()
        if self._event_sink is not None:
            self._event_sink.clear()

    # -------------------------------------------------------------------------
    # Dispatcher Query & Command Handlers (M38 Session-Isolated)
    # -------------------------------------------------------------------------

    def handle_status_query(self, query_envelope: MessageEnvelope) -> MessageEnvelope:
        """Handle ultron.status query."""
        sess_id = query_envelope.metadata.get("session_id") or query_envelope.payload.get("session_id")
        avg_latency = (
            round(self._total_latency_ms / float(self._processed_observations), 2)
            if self._processed_observations > 0
            else 0.0
        )

        entity_count = 0
        context_count = 0
        reasoning_count = 0

        if sess_id and sess_id in self._session_stores:
            entity_count = len(self._session_fusion_engines[sess_id].entities)
            context_count = self._session_stores[sess_id].observation_count
            reasoning_count = len(self._session_rule_engines[sess_id].traces)
        elif not sess_id and len(self._session_stores) == 1:
            default_key = next(iter(self._session_stores.keys()))
            entity_count = len(self._session_fusion_engines[default_key].entities)
            context_count = self._session_stores[default_key].observation_count
            reasoning_count = len(self._session_rule_engines[default_key].traces)

        payload = serialize_ultron_payload(
            {
                "service_name": self.name,
                "state": self._state.name,
                "epoch_id": self._epoch_id,
                "processed_observations": self._processed_observations,
                "entity_count": entity_count,
                "context_count": context_count,
                "reasoning_count": reasoning_count,
                "average_latency_ms": avg_latency,
                "budget_overruns": self._budget_overruns,
                "active_sessions_count": len(self._session_stores),
            }
        )
        return create_response(query_envelope, self.name, payload=dict(payload))

    def handle_context_query(self, query_envelope: MessageEnvelope) -> MessageEnvelope:
        """Handle ultron.context query with strict session isolation."""
        sess_id = query_envelope.metadata.get("session_id") or query_envelope.payload.get("session_id")
        entities = []
        conflicts_count = 0

        target_fusion = None
        if sess_id and sess_id in self._session_fusion_engines:
            target_fusion = self._session_fusion_engines[sess_id]
        elif not sess_id and len(self._session_fusion_engines) == 1:
            target_fusion = next(iter(self._session_fusion_engines.values()))

        if target_fusion is not None:
            for e in target_fusion.entities:
                entities.append(
                    {
                        "entity_id": e.entity_id,
                        "entity_type": e.entity_type.value,
                        "confidence": e.confidence,
                        "position": e.position,
                        "source_modalities": [m.value for m in sorted(e.source_modalities, key=lambda m: m.value)],
                    }
                )
            conflicts_count = len(target_fusion.conflicts)

        payload = serialize_ultron_payload(
            {
                "epoch_id": self._epoch_id,
                "entities_count": len(entities),
                "entities": entities,
                "active_conflicts_count": conflicts_count,
            }
        )
        return create_response(query_envelope, self.name, payload=dict(payload))

    def handle_reasoning_query(self, query_envelope: MessageEnvelope) -> MessageEnvelope:
        """Handle ultron.reasoning query with strict session isolation."""
        sess_id = query_envelope.metadata.get("session_id") or query_envelope.payload.get("session_id")
        traces = []

        target_rule = None
        if sess_id and sess_id in self._session_rule_engines:
            target_rule = self._session_rule_engines[sess_id]
        elif not sess_id and len(self._session_rule_engines) == 1:
            target_rule = next(iter(self._session_rule_engines.values()))

        if target_rule is not None:
            for t in target_rule.traces:
                traces.append(
                    {
                        "trace_id": t.trace_id,
                        "fired_rules": list(t.fired_rule_ids),
                        "intent_ids": list(t.intent_ids),
                        "processing_time_ms": t.processing_time_ms,
                        "degraded": t.degraded,
                    }
                )

        payload = serialize_ultron_payload(
            {
                "traces_count": len(traces),
                "traces": traces,
            }
        )
        return create_response(query_envelope, self.name, payload=dict(payload))

    def handle_audit_query(self, query_envelope: MessageEnvelope) -> MessageEnvelope:
        """Handle ultron.audit query."""
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

        payload = serialize_ultron_payload(
            {
                "is_consistent": is_consistent,
                "epoch_id": self._epoch_id,
                "resource_handles_count": len(self._resources.outstanding_handles) if self._resources else 0,
                "findings": findings,
            }
        )
        return create_response(query_envelope, self.name, payload=dict(payload))

    def handle_reset_command(self, command_envelope: MessageEnvelope) -> MessageEnvelope:
        """Handle ultron.reset command."""
        req_epoch = command_envelope.payload.get("epoch_id")
        if req_epoch != self._epoch_id:
            return create_error_response(
                command_envelope,
                self.name,
                error_code="EPOCH_MISMATCH",
                error_message=f"Command epoch {req_epoch} does not match service epoch {self._epoch_id}",
            )

        self.clear()
        payload = serialize_ultron_payload({"reset_completed": True, "epoch_id": self._epoch_id})
        return create_response(command_envelope, self.name, payload=dict(payload))

    def _emit_event(self, topic: str, payload: Mapping[str, Any]) -> None:
        """Emit protocol event over dispatcher and record in event sink."""
        frozen_payload = serialize_ultron_payload(payload)
        env = create_event(
            message_name=topic,
            source=self.name,
            payload=dict(frozen_payload),
        )

        if self._event_sink is not None:
            try:
                self._event_sink.record_event(env)
            except UltronCapacityError:
                pass  # Sink capacity exhaustion does not crash pipeline

        if self._dispatcher is not None:
            self._dispatcher.dispatch(env)
