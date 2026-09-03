# -*- coding: utf-8 -*-
"""Registration Drift & Anatomical Landmark Monitoring Service for M16."""

from __future__ import annotations

from datetime import datetime, timezone
import math
from typing import Any, Dict, List, Mapping, Optional, Tuple
import uuid

from holomed.core.dispatcher import MessageDispatcher
from holomed.core.models import DispatcherState
from holomed.devices.models import DeviceShutdownFailureRecord
from holomed.drift.constants import (
    DEFAULT_PATIENT_TRACKER_FRAME,
    MAX_ACTIVE_DRIFT_SESSIONS,
    MAX_DWELL_BUFFER_SAMPLES,
    MAX_LANDMARKS_PER_SESSION,
    SERVICE_NAME,
)
from holomed.drift.evaluator import DriftEvaluator
from holomed.drift.exceptions import (
    DriftCapacityError,
    DriftEpochMismatchError,
    DriftError,
    DriftInterlockError,
    DriftLifecycleError,
    DriftRegistrationError,
    DriftSequenceError,
    DriftShutdownError,
    DriftValidationError,
)
from holomed.drift.geometry import convert_point_mm_to_meters
from holomed.drift.models import (
    DriftState,
    DriftStatusRecord,
    LandmarkDefinition,
    LandmarkObservation,
    LandmarkVerificationRecord,
)
from holomed.protocol.builders import (
    create_error_response,
    create_event,
    create_response,
)
from holomed.protocol.models import MessageEnvelope
from holomed.registration.models import RegistrationState
from holomed.registration.service import RegistrationService
from holomed.runtime.context import RuntimeContext
from holomed.runtime.logging import SecretFilter, StructuredLogger
from holomed.runtime.models import (
    HealthStatus,
    OwnedResourceSet,
    ServiceHealth,
)
from holomed.runtime.service import IService, ServiceState
from holomed.workflow.models import SafetyInterlock

STRUCTURAL_RESOURCE_IDS: tuple[str, ...] = (
    "drift.registry",
    "drift.evaluator",
    "drift.landmarks",
    "drift.interlock",
)


def _format_error_code(exc_type_name: str) -> str:
    """Format exception class name into protocol-compliant ^ERR_[A-Z0-9_]+$ error code."""
    clean = exc_type_name.upper().replace("ERROR", "")
    return f"ERR_{clean}" if not clean.startswith("ERR_") else clean


class DriftService(IService):
    """Registration Drift Detection & Anatomical Landmark Confirmation Service."""

    def __init__(
        self,
        dispatcher: Optional[MessageDispatcher] = None,
        registration_service: Optional[RegistrationService] = None,
        secret_filter: Optional[SecretFilter] = None,
        logger: Optional[StructuredLogger] = None,
    ) -> None:
        self._dispatcher = dispatcher
        self._registration_service = registration_service
        self._secret_filter = secret_filter
        self._logger = logger or StructuredLogger(SERVICE_NAME, secret_filter=secret_filter)

        self._state: ServiceState = ServiceState.UNINITIALIZED
        self._context: Optional[RuntimeContext] = None
        self._resources: Optional[OwnedResourceSet] = None
        self._epoch_id: int = 0

        # Session tracking state
        # session_id -> dict of landmark_id -> LandmarkDefinition
        self._landmarks: Dict[str, Dict[str, LandmarkDefinition]] = {}
        # session_id -> DriftState
        self._session_states: Dict[str, DriftState] = {}
        # session_id -> latest sequence number
        self._latest_sequences: Dict[str, int] = {}
        # session_id -> set of verified landmark_ids
        self._verified_landmarks: Dict[str, set[str]] = {}
        # session_id -> latest LandmarkVerificationRecord
        self._latest_verifications: Dict[str, LandmarkVerificationRecord] = {}
        # (session_id, landmark_id) -> list of recent LandmarkObservation for dwell stability
        self._dwell_buffers: Dict[Tuple[str, str], List[LandmarkObservation]] = {}

        self._in_transaction: bool = False

    @property
    def name(self) -> str:
        return SERVICE_NAME

    @property
    def dependencies(self) -> tuple[str, ...]:
        return ("dispatcher", "registration_service")

    @property
    def state(self) -> ServiceState:
        return self._state

    @property
    def resources(self) -> OwnedResourceSet:
        if self._resources is None:
            raise DriftLifecycleError("Service has not been initialized; resources unavailable")
        return self._resources

    @property
    def active_sessions_count(self) -> int:
        return len(self._session_states)

    def initialize(self, context: RuntimeContext) -> None:
        """Initialize drift service and acquire 4 structural handles."""
        if self._state not in (ServiceState.UNINITIALIZED, ServiceState.STOPPED):
            raise DriftLifecycleError(f"Cannot initialize DriftService in state {self._state.name}")

        self._context = context
        self._epoch_id = context.epoch_id
        self._resources = OwnedResourceSet(self.name, self._epoch_id)

        for res_id in STRUCTURAL_RESOURCE_IDS:
            self._resources.acquire(res_id)

        # Register Dispatcher Routes
        if self._dispatcher is not None:
            self._dispatcher.register_command_handler("drift.evaluate", self.handle_evaluate_command, self.name)
            self._dispatcher.register_query_handler("drift.status.get", self.handle_get_status_query, self.name)
            self._dispatcher.register_query_handler("drift.landmarks.get", self.handle_get_landmarks_query, self.name)

        self._state = ServiceState.INITIALIZED

    def start(self) -> None:
        """Transition service to STARTED."""
        if self._state != ServiceState.INITIALIZED:
            raise DriftLifecycleError(f"Cannot start DriftService in state {self._state.name}")

        self._state = ServiceState.STARTED
        self._emit_event("drift.started", {"epoch_id": self._epoch_id})

    def stop(self) -> None:
        """Clear transient state and release 4 handles cleanly."""
        if self._state in (ServiceState.STOPPED, ServiceState.UNINITIALIZED):
            return

        if self._in_transaction:
            raise DriftLifecycleError("Cannot stop DriftService during an active transaction")

        self._in_transaction = True
        failures: list[DeviceShutdownFailureRecord] = []
        try:
            self.clear()

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
                raise DriftShutdownError(
                    f"DriftService teardown encountered {len(failures)} resource failure(s)",
                    failures,
                )

            self._state = ServiceState.STOPPED
        finally:
            self._in_transaction = False

    def health(self) -> ServiceHealth:
        """Return in-process health snapshot."""
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
                message="Structural resource tracking desynchronized",
                timestamp_utc=now_utc,
            )

        return ServiceHealth(
            name=self.name,
            status=HealthStatus.HEALTHY,
            message=f"Active drift sessions: {len(self._session_states)}",
            timestamp_utc=now_utc,
        )

    # -------------------------------------------------------------------------
    # Core Drift & Landmark Operations
    # -------------------------------------------------------------------------

    def bind_landmarks(
        self,
        session_id: str,
        landmarks: Tuple[LandmarkDefinition, ...],
    ) -> None:
        """Bind planned anatomical landmarks to a session for drift monitoring.

        Requires an active verified M13 registration for the session.
        Sets initial session state to READY (never prematurely STABLE).
        """
        if self._state != ServiceState.STARTED:
            raise DriftLifecycleError(f"Cannot bind landmarks in state {self._state.name}")
        if self._in_transaction:
            raise DriftLifecycleError("Reentrant call to bind_landmarks rejected")

        self._in_transaction = True
        try:
            # Session capacity check
            if session_id not in self._session_states and len(self._session_states) >= MAX_ACTIVE_DRIFT_SESSIONS:
                raise DriftCapacityError(f"Max active drift sessions ({MAX_ACTIVE_DRIFT_SESSIONS}) exceeded")

            # Landmark capacity check
            if len(landmarks) > MAX_LANDMARKS_PER_SESSION:
                raise DriftCapacityError(
                    f"Max landmarks per session ({MAX_LANDMARKS_PER_SESSION}) exceeded: got {len(landmarks)}"
                )

            if len(landmarks) == 0:
                raise DriftValidationError("Must bind at least one landmark definition")

            # Verify M13 registration is verified
            if self._registration_service is None or not self._registration_service.is_registration_verified(session_id):
                raise DriftRegistrationError(
                    f"Session {session_id!r} does not have a verified M13 registration; cannot bind landmarks"
                )

            # Validate unique landmark IDs
            l_dict: Dict[str, LandmarkDefinition] = {}
            for l in landmarks:
                if l.landmark_id in l_dict:
                    raise DriftValidationError(f"Duplicate landmark_id {l.landmark_id!r} found in definitions")
                l_dict[l.landmark_id] = l

            self._landmarks[session_id] = l_dict
            self._session_states[session_id] = DriftState.READY
            self._verified_landmarks[session_id] = set()

            self._emit_event(
                "drift.landmarks.bound",
                {
                    "session_id": session_id,
                    "landmark_count": len(landmarks),
                    "epoch_id": self._epoch_id,
                },
            )
        finally:
            self._in_transaction = False

    def evaluate(
        self,
        observation: LandmarkObservation,
        now_utc: Optional[str] = None,
    ) -> LandmarkVerificationRecord:
        """Evaluate a physical pointer observation against bound landmark definition and M13 transform."""
        if self._state != ServiceState.STARTED:
            raise DriftLifecycleError(f"Cannot evaluate landmark observation in state {self._state.name}")
        if self._in_transaction:
            raise DriftLifecycleError("Reentrant call to evaluate rejected")

        self._in_transaction = True
        try:
            session_id = observation.session_id
            landmark_id = observation.landmark_id

            # 1. Verify session exists and is bound
            if session_id not in self._landmarks:
                raise DriftLifecycleError(f"No landmarks bound for session {session_id!r}; bind landmarks first")

            landmark_dict = self._landmarks[session_id]
            if landmark_id not in landmark_dict:
                raise DriftValidationError(f"Landmark {landmark_id!r} not found in session {session_id!r}")

            landmark = landmark_dict[landmark_id]

            # 2. Verify active M13 registration
            if self._registration_service is None:
                self._session_states[session_id] = DriftState.INTERLOCKED
                raise DriftRegistrationError("RegistrationService unavailable; drift interlocked")

            reg = self._registration_service.get_registration(session_id)
            if reg is None or reg.state != RegistrationState.VERIFIED or reg.transform is None:
                self._session_states[session_id] = DriftState.INTERLOCKED
                raise DriftRegistrationError(
                    f"Registration for session {session_id!r} is not verified; drift interlocked"
                )

            # 3. Epoch verification
            if observation.epoch_id != reg.epoch_id or observation.epoch_id != self._epoch_id:
                self._session_states[session_id] = DriftState.INTERLOCKED
                raise DriftEpochMismatchError(
                    f"Observation epoch ({observation.epoch_id}) does not match registration epoch ({reg.epoch_id})"
                )

            # 4. Sequence number monotonicity
            last_seq = self._latest_sequences.get(session_id)
            if last_seq is not None and observation.sequence_number <= last_seq:
                raise DriftSequenceError(
                    f"Non-monotonic sequence number {observation.sequence_number} <= previous {last_seq} "
                    f"for session {session_id!r}"
                )

            self._latest_sequences[session_id] = observation.sequence_number

            # 5. Dwell history retrieval & buffering
            buffer_key = (session_id, landmark_id)
            if buffer_key not in self._dwell_buffers:
                self._dwell_buffers[buffer_key] = []
            dwell_buf = self._dwell_buffers[buffer_key]

            # Run evaluation engine
            verification = DriftEvaluator.evaluate(
                observation=observation,
                landmark=landmark,
                transform=reg.transform,
                dwell_history=dwell_buf if dwell_buf else None,
                now_utc=now_utc,
            )

            # Append to dwell buffer (keep last MAX_DWELL_BUFFER_SAMPLES)
            dwell_buf.append(observation)
            if len(dwell_buf) > MAX_DWELL_BUFFER_SAMPLES:
                dwell_buf.pop(0)

            # Track previous state for transition event detection
            prev_state = self._session_states.get(session_id, DriftState.READY)

            # Enforce latching state for DRIFT_EXCEEDED and INTERLOCKED
            # Ordinary landmark evaluation MUST NOT automatically recover from DRIFT_EXCEEDED or INTERLOCKED
            if prev_state in (DriftState.DRIFT_EXCEEDED, DriftState.INTERLOCKED):
                new_state = prev_state
            else:
                new_state = verification.state

            # Update session state & verification tracking
            self._session_states[session_id] = new_state
            self._latest_verifications[session_id] = verification
            if verification.passed and new_state == DriftState.STABLE:
                self._verified_landmarks[session_id].add(landmark_id)

            # Emit transition-only events
            self._emit_transition_events(session_id, prev_state, new_state, verification)

            return verification
        finally:
            self._in_transaction = False

    def get_drift_status(self, session_id: str) -> DriftStatusRecord:
        """Query active registration drift monitoring status snapshot."""
        now_utc = datetime.now(timezone.utc).isoformat()
        state = self._session_states.get(session_id, DriftState.UNINITIALIZED)
        l_dict = self._landmarks.get(session_id, {})
        v_set = self._verified_landmarks.get(session_id, set())
        last_verif = self._latest_verifications.get(session_id)
        is_safe = state in (DriftState.READY, DriftState.STABLE)

        return DriftStatusRecord(
            session_id=session_id,
            epoch_id=self._epoch_id,
            state=state,
            bound_landmark_count=len(l_dict),
            verified_landmark_count=len(v_set),
            last_verification=last_verif,
            is_safe=is_safe,
            updated_at_utc=now_utc,
        )

    def get_bound_landmarks(self, session_id: str) -> Tuple[LandmarkDefinition, ...]:
        """Retrieve bound landmark definitions for a session."""
        l_dict = self._landmarks.get(session_id)
        return tuple(l_dict.values()) if l_dict else ()

    def evict_session(self, session_id: str, capability: Optional[Any] = None) -> bool:
        """Evict session-scoped landmark definitions, observations, and drift state, releasing capacity (M26)."""
        if self._in_transaction:
            raise DriftLifecycleError("Reentrant call to evict_session rejected")
        evicted = False
        if session_id in self._session_states:
            del self._session_states[session_id]
            evicted = True
        if session_id in self._landmarks:
            del self._landmarks[session_id]
            evicted = True
        if session_id in self._latest_sequences:
            del self._latest_sequences[session_id]
            evicted = True
        if session_id in self._verified_landmarks:
            del self._verified_landmarks[session_id]
            evicted = True
        if session_id in self._latest_verifications:
            del self._latest_verifications[session_id]
            evicted = True
        dwell_keys = [k for k in self._dwell_buffers if k[0] == session_id]
        for k in dwell_keys:
            del self._dwell_buffers[k]
            evicted = True
        return evicted

    def clear(self) -> None:

        """Clear all session states and telemetry buffers."""
        self._landmarks.clear()
        self._session_states.clear()
        self._latest_sequences.clear()
        self._verified_landmarks.clear()
        self._latest_verifications.clear()
        self._dwell_buffers.clear()

    # -------------------------------------------------------------------------
    # Transition Event Emission (Low-Frequency Transitions Only)
    # -------------------------------------------------------------------------

    def _emit_transition_events(
        self,
        session_id: str,
        prev_state: DriftState,
        new_state: DriftState,
        verification: LandmarkVerificationRecord,
    ) -> None:
        """Emit state-transition events only — zero repeated steady-state events.

        Transitions:
            READY -> STABLE:        drift.landmark.verified
            WARNING -> STABLE:      drift.landmark.verified & drift.warning.cleared
            STABLE -> WARNING:      drift.warning.entered
            ANY -> DRIFT_EXCEEDED:  drift.breached
            ANY -> INTERLOCKED:     drift.interlock.triggered
        """
        if new_state == prev_state:
            return  # Zero repeated events for steady state

        base_payload: Dict[str, Any] = {
            "session_id": session_id,
            "record_id": verification.record_id,
            "landmark_id": verification.landmark_id,
            "epoch_id": self._epoch_id,
            "residual_error_mm": verification.residual_error_mm,
            "effective_tolerance_mm": verification.effective_tolerance_mm,
            "previous_state": prev_state.value,
            "new_state": new_state.value,
        }

        if (prev_state == DriftState.READY and new_state == DriftState.STABLE) or (
            prev_state == DriftState.WARNING and new_state == DriftState.STABLE
        ):
            self._emit_event("drift.landmark.verified", base_payload)
            if prev_state == DriftState.WARNING:
                self._emit_event("drift.warning.cleared", base_payload)

        elif prev_state == DriftState.STABLE and new_state == DriftState.WARNING:
            self._emit_event("drift.warning.entered", base_payload)

        elif new_state == DriftState.DRIFT_EXCEEDED:
            # Emits ONLY drift.breached (zero drift.interlock.triggered)
            self._emit_event("drift.breached", base_payload)

        elif new_state == DriftState.INTERLOCKED:
            self._emit_event("drift.interlock.triggered", base_payload)

    # -------------------------------------------------------------------------
    # Helper & Protocol Handlers
    # -------------------------------------------------------------------------

    def _emit_event(self, topic: str, payload: Mapping[str, Any]) -> None:
        """Emit protocol event over dispatcher."""
        env = create_event(
            message_name=topic,
            source=self.name,
            payload=dict(payload),
        )
        if self._dispatcher is not None and getattr(self._dispatcher, "state", None) == DispatcherState.STARTED:
            self._dispatcher.dispatch(env)

    def handle_evaluate_command(self, command_envelope: MessageEnvelope) -> MessageEnvelope:
        """Handle drift.evaluate command."""
        payload = command_envelope.payload
        session_id = payload.get("session_id")
        landmark_id = payload.get("landmark_id")
        observed_pt = payload.get("observed_point_mm")
        confidence = payload.get("confidence", 1.0)
        uncertainty = payload.get("uncertainty_mm", 0.5)
        seq = payload.get("sequence_number", 1)
        epoch = payload.get("epoch_id", self._epoch_id)
        ts = payload.get("timestamp_utc") or datetime.now(timezone.utc).isoformat()
        frame = payload.get("coordinate_frame", DEFAULT_PATIENT_TRACKER_FRAME)

        if not session_id or not landmark_id or not observed_pt:
            return create_error_response(
                command_envelope, self.name, "ERR_INVALID_ARGS", "Missing session_id, landmark_id, or observed_point_mm"
            )

        try:
            obs = LandmarkObservation(
                landmark_id=str(landmark_id),
                session_id=str(session_id),
                epoch_id=int(epoch),
                sequence_number=int(seq),
                observed_point_mm=tuple(float(v) for v in observed_pt),  # type: ignore
                confidence=float(confidence),
                uncertainty_mm=float(uncertainty),
                timestamp_utc=str(ts),
                coordinate_frame=str(frame),
            )

            record = self.evaluate(obs)

            resp_payload: dict[str, Any] = {
                "record_id": record.record_id,
                "session_id": record.session_id,
                "landmark_id": record.landmark_id,
                "residual_error_mm": record.residual_error_mm,
                "effective_tolerance_mm": record.effective_tolerance_mm,
                "state": record.state.value,
                "passed": record.passed,
                "evaluated_at_utc": record.evaluated_at_utc,
                "xr_presentation": {
                    "observed_point_m": list(convert_point_mm_to_meters(record.observed_point_mm)),
                    "expected_point_m": list(convert_point_mm_to_meters(record.expected_point_mm)),
                },
            }
            return create_response(command_envelope, self.name, payload=resp_payload)
        except Exception as e:
            raw_err = str(e)
            redacted = self._secret_filter.redact(raw_err) if self._secret_filter else raw_err
            return create_error_response(command_envelope, self.name, _format_error_code(type(e).__name__), redacted)

    def handle_get_status_query(self, query_envelope: MessageEnvelope) -> MessageEnvelope:
        """Handle drift.status.get query."""
        session_id = query_envelope.payload.get("session_id")
        if not session_id:
            return create_error_response(query_envelope, self.name, "ERR_INVALID_ARGS", "Missing session_id")

        status = self.get_drift_status(str(session_id))
        resp_payload: dict[str, Any] = {
            "session_id": status.session_id,
            "epoch_id": status.epoch_id,
            "state": status.state.value,
            "bound_landmark_count": status.bound_landmark_count,
            "verified_landmark_count": status.verified_landmark_count,
            "is_safe": status.is_safe,
            "updated_at_utc": status.updated_at_utc,
        }
        return create_response(query_envelope, self.name, payload=resp_payload)

    def handle_get_landmarks_query(self, query_envelope: MessageEnvelope) -> MessageEnvelope:
        """Handle drift.landmarks.get query."""
        session_id = query_envelope.payload.get("session_id")
        if not session_id:
            return create_error_response(query_envelope, self.name, "ERR_INVALID_ARGS", "Missing session_id")

        landmarks = self.get_bound_landmarks(str(session_id))
        l_list: list[dict[str, Any]] = []
        for lm in landmarks:
            l_list.append({
                "landmark_id": lm.landmark_id,
                "name": lm.name,
                "planned_point_mm": list(lm.planned_point_mm),
                "max_tolerance_mm": lm.max_tolerance_mm,
                "min_confidence": lm.min_confidence,
                "max_uncertainty_mm": lm.max_uncertainty_mm,
            })

        resp_payload: dict[str, Any] = {
            "session_id": str(session_id),
            "landmarks": l_list,
            "landmark_count": len(l_list),
        }
        return create_response(query_envelope, self.name, payload=resp_payload)
