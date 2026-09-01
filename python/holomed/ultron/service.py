# -*- coding: utf-8 -*-
"""Master Ultron Domain Service implementing IService for M04."""

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
    UltronResourceIntegrityError,
    UltronShutdownError,
    UltronValidationError,
)
from holomed.ultron.fusion import MultimodalFusionEngine
from holomed.ultron.models import (
    ActionIntent,
    ModalityObservation,
    MultimodalContext,
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
    """Multimodal Intelligence & Deterministic Reasoning Service (Ultron)."""

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

        # Subsystem Components
        self._fusion_engine: Optional[MultimodalFusionEngine] = None
        self._context_store: Optional[MultimodalContextStore] = None
        self._rule_engine: Optional[DeterministicRuleEngine] = None
        self._sequence_tracker: Optional[SessionSequenceTracker] = None
        self._event_sink: Optional[RecordingUltronEventSink] = None

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
        return self._fusion_engine

    @property
    def context_store(self) -> Optional[MultimodalContextStore]:
        return self._context_store

    @property
    def rule_engine(self) -> Optional[DeterministicRuleEngine]:
        return self._rule_engine

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

        # Instantiate components
        self._fusion_engine = MultimodalFusionEngine()
        self._context_store = MultimodalContextStore()
        self._rule_engine = DeterministicRuleEngine()
        self._sequence_tracker = SessionSequenceTracker()
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

    # -------------------------------------------------------------------------
    # Public Core API (D218)
    # -------------------------------------------------------------------------

    def ingest_observation(self, observation: ModalityObservation) -> ModalityObservation:
        """Synchronously ingest a normalized modality observation."""
        if self._state != ServiceState.STARTED:
            raise UltronLifecycleError(f"Cannot ingest observation in state {self._state.name}")

        if self._in_transaction:
            raise UltronLifecycleError("Reentrant call to ingest_observation rejected by transaction guard")

        self._in_transaction = True
        try:
            # 1. Epoch and Sequence validation
            if self._sequence_tracker is not None:
                self._sequence_tracker.validate_and_record(observation, self._epoch_id)

            # 2. Record in bounded context store
            if self._context_store is not None:
                self._context_store.record_observation(observation)

            self._processed_observations += 1
            self._emit_event(
                "ultron.observation.accepted",
                {
                    "observation_id": observation.observation_id,
                    "modality": observation.modality.value,
                    "source_id": observation.source_id,
                    "sequence_number": observation.sequence_number,
                },
            )
            return observation
        finally:
            self._in_transaction = False

    def fuse(self) -> MultimodalContext:
        """Run cross-modal fusion on active observations and return updated MultimodalContext."""
        if self._state != ServiceState.STARTED:
            raise UltronLifecycleError(f"Cannot fuse in state {self._state.name}")

        if self._in_transaction:
            raise UltronLifecycleError("Reentrant call to fuse rejected by transaction guard")

        self._in_transaction = True
        try:
            if self._fusion_engine is None or self._context_store is None:
                raise UltronResourceIntegrityError("Fusion engine or context store is uninitialized")

            obs_snapshot = tuple(self._context_store._observations)
            entities, conflicts = self._fusion_engine.fuse_observations(obs_snapshot, self._epoch_id)

            for c in conflicts:
                self._context_store.record_conflict(c)
                self._emit_event(
                    "ultron.conflict.detected",
                    {
                        "conflict_id": c.conflict_id,
                        "conflict_type": c.conflict_type.value,
                        "severity": c.severity.value,
                        "selected_source": c.selected_source.value if c.selected_source else None,
                    },
                )

            # Extract active gestures from latest gesture observation
            active_gestures: list[str] = []
            for obs in reversed(obs_snapshot):
                if obs.modality.value == "GESTURE":
                    active_gestures = list(obs.payload.get("active_gestures", []))
                    break

            ctx = self._context_store.capture_snapshot(self._epoch_id, entities, active_gestures)
            return ctx
        finally:
            self._in_transaction = False

    def reason(self, depth: int = 0) -> tuple[ActionIntent, ...]:
        """Evaluate deterministic rules over current multimodal context."""
        if self._state != ServiceState.STARTED:
            raise UltronLifecycleError(f"Cannot reason in state {self._state.name}")

        if self._in_transaction:
            raise UltronLifecycleError("Reentrant call to reason rejected by transaction guard")

        self._in_transaction = True
        try:
            if self._rule_engine is None or self._context_store is None:
                raise UltronResourceIntegrityError("Rule engine or context store is uninitialized")

            ctx = self.fuse() if not self._context_store.history_count else self._context_store._history[-1]
            intents, trace = self._rule_engine.evaluate(ctx, depth=depth)

            self._total_latency_ms += trace.processing_time_ms
            if trace.degraded:
                self._budget_overruns += 1
                self._emit_event(
                    "ultron.reasoning.degraded",
                    {
                        "trace_id": trace.trace_id,
                        "processing_time_ms": trace.processing_time_ms,
                        "reason": "PROCESSING_BUDGET_EXCEEDED",
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
                    },
                )

            return intents
        finally:
            self._in_transaction = False

    def step(self, observation: Optional[ModalityObservation] = None) -> tuple[ActionIntent, ...]:
        """Perform one complete pipeline cycle: ingest (optional) -> fuse -> reason."""
        if observation is not None:
            self.ingest_observation(observation)
        self.fuse()
        return self.reason(depth=0)

    def reset(self, epoch_id: int) -> None:
        """Reset internal state for a new epoch."""
        if epoch_id != self._epoch_id:
            raise UltronEpochMismatchError(
                f"Reset epoch {epoch_id} does not match active service epoch {self._epoch_id}"
            )
        self.clear()

    def clear(self) -> None:
        """Clear all active observations, entities, conflicts, and intents."""
        if self._fusion_engine is not None:
            self._fusion_engine.clear()
        if self._context_store is not None:
            self._context_store.clear()
        if self._rule_engine is not None:
            self._rule_engine.clear()
        if self._sequence_tracker is not None:
            self._sequence_tracker.clear()
        if self._event_sink is not None:
            self._event_sink.clear()

    # -------------------------------------------------------------------------
    # Dispatcher Query & Command Handlers
    # -------------------------------------------------------------------------

    def handle_status_query(self, query_envelope: MessageEnvelope) -> MessageEnvelope:
        """Handle ultron.status query."""
        avg_latency = (
            round(self._total_latency_ms / float(self._processed_observations), 2)
            if self._processed_observations > 0
            else 0.0
        )
        entity_count = len(self._fusion_engine.entities) if self._fusion_engine else 0
        context_count = self._context_store.observation_count if self._context_store else 0
        reasoning_count = len(self._rule_engine.traces) if self._rule_engine else 0

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
            }
        )
        return create_response(query_envelope, self.name, payload=dict(payload))

    def handle_context_query(self, query_envelope: MessageEnvelope) -> MessageEnvelope:
        """Handle ultron.context query."""
        entities = []
        if self._fusion_engine:
            for e in self._fusion_engine.entities:
                entities.append(
                    {
                        "entity_id": e.entity_id,
                        "entity_type": e.entity_type.value,
                        "confidence": e.confidence,
                        "position": e.position,
                        "source_modalities": [m.value for m in sorted(e.source_modalities, key=lambda m: m.value)],
                    }
                )

        payload = serialize_ultron_payload(
            {
                "epoch_id": self._epoch_id,
                "entities_count": len(entities),
                "entities": entities,
                "active_conflicts_count": len(self._fusion_engine.conflicts) if self._fusion_engine else 0,
            }
        )
        return create_response(query_envelope, self.name, payload=dict(payload))

    def handle_reasoning_query(self, query_envelope: MessageEnvelope) -> MessageEnvelope:
        """Handle ultron.reasoning query."""
        traces = []
        if self._rule_engine:
            for t in self._rule_engine.traces:
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
