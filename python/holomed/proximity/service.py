# -*- coding: utf-8 -*-
"""Proximity Monitoring Service Implementing IService for M15.

Surgical proximity monitoring, safety exclusion zone evaluation,
and critical structure protection foundation.
"""

from __future__ import annotations

from datetime import datetime, timezone
import math
from typing import Any, Dict, List, Mapping, Optional, Tuple
import uuid

from holomed.core.dispatcher import MessageDispatcher
from holomed.core.models import DispatcherState
from holomed.devices.models import DeviceShutdownFailureRecord
from holomed.planning.models import SafetyExclusionZone
from holomed.proximity.constants import (
    DEFAULT_PATIENT_TRACKER_FRAME,
    MAX_ACTIVE_PROXIMITY_SESSIONS,
    MAX_MONITORED_ZONES,
    MAX_REGISTRATION_ERROR_MM,
    MAX_STATIC_MARGIN_MM,
    SERVICE_NAME,
)
from holomed.proximity.evaluator import ProximityEvaluator
from holomed.proximity.exceptions import (
    ProximityCapacityError,
    ProximityError,
    ProximityInterlockError,
    ProximityLifecycleError,
    ProximityRegistrationError,
    ProximitySequenceError,
    ProximityShutdownError,
    ProximityValidationError,
)
from holomed.proximity.geometry import convert_mm_to_meters, convert_point_mm_to_meters
from holomed.proximity.models import (
    ProximityEvaluationRecord,
    ProximitySeverity,
    ProximityState,
    ProximityStatusRecord,
    ToolClearanceGeometry,
    ZoneBreachRecord,
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
    "proximity.registry",
    "proximity.evaluator",
    "proximity.zones",
    "proximity.interlock",
)


def _format_error_code(exc_type_name: str) -> str:
    """Format exception class name into protocol-compliant ^ERR_[A-Z0-9_]+$ error code."""
    clean = exc_type_name.upper().replace("ERROR", "")
    return f"ERR_{clean}" if not clean.startswith("ERR_") else clean


class ProximityService(IService):
    """Surgical Proximity Monitoring & Safety Exclusion Zone Protection Service."""

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
        # session_id -> list of SafetyExclusionZone
        self._monitored_zones: Dict[str, Tuple[SafetyExclusionZone, ...]] = {}
        # session_id -> registration_error_mm
        self._registration_errors: Dict[str, float] = {}
        # session_id -> static_margin_mm
        self._static_margins: Dict[str, float] = {}
        # (session_id, instrument_id) -> latest ToolClearanceGeometry
        self._latest_geometries: Dict[Tuple[str, str], ToolClearanceGeometry] = {}
        # (session_id, instrument_id) -> latest sequence_number
        self._latest_sequences: Dict[Tuple[str, str], int] = {}
        # session_id -> latest ProximityEvaluationRecord
        self._latest_evaluations: Dict[str, ProximityEvaluationRecord] = {}
        # session_id -> ProximityState
        self._session_states: Dict[str, ProximityState] = {}
        # session_id -> active instrument_id
        self._active_instruments: Dict[str, str] = {}
        # (session_id, zone_id) -> (clearance_mm, timestamp_utc) for rate tracking
        self._clearance_history: Dict[Tuple[str, str], Tuple[float, str]] = {}

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
            raise ProximityLifecycleError("Service has not been initialized; resources unavailable")
        return self._resources

    @property
    def active_sessions_count(self) -> int:
        return len(self._session_states)

    def initialize(self, context: RuntimeContext) -> None:
        """Initialize proximity service and acquire 4 structural handles."""
        if self._state not in (ServiceState.UNINITIALIZED, ServiceState.STOPPED):
            raise ProximityLifecycleError(f"Cannot initialize ProximityService in state {self._state.name}")

        self._context = context
        self._epoch_id = context.epoch_id
        self._resources = OwnedResourceSet(self.name, self._epoch_id)

        for res_id in STRUCTURAL_RESOURCE_IDS:
            self._resources.acquire(res_id)

        # Register Dispatcher Routes
        if self._dispatcher is not None:
            self._dispatcher.register_command_handler("proximity.evaluate", self.handle_evaluate_command, self.name)
            self._dispatcher.register_query_handler("proximity.status.get", self.handle_get_status_query, self.name)
            self._dispatcher.register_query_handler("proximity.zones.get", self.handle_get_zones_query, self.name)

        self._state = ServiceState.INITIALIZED

    def start(self) -> None:
        """Transition service to STARTED."""
        if self._state != ServiceState.INITIALIZED:
            raise ProximityLifecycleError(f"Cannot start ProximityService in state {self._state.name}")

        self._state = ServiceState.STARTED
        self._emit_event("proximity.started", {"epoch_id": self._epoch_id})

    def stop(self) -> None:
        """Clear transient state and release 4 handles cleanly."""
        if self._state in (ServiceState.STOPPED, ServiceState.UNINITIALIZED):
            return

        if self._in_transaction:
            raise ProximityLifecycleError("Cannot stop ProximityService during an active transaction")

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
                raise ProximityShutdownError(
                    f"ProximityService teardown encountered {len(failures)} resource failure(s)",
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
            message=f"Active proximity sessions: {len(self._session_states)}",
            timestamp_utc=now_utc,
        )

    # -------------------------------------------------------------------------
    # Core Proximity Operations
    # -------------------------------------------------------------------------

    def bind_zones(
        self,
        session_id: str,
        zones: Tuple[SafetyExclusionZone, ...],
        registration_error_mm: float,
        static_margin_mm: float = 0.0,
    ) -> None:
        """Bind safety exclusion zones to a session for proximity monitoring.

        Requires a verified M13 registration for the session.
        """
        if self._state != ServiceState.STARTED:
            raise ProximityLifecycleError(f"Cannot bind zones in state {self._state.name}")
        if self._in_transaction:
            raise ProximityLifecycleError("Reentrant call to bind_zones rejected")

        self._in_transaction = True
        try:
            # Session capacity check
            if session_id not in self._session_states and len(self._session_states) >= MAX_ACTIVE_PROXIMITY_SESSIONS:
                raise ProximityCapacityError(
                    f"Max active proximity sessions ({MAX_ACTIVE_PROXIMITY_SESSIONS}) exceeded"
                )

            # Zone capacity check
            if len(zones) > MAX_MONITORED_ZONES:
                raise ProximityCapacityError(
                    f"Max monitored zones ({MAX_MONITORED_ZONES}) exceeded: got {len(zones)}"
                )

            # Validate registration_error_mm
            if not isinstance(registration_error_mm, (int, float)) or not math.isfinite(registration_error_mm):
                raise ProximityValidationError(
                    f"registration_error_mm must be finite float, got {registration_error_mm!r}"
                )
            if registration_error_mm < 0.0:
                raise ProximityValidationError("registration_error_mm must be non-negative")
            if registration_error_mm > MAX_REGISTRATION_ERROR_MM:
                raise ProximityValidationError(
                    f"registration_error_mm ({registration_error_mm:.2f}) exceeds maximum ({MAX_REGISTRATION_ERROR_MM})"
                )

            # Validate static_margin_mm
            if not isinstance(static_margin_mm, (int, float)) or not math.isfinite(static_margin_mm):
                raise ProximityValidationError(
                    f"static_margin_mm must be finite float, got {static_margin_mm!r}"
                )
            if static_margin_mm < 0.0:
                raise ProximityValidationError("static_margin_mm must be non-negative")
            if static_margin_mm > MAX_STATIC_MARGIN_MM:
                raise ProximityValidationError(
                    f"static_margin_mm ({static_margin_mm:.2f}) exceeds maximum ({MAX_STATIC_MARGIN_MM})"
                )

            # Check verified M13 registration requirement
            if self._registration_service is None or not self._registration_service.is_registration_verified(session_id):
                raise ProximityRegistrationError(
                    f"Session {session_id!r} does not have a verified M13 registration; proximity monitoring cannot bind"
                )

            # Validate unique zone IDs
            zone_ids = [z.zone_id for z in zones]
            if len(zone_ids) != len(set(zone_ids)):
                raise ProximityValidationError("Duplicate zone_id found in exclusion zones")

            self._monitored_zones[session_id] = zones
            self._registration_errors[session_id] = registration_error_mm
            self._static_margins[session_id] = static_margin_mm
            self._session_states[session_id] = ProximityState.CLEAR

            self._emit_event(
                "proximity.zones.bound",
                {
                    "session_id": session_id,
                    "zone_count": len(zones),
                    "registration_error_mm": registration_error_mm,
                    "static_margin_mm": static_margin_mm,
                    "epoch_id": self._epoch_id,
                },
            )
        finally:
            self._in_transaction = False

    def submit_geometry(self, tool_geom: ToolClearanceGeometry) -> None:
        """Validate and store the latest tool clearance geometry for proximity evaluation."""
        if self._state != ServiceState.STARTED:
            raise ProximityLifecycleError(f"Cannot submit geometry in state {self._state.name}")
        if self._in_transaction:
            raise ProximityLifecycleError("Reentrant call to submit_geometry rejected")

        self._in_transaction = True
        try:
            session_id = tool_geom.session_id
            instrument_id = tool_geom.instrument_id
            key = (session_id, instrument_id)

            # Validate session exists
            if session_id not in self._session_states:
                raise ProximityLifecycleError(
                    f"No proximity zones bound for session {session_id!r}; bind zones first"
                )

            # Sequence monotonicity check
            last_seq = self._latest_sequences.get(key)
            if last_seq is not None and tool_geom.sequence_number <= last_seq:
                raise ProximitySequenceError(
                    f"Non-monotonic sequence number {tool_geom.sequence_number} <= previous {last_seq} "
                    f"for instrument {instrument_id!r}"
                )

            # Store validated geometry
            self._latest_geometries[key] = tool_geom
            self._latest_sequences[key] = tool_geom.sequence_number
            self._active_instruments[session_id] = instrument_id
        finally:
            self._in_transaction = False

    def evaluate(
        self,
        session_id: str,
        instrument_id: Optional[str] = None,
        now_utc: Optional[str] = None,
    ) -> ProximityEvaluationRecord:
        """Evaluate proximity of the latest stored geometry against bound exclusion zones."""
        if self._state != ServiceState.STARTED:
            raise ProximityLifecycleError(f"Cannot evaluate proximity in state {self._state.name}")
        if self._in_transaction:
            raise ProximityLifecycleError("Reentrant call to evaluate rejected")

        self._in_transaction = True
        try:
            # Verify session
            if session_id not in self._session_states:
                raise ProximityLifecycleError(f"No proximity zones bound for session {session_id!r}")

            # Verify active M13 registration
            if self._registration_service is not None:
                reg = self._registration_service.get_registration(session_id)
                if reg is None or reg.state != RegistrationState.VERIFIED:
                    # Fail closed to INTERLOCKED
                    self._session_states[session_id] = ProximityState.INTERLOCKED
                    raise ProximityRegistrationError(
                        f"Registration for session {session_id!r} is not verified; proximity interlocked"
                    )

            # Resolve instrument
            target_inst = instrument_id or self._active_instruments.get(session_id)
            if not target_inst or (session_id, target_inst) not in self._latest_geometries:
                raise ProximityValidationError(f"No tool geometry submitted for session {session_id!r}")

            tool_geom = self._latest_geometries[(session_id, target_inst)]
            zones = self._monitored_zones[session_id]
            reg_error = self._registration_errors[session_id]
            static_margin = self._static_margins[session_id]

            # Build previous clearance history for rate calculation
            prev_clearances: Dict[str, Tuple[float, str]] = {}
            for zone in zones:
                hist_key = (session_id, zone.zone_id)
                if hist_key in self._clearance_history:
                    prev_clearances[zone.zone_id] = self._clearance_history[hist_key]

            # Run evaluator
            evaluation = ProximityEvaluator.evaluate(
                tool_geom=tool_geom,
                zones=zones,
                registration_error_mm=reg_error,
                static_margin_mm=static_margin,
                previous_clearances=prev_clearances if prev_clearances else None,
                now_utc=now_utc,
            )

            # Update clearance history for next rate calculation
            for zr in evaluation.zone_results:
                self._clearance_history[(session_id, zr.zone_id)] = (
                    zr.effective_clearance_mm,
                    evaluation.evaluated_at_utc,
                )

            # Track previous state for transition event detection
            prev_state = self._session_states.get(session_id, ProximityState.CLEAR)

            # Update session state
            self._session_states[session_id] = evaluation.worst_state
            self._latest_evaluations[session_id] = evaluation

            # Emit state-transition events (low-frequency only)
            self._emit_transition_events(session_id, prev_state, evaluation)

            return evaluation
        finally:
            self._in_transaction = False

    def get_proximity_status(self, session_id: str) -> ProximityStatusRecord:
        """Query the active proximity monitoring status for a session."""
        now_utc = datetime.now(timezone.utc).isoformat()
        state = self._session_states.get(session_id, ProximityState.CLEAR)
        zones = self._monitored_zones.get(session_id, ())
        last_eval = self._latest_evaluations.get(session_id)
        active_inst = self._active_instruments.get(session_id)
        is_monitoring = session_id in self._monitored_zones and self._state == ServiceState.STARTED

        return ProximityStatusRecord(
            session_id=session_id,
            epoch_id=self._epoch_id,
            state=state,
            monitored_zone_count=len(zones),
            active_instrument_id=active_inst,
            last_evaluation=last_eval,
            is_monitoring_active=is_monitoring,
            updated_at_utc=now_utc,
        )

    def get_monitored_zones(self, session_id: str) -> Tuple[SafetyExclusionZone, ...]:
        """Retrieve bound exclusion zones for a session."""
        return self._monitored_zones.get(session_id, ())

    def evict_session(self, session_id: str, capability: Optional[Any] = None) -> bool:
        """Evict session-scoped proximity monitoring state and geometries, releasing capacity (M26)."""
        if self._in_transaction:
            raise ProximityLifecycleError("Reentrant call to evict_session rejected")
        evicted = False
        if session_id in self._session_states:
            del self._session_states[session_id]
            evicted = True
        if session_id in self._monitored_zones:
            del self._monitored_zones[session_id]
            evicted = True
        if session_id in self._registration_errors:
            del self._registration_errors[session_id]
            evicted = True
        if session_id in self._static_margins:
            del self._static_margins[session_id]
            evicted = True
        geom_keys = [k for k in self._latest_geometries if k[0] == session_id]
        for k in geom_keys:
            del self._latest_geometries[k]
            evicted = True
        seq_keys = [k for k in self._latest_sequences if k[0] == session_id]
        for k in seq_keys:
            del self._latest_sequences[k]
            evicted = True
        if session_id in self._latest_evaluations:
            del self._latest_evaluations[session_id]
            evicted = True
        if session_id in self._active_instruments:
            del self._active_instruments[session_id]
            evicted = True
        hist_keys = [k for k in self._clearance_history if k[0] == session_id]
        for k in hist_keys:
            del self._clearance_history[k]
            evicted = True
        return evicted

    def clear(self) -> None:

        """Clear all session states and stored telemetry."""
        self._monitored_zones.clear()
        self._registration_errors.clear()
        self._static_margins.clear()
        self._latest_geometries.clear()
        self._latest_sequences.clear()
        self._latest_evaluations.clear()
        self._session_states.clear()
        self._active_instruments.clear()
        self._clearance_history.clear()

    # -------------------------------------------------------------------------
    # State-Transition Event Emission (Low-Frequency Only)
    # -------------------------------------------------------------------------

    def _emit_transition_events(
        self,
        session_id: str,
        prev_state: ProximityState,
        evaluation: ProximityEvaluationRecord,
    ) -> None:
        """Emit state-transition events only — never per-frame telemetry.

        Transitions:
            CLEAR -> WARNING:       proximity.warning.entered
            WARNING -> CLEAR:       proximity.warning.cleared
            ANY -> BREACHED:        proximity.zone.breached
            BREACHED -> CLEAR:      proximity.zone.cleared
            ANY -> INTERLOCKED:     proximity.interlock.triggered
        """
        new_state = evaluation.worst_state
        if new_state == prev_state:
            return  # No transition

        base_payload: Dict[str, Any] = {
            "session_id": session_id,
            "record_id": evaluation.record_id,
            "epoch_id": self._epoch_id,
            "previous_state": prev_state.value,
            "new_state": new_state.value,
        }

        if prev_state == ProximityState.CLEAR and new_state == ProximityState.WARNING:
            self._emit_event("proximity.warning.entered", base_payload)

        elif prev_state == ProximityState.WARNING and new_state == ProximityState.CLEAR:
            self._emit_event("proximity.warning.cleared", base_payload)

        elif new_state == ProximityState.BREACHED:
            # ANY -> BREACHED
            breach_zones = [zr.zone_id for zr in evaluation.zone_results if zr.state == ProximityState.BREACHED]
            payload = {**base_payload, "breached_zones": breach_zones}
            self._emit_event("proximity.zone.breached", payload)

        elif prev_state == ProximityState.BREACHED and new_state == ProximityState.CLEAR:
            self._emit_event("proximity.zone.cleared", base_payload)

        elif new_state == ProximityState.INTERLOCKED:
            # ANY -> INTERLOCKED
            interlock = ProximityEvaluator.generate_interlock(evaluation, session_id, self._epoch_id)
            interlock_payload = {
                **base_payload,
                "interlock_id": interlock.interlock_id if interlock else "unknown",
                "severity": interlock.severity.value if interlock else "BLOCKING",
            }
            self._emit_event("proximity.interlock.triggered", interlock_payload)

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
        """Handle proximity.evaluate command."""
        payload = command_envelope.payload
        session_id = payload.get("session_id")
        instrument_id = payload.get("instrument_id")
        now_utc = payload.get("now_utc")

        if not session_id:
            return create_error_response(command_envelope, self.name, "ERR_INVALID_ARGS", "Missing session_id")

        try:
            evaluation = self.evaluate(
                str(session_id),
                instrument_id=str(instrument_id) if instrument_id else None,
                now_utc=str(now_utc) if now_utc else None,
            )

            resp_payload: dict[str, Any] = {
                "record_id": evaluation.record_id,
                "session_id": evaluation.session_id,
                "instrument_id": evaluation.instrument_id,
                "worst_state": evaluation.worst_state.value,
                "is_safe": evaluation.is_safe,
                "zone_count": len(evaluation.zone_results),
                "evaluated_at_utc": evaluation.evaluated_at_utc,
            }

            # XR presentation (meters)
            if (evaluation.session_id, evaluation.instrument_id) in self._latest_geometries:
                geom = self._latest_geometries[(evaluation.session_id, evaluation.instrument_id)]
                resp_payload["xr_presentation"] = {
                    "tip_position_m": list(convert_point_mm_to_meters(geom.tip_position_mm)),
                }

            return create_response(command_envelope, self.name, payload=resp_payload)
        except Exception as e:
            raw_err = str(e)
            redacted = self._secret_filter.redact(raw_err) if self._secret_filter else raw_err
            return create_error_response(command_envelope, self.name, _format_error_code(type(e).__name__), redacted)

    def handle_get_status_query(self, query_envelope: MessageEnvelope) -> MessageEnvelope:
        """Handle proximity.status.get query."""
        session_id = query_envelope.payload.get("session_id")
        if not session_id:
            return create_error_response(query_envelope, self.name, "ERR_INVALID_ARGS", "Missing session_id")

        status = self.get_proximity_status(str(session_id))
        resp_payload: dict[str, Any] = {
            "session_id": status.session_id,
            "state": status.state.value,
            "monitored_zone_count": status.monitored_zone_count,
            "active_instrument_id": status.active_instrument_id,
            "is_monitoring_active": status.is_monitoring_active,
            "updated_at_utc": status.updated_at_utc,
        }
        return create_response(query_envelope, self.name, payload=resp_payload)

    def handle_get_zones_query(self, query_envelope: MessageEnvelope) -> MessageEnvelope:
        """Handle proximity.zones.get query."""
        session_id = query_envelope.payload.get("session_id")
        if not session_id:
            return create_error_response(query_envelope, self.name, "ERR_INVALID_ARGS", "Missing session_id")

        zones = self.get_monitored_zones(str(session_id))
        zone_list: list[dict[str, Any]] = []
        for z in zones:
            zone_list.append({
                "zone_id": z.zone_id,
                "anatomical_structure": z.anatomical_structure,
                "center_point_mm": list(z.center_point_mm),
                "bounding_radius_mm": z.bounding_radius_mm,
                "min_clearance_mm": z.min_clearance_mm,
                "severity_if_breached": z.severity_if_breached.value,
            })

        resp_payload: dict[str, Any] = {
            "session_id": str(session_id),
            "zones": zone_list,
            "zone_count": len(zone_list),
        }
        return create_response(query_envelope, self.name, payload=resp_payload)
