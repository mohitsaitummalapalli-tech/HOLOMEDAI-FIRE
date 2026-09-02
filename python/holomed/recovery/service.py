# -*- coding: utf-8 -*-
"""Spatial Re-Registration & Recovery Orchestration Service Implementing IService for M17."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, Mapping, Optional, Tuple
import uuid

from holomed.core.dispatcher import MessageDispatcher
from holomed.core.models import DispatcherState
from holomed.devices.models import DeviceShutdownFailureRecord
from holomed.drift.models import LandmarkDefinition
from holomed.drift.service import DriftService
from holomed.navigation.service import NavigationService
from holomed.planning.models import SafetyExclusionZone, TrajectoryPlan
from holomed.persistence.service import PersistenceService
from holomed.protocol.builders import (
    create_error_response,
    create_event,
    create_response,
)
from holomed.protocol.models import MessageEnvelope
from holomed.proximity.service import ProximityService
from holomed.recovery.constants import (
    MAX_ACTIVE_RECOVERY_SESSIONS,
    SERVICE_NAME,
    STRUCTURAL_RESOURCE_IDS,
)
from holomed.recovery.evaluator import RecoveryEvaluator
from holomed.recovery.exceptions import (
    RecoveryActivationError,
    RecoveryAuthorizationError,
    RecoveryCapacityError,
    RecoveryConsistencyError,
    RecoveryLifecycleError,
    RecoveryShutdownError,
    RecoveryValidationError,
)
from holomed.recovery.models import (
    RecoveryAuthorization,
    RecoveryState,
    RecoveryStatusRecord,
    RecoveryVerificationSnapshot,
    StagedRegistrationCandidate,
)
from holomed.registration.models import (
    FiducialCloud,
    FiducialPointPair,
    RegistrationState,
)
from holomed.registration.service import RegistrationService
from holomed.runtime.context import RuntimeContext
from holomed.runtime.logging import SecretFilter, StructuredLogger
from holomed.runtime.models import (
    HealthStatus,
    OwnedResourceSet,
    ServiceHealth,
)
from holomed.runtime.service import IService, ServiceState


def _format_error_code(exc_type_name: str) -> str:
    """Format exception class name into protocol-compliant ^ERR_[A-Z0-9_]+$ error code."""
    clean = exc_type_name.upper().replace("ERROR", "")
    return f"ERR_{clean}" if not clean.startswith("ERR_") else clean


class RecoveryService(IService):
    """Intraoperative Spatial Re-Registration & Recovery Orchestration Service."""

    def __init__(
        self,
        dispatcher: Optional[MessageDispatcher] = None,
        registration_service: Optional[RegistrationService] = None,
        drift_service: Optional[DriftService] = None,
        proximity_service: Optional[ProximityService] = None,
        navigation_service: Optional[NavigationService] = None,
        persistence_service: Optional[PersistenceService] = None,
        secret_filter: Optional[SecretFilter] = None,
        logger: Optional[StructuredLogger] = None,
    ) -> None:
        self._dispatcher = dispatcher
        self._registration_service = registration_service
        self._drift_service = drift_service
        self._proximity_service = proximity_service
        self._navigation_service = navigation_service
        self._persistence_service = persistence_service
        self._secret_filter = secret_filter
        self._logger = logger or StructuredLogger(SERVICE_NAME, secret_filter=secret_filter)

        self._state: ServiceState = ServiceState.UNINITIALIZED
        self._context: Optional[RuntimeContext] = None
        self._resources: Optional[OwnedResourceSet] = None
        self._epoch_id: int = 0

        # Session tracking state
        self._session_states: Dict[str, RecoveryState] = {}
        self._revisions: Dict[str, int] = {}
        self._staged_candidates: Dict[str, StagedRegistrationCandidate] = {}
        self._verifications: Dict[str, RecoveryVerificationSnapshot] = {}
        self._authorizations: Dict[str, RecoveryAuthorization] = {}
        self._checkpoint_pairs: Dict[str, Tuple[Tuple[float, float, float], Tuple[float, float, float]]] = {}
        self._latest_records: Dict[str, RecoveryStatusRecord] = {}

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
            raise RecoveryLifecycleError("Service has not been initialized; resources unavailable")
        return self._resources

    @property
    def active_sessions_count(self) -> int:
        return len(self._session_states)

    def initialize(self, context: RuntimeContext) -> None:
        """Initialize recovery service and acquire 4 structural handles."""
        if self._state not in (ServiceState.UNINITIALIZED, ServiceState.STOPPED):
            raise RecoveryLifecycleError(f"Cannot initialize RecoveryService in state {self._state.name}")

        self._context = context
        self._epoch_id = context.epoch_id
        self._resources = OwnedResourceSet(self.name, self._epoch_id)

        for res_id in STRUCTURAL_RESOURCE_IDS:
            self._resources.acquire(res_id)

        # Register Dispatcher Routes (M22: recovery.stage/verify/activate removed; query retained)
        if self._dispatcher is not None:
            self._dispatcher.register_query_handler("recovery.status.get", self.handle_get_status_query, self.name)

        self._state = ServiceState.INITIALIZED

    def start(self) -> None:
        """Transition service to STARTED."""
        if self._state != ServiceState.INITIALIZED:
            raise RecoveryLifecycleError(f"Cannot start RecoveryService in state {self._state.name}")

        self._state = ServiceState.STARTED
        self._emit_event("recovery.started", {"epoch_id": self._epoch_id, "state": ServiceState.STARTED.name})

    def stop(self) -> None:
        """Stop recovery service, clear transient session states, and release structural handles idempotently."""
        if self._state in (ServiceState.STOPPED, getattr(ServiceState, "UNINITIALIZED")):
            return

        if self._in_transaction:
            raise RecoveryLifecycleError("Cannot stop RecoveryService during an active transaction")

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
                        redacted = self._secret_filter.redact(raw_err) if self._secret_filter else raw_err
                        failures.append(
                            DeviceShutdownFailureRecord(
                                device_id=self.name,
                                error_type=type(e).__name__,
                                error_message=redacted,
                                execution_index=len(failures),
                                unreleased_resources=(handle.resource_id,),
                            )
                        )

            if failures:
                self._state = ServiceState.FAILED
                raise RecoveryShutdownError(
                    f"RecoveryService teardown encountered {len(failures)} resource failure(s)",
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
            message=f"Active recovery sessions: {len(self._session_states)}",
            timestamp_utc=now_utc,
        )

    # -------------------------------------------------------------------------
    # Core Spatial Recovery Operations
    # -------------------------------------------------------------------------

    def stage_candidate(
        self,
        session_id: str,
        plan_id: str,
        cloud: FiducialCloud,
        now_utc: Optional[str] = None,
        sequence_number: int = 1,
        capability: Any = None,
    ) -> StagedRegistrationCandidate:
        """Stage candidate fiducials and solve candidate transform entirely in isolated M17 memory."""
        if self._state != ServiceState.STARTED:
            raise RecoveryLifecycleError(f"Cannot stage candidate in state {self._state.name}")

        # Strict execution capability verification
        if capability is None:
            raise RecoveryAuthorizationError(
                "Direct uncoordinated call to stage_candidate rejected: missing execution capability"
            )
        if not getattr(capability, "is_active", False):
            raise RecoveryAuthorizationError("Execution capability is inactive, expired, or replayed")
        if getattr(capability, "session_id", None) != session_id:
            raise RecoveryAuthorizationError(
                f"Capability session mismatch: expected {session_id!r}, got {getattr(capability, 'session_id', None)!r}"
            )
        if getattr(capability, "action", None) != "RECOVERY_REORIENTATION":
            raise RecoveryAuthorizationError(
                f"Capability action mismatch: expected 'RECOVERY_REORIENTATION', got {getattr(capability, 'action', None)!r}"
            )
        if getattr(capability, "sequence_number", None) != sequence_number:
            raise RecoveryAuthorizationError(
                f"Capability sequence mismatch: expected {sequence_number}, got {getattr(capability, 'sequence_number', None)}"
            )
        if getattr(capability, "service_instance_id", None) != id(self):
            raise RecoveryAuthorizationError(
                f"Capability service binding mismatch: expected {id(self)}, got {getattr(capability, 'service_instance_id', None)}"
            )

        if self._in_transaction:
            raise RecoveryLifecycleError("Reentrant call to stage_candidate rejected")

        self._in_transaction = True
        try:
            # Check session capacity
            if session_id not in self._session_states and len(self._session_states) >= MAX_ACTIVE_RECOVERY_SESSIONS:
                self._session_states[session_id] = RecoveryState.BLOCKED
                raise RecoveryCapacityError(f"Max active recovery sessions ({MAX_ACTIVE_RECOVERY_SESSIONS}) exceeded")

            # Check latching states
            current_state = self._session_states.get(session_id, RecoveryState.IDLE)
            if current_state in (RecoveryState.FAILED, RecoveryState.BLOCKED):
                raise RecoveryLifecycleError(
                    f"Session {session_id!r} is in latching state {current_state.value}; explicit reset required"
                )

            # Solve and validate candidate transform in isolated memory (NO M13 MUTATION!)
            try:
                candidate = RecoveryEvaluator.stage_and_solve_candidate(
                    session_id=session_id,
                    plan_id=plan_id,
                    cloud=cloud,
                    epoch_id=self._epoch_id,
                    now_utc=now_utc,
                )
            except Exception as e:
                self._session_states[session_id] = RecoveryState.FAILED
                self._emit_event("recovery.failed", {"session_id": session_id, "reason": str(e), "stage": "STAGED"})
                raise

            self._staged_candidates[session_id] = candidate
            self._session_states[session_id] = RecoveryState.SOLVED
            if session_id not in self._revisions:
                self._revisions[session_id] = 0

            self._emit_event(
                "recovery.started",
                {
                    "session_id": session_id,
                    "plan_id": plan_id,
                    "point_count": len(cloud.pairs),
                    "candidate_fre_rms_mm": candidate.quality_report.fre_rms_mm,
                    "epoch_id": self._epoch_id,
                },
            )
            return candidate
        finally:
            self._in_transaction = False

    def verify_candidate(
        self,
        session_id: str,
        authorization: RecoveryAuthorization,
        checkpoint_plan_mm: Tuple[float, float, float],
        checkpoint_measured_mm: Tuple[float, float, float],
        now_utc: Optional[str] = None,
        sequence_number: int = 1,
        capability: Any = None,
    ) -> RecoveryVerificationSnapshot:
        """Verify physical checkpoint drift against the staged candidate transform."""
        if self._state != ServiceState.STARTED:
            raise RecoveryLifecycleError(f"Cannot verify candidate in state {self._state.name}")

        # Strict execution capability verification
        if capability is None:
            raise RecoveryAuthorizationError(
                "Direct uncoordinated call to verify_candidate rejected: missing execution capability"
            )
        if not getattr(capability, "is_active", False):
            raise RecoveryAuthorizationError("Execution capability is inactive, expired, or replayed")
        if getattr(capability, "session_id", None) != session_id:
            raise RecoveryAuthorizationError(
                f"Capability session mismatch: expected {session_id!r}, got {getattr(capability, 'session_id', None)!r}"
            )
        if getattr(capability, "action", None) != "RECOVERY_REORIENTATION":
            raise RecoveryAuthorizationError(
                f"Capability action mismatch: expected 'RECOVERY_REORIENTATION', got {getattr(capability, 'action', None)!r}"
            )
        if getattr(capability, "sequence_number", None) != sequence_number:
            raise RecoveryAuthorizationError(
                f"Capability sequence mismatch: expected {sequence_number}, got {getattr(capability, 'sequence_number', None)}"
            )
        if getattr(capability, "service_instance_id", None) != id(self):
            raise RecoveryAuthorizationError(
                f"Capability service binding mismatch: expected {id(self)}, got {getattr(capability, 'service_instance_id', None)}"
            )

        if self._in_transaction:
            raise RecoveryLifecycleError("Reentrant call to verify_candidate rejected")

        self._in_transaction = True
        try:
            current_state = self._session_states.get(session_id, RecoveryState.IDLE)
            if current_state in (RecoveryState.FAILED, RecoveryState.BLOCKED):
                raise RecoveryLifecycleError(
                    f"Session {session_id!r} is in latching state {current_state.value}; explicit reset required"
                )
            if current_state != RecoveryState.SOLVED or session_id not in self._staged_candidates:
                raise RecoveryLifecycleError(
                    f"Candidate must be in SOLVED state to verify; current state is {current_state.value}"
                )

            candidate = self._staged_candidates[session_id]

            # Validate operator authorization
            try:
                RecoveryEvaluator.validate_authorization(authorization, now_utc)
                snapshot = RecoveryEvaluator.verify_checkpoint(
                    candidate=candidate,
                    checkpoint_plan_mm=checkpoint_plan_mm,
                    checkpoint_measured_mm=checkpoint_measured_mm,
                    operator_id=authorization.operator_id,
                    now_utc=now_utc,
                )
            except Exception as e:
                self._session_states[session_id] = RecoveryState.FAILED
                self._emit_event("recovery.failed", {"session_id": session_id, "reason": str(e), "stage": "VERIFIED"})
                raise

            self._verifications[session_id] = snapshot
            self._authorizations[session_id] = authorization
            self._checkpoint_pairs[session_id] = (checkpoint_plan_mm, checkpoint_measured_mm)
            self._session_states[session_id] = RecoveryState.VERIFIED

            self._emit_event(
                "recovery.candidate.verified",
                {
                    "session_id": session_id,
                    "verification_id": snapshot.verification_id,
                    "operator_id": authorization.operator_id,
                    "measured_drift_error_mm": snapshot.measured_drift_error_mm,
                    "epoch_id": self._epoch_id,
                },
            )
            return snapshot
        finally:
            self._in_transaction = False

    def activate_recovery(
        self,
        session_id: str,
        plan_trajectory: Optional[TrajectoryPlan] = None,
        zones: Optional[Tuple[SafetyExclusionZone, ...]] = None,
        landmarks: Optional[Tuple[LandmarkDefinition, ...]] = None,
        registration_error_mm: float = 0.5,
        static_margin_mm: float = 0.0,
        now_utc: Optional[str] = None,
        sequence_number: int = 1,
        capability: Any = None,
    ) -> RecoveryStatusRecord:
        """Perform controlled sequential activation into M13 and rebind M16/M15/M14.

        On failure, M17 enters FAILED, refuses further activation, emits recovery.failed,
        and does not emit recovery.spatial.activated. M17 does not claim system-wide
        navigation blocking under the frozen M00–M16 architecture.
        """
        if self._state != ServiceState.STARTED:
            raise RecoveryLifecycleError(f"Cannot activate recovery in state {self._state.name}")

        # Strict execution capability verification
        if capability is None:
            raise RecoveryAuthorizationError(
                "Direct uncoordinated call to activate_recovery rejected: missing execution capability"
            )
        if not getattr(capability, "is_active", False):
            raise RecoveryAuthorizationError("Execution capability is inactive, expired, or replayed")
        if getattr(capability, "session_id", None) != session_id:
            raise RecoveryAuthorizationError(
                f"Capability session mismatch: expected {session_id!r}, got {getattr(capability, 'session_id', None)!r}"
            )
        if getattr(capability, "action", None) != "RECOVERY_REORIENTATION":
            raise RecoveryAuthorizationError(
                f"Capability action mismatch: expected 'RECOVERY_REORIENTATION', got {getattr(capability, 'action', None)!r}"
            )
        if getattr(capability, "sequence_number", None) != sequence_number:
            raise RecoveryAuthorizationError(
                f"Capability sequence mismatch: expected {sequence_number}, got {getattr(capability, 'sequence_number', None)}"
            )
        if getattr(capability, "service_instance_id", None) != id(self):
            raise RecoveryAuthorizationError(
                f"Capability service binding mismatch: expected {id(self)}, got {getattr(capability, 'service_instance_id', None)}"
            )

        if self._in_transaction:
            raise RecoveryLifecycleError("Reentrant call to activate_recovery rejected")

        self._in_transaction = True
        try:
            current_state = self._session_states.get(session_id, RecoveryState.IDLE)
            if current_state in (RecoveryState.FAILED, RecoveryState.BLOCKED):
                raise RecoveryLifecycleError(
                    f"Session {session_id!r} is in latching state {current_state.value}; explicit reset required"
                )
            if current_state != RecoveryState.VERIFIED or session_id not in self._staged_candidates:
                raise RecoveryLifecycleError(
                    f"Recovery must be in VERIFIED state before activation; current state is {current_state.value}"
                )

            candidate = self._staged_candidates[session_id]
            auth = self._authorizations[session_id]
            chk_plan, chk_meas = self._checkpoint_pairs[session_id]
            verif_snapshot = self._verifications[session_id]

            # Transition to ACTIVATING
            self._session_states[session_id] = RecoveryState.ACTIVATING
            self._emit_event(
                "recovery.activation.started",
                {
                    "session_id": session_id,
                    "operator_id": auth.operator_id,
                    "epoch_id": self._epoch_id,
                },
            )

            # Record prior registration state before mutating M13
            prior_reg = self._registration_service.get_registration(session_id) if self._registration_service else None
            prior_state = prior_reg.state if prior_reg else None

            # Execute Controlled Synchronous Activation
            try:
                # 1. Mutate M13 Registration
                if self._registration_service is None:
                    raise RecoveryActivationError("RegistrationService is unavailable")

                self._registration_service.submit_fiducials(session_id, candidate.plan_id, candidate.fiducial_cloud)
                self._registration_service.solve_registration(session_id, candidate.plan_id)
                reg_snapshot = self._registration_service.verify_registration(
                    session_id=session_id,
                    operator_id=auth.operator_id,
                    checkpoint_plan_mm=chk_plan,
                    checkpoint_measured_mm=chk_meas,
                )

                # 2. Re-bind M16 Drift Monitoring (Transitions to READY)
                if self._drift_service is not None and landmarks is not None:
                    self._drift_service.bind_landmarks(session_id, landmarks)

                # 3. Re-bind M15 Proximity Protection (Transitions to SAFE)
                if self._proximity_service is not None and zones is not None:
                    self._proximity_service.bind_zones(
                        session_id=session_id,
                        zones=zones,
                        registration_error_mm=registration_error_mm,
                        static_margin_mm=static_margin_mm,
                    )

                # 4. Re-bind M14 Navigation (Transitions to IDLE)
                if self._navigation_service is not None and plan_trajectory is not None:
                    from holomed.execution._capability import _create_execution_capability

                    cap_nav = _create_execution_capability(
                        service_instance_id=id(self._navigation_service),
                        session_id=session_id,
                        action="TRAJECTORY_ALIGNMENT",
                        sequence_number=sequence_number,
                    )
                    try:
                        self._navigation_service.bind_trajectory(
                            session_id=session_id,
                            trajectory_id=plan_trajectory.trajectory_id,
                            plan_trajectory=plan_trajectory,
                            sequence_number=sequence_number,
                            capability=cap_nav,
                        )
                    finally:
                        cap_nav.invalidate()

                # 5. Post-Activation Consistency Verification
                RecoveryEvaluator.verify_post_activation_consistency(
                    session_id=session_id,
                    epoch_id=self._epoch_id,
                    registration_service=self._registration_service,
                    drift_service=self._drift_service if landmarks is not None else None,
                    proximity_service=self._proximity_service if zones is not None else None,
                    navigation_service=self._navigation_service if plan_trajectory is not None else None,
                )

            except Exception as e:
                # Post-activation failure -> FAILED (No rollback claimed!)
                self._session_states[session_id] = RecoveryState.FAILED
                self._emit_event(
                    "recovery.failed",
                    {"session_id": session_id, "reason": str(e), "stage": "ACTIVATING"},
                )
                raise RecoveryActivationError(f"Recovery activation failed: {e}") from e

            # Success: Advance registration revision (M17-owned positive integer)
            self._revisions[session_id] = self._revisions.get(session_id, 0) + 1
            new_revision = self._revisions[session_id]

            rec_id = f"rec_{uuid.uuid4().hex[:12]}"
            ts = now_utc or datetime.now(timezone.utc).isoformat()

            active_reg = self._registration_service.get_registration(session_id)
            active_transform = active_reg.transform if active_reg else None

            status_record = RecoveryStatusRecord(
                recovery_id=rec_id,
                session_id=session_id,
                state=RecoveryState.ACTIVATED,
                registration_revision=new_revision,
                prior_registration_state=prior_state,
                active_transform=active_transform,
                last_quality_report=candidate.quality_report,
                last_verification=verif_snapshot,
                updated_at_utc=ts,
            )

            self._session_states[session_id] = RecoveryState.ACTIVATED
            self._latest_records[session_id] = status_record

            # Persist recovery audit record via public PersistenceService if attached
            if self._persistence_service is not None:
                audit_payload = {
                    "recovery_id": rec_id,
                    "session_id": session_id,
                    "registration_revision": new_revision,
                    "prior_registration_state": prior_state.value if prior_state else None,
                    "fre_rms_mm": candidate.quality_report.fre_rms_mm,
                    "measured_drift_error_mm": verif_snapshot.measured_drift_error_mm,
                    "operator_id": auth.operator_id,
                    "authorization_reference": auth.authorization_reference,
                    "recovery_state": RecoveryState.ACTIVATED.value,
                    "epoch_id": self._epoch_id,
                    "timestamp_utc": ts,
                }
                self._persistence_service.record_audit(audit_payload, session_id=session_id)

            self._emit_event(
                "recovery.spatial.activated",
                {
                    "session_id": session_id,
                    "recovery_id": rec_id,
                    "registration_revision": new_revision,
                    "fre_rms_mm": candidate.quality_report.fre_rms_mm,
                    "measured_drift_error_mm": verif_snapshot.measured_drift_error_mm,
                    "epoch_id": self._epoch_id,
                },
            )
            return status_record
        finally:
            self._in_transaction = False

    def get_recovery_status(self, session_id: str) -> RecoveryStatusRecord:
        """Query active recovery status snapshot for a session."""
        now_utc = datetime.now(timezone.utc).isoformat()
        state = self._session_states.get(session_id, RecoveryState.IDLE)
        revision = self._revisions.get(session_id, 0)
        candidate = self._staged_candidates.get(session_id)
        verif = self._verifications.get(session_id)

        active_reg = self._registration_service.get_registration(session_id) if self._registration_service else None
        active_transform = active_reg.transform if active_reg else None
        prior_state = active_reg.state if active_reg else None

        return RecoveryStatusRecord(
            recovery_id=f"status_{session_id}",
            session_id=session_id,
            state=state,
            registration_revision=revision,
            prior_registration_state=prior_state,
            active_transform=active_transform,
            last_quality_report=candidate.quality_report if candidate else None,
            last_verification=verif,
            updated_at_utc=now_utc,
        )

    def reset_session(self, session_id: str) -> None:
        """Explicitly reset a session back to IDLE (clearing FAILED or BLOCKED latching)."""
        self._session_states[session_id] = RecoveryState.IDLE
        self._staged_candidates.pop(session_id, None)
        self._verifications.pop(session_id, None)
        self._authorizations.pop(session_id, None)
        self._checkpoint_pairs.pop(session_id, None)

    def clear(self) -> None:
        """Clear all active recovery session tracking state."""
        self._session_states.clear()
        self._revisions.clear()
        self._staged_candidates.clear()
        self._verifications.clear()
        self._authorizations.clear()
        self._checkpoint_pairs.clear()
        self._latest_records.clear()

    # -------------------------------------------------------------------------
    # Helper & Dispatcher Protocol Handlers
    # -------------------------------------------------------------------------

    def _emit_event(self, topic: str, payload: Mapping[str, Any]) -> None:
        """Emit protocol event over message dispatcher."""
        env = create_event(
            message_name=topic,
            source=self.name,
            payload=dict(payload),
        )
        if self._dispatcher is not None and getattr(self._dispatcher, "state", None) == DispatcherState.STARTED:
            self._dispatcher.dispatch(env)

    def handle_stage_command(self, command_envelope: MessageEnvelope) -> MessageEnvelope:
        """Handle recovery.stage command."""
        payload = command_envelope.payload
        session_id = payload.get("session_id")
        plan_id = payload.get("plan_id")
        fiducials_data = payload.get("fiducials")

        if not session_id or not plan_id or not fiducials_data:
            return create_error_response(command_envelope, self.name, "ERR_INVALID_ARGS", "Missing session_id, plan_id, or fiducials")

        try:
            pairs = tuple(
                FiducialPointPair(
                    fiducial_id=f["fiducial_id"],
                    planned_point_mm=tuple(f["planned_point_mm"]),  # type: ignore
                    measured_point_mm=tuple(f["measured_point_mm"]),  # type: ignore
                    label=f.get("label", "landmark"),
                )
                for f in fiducials_data
            )
            cloud = FiducialCloud(pairs=pairs)
            candidate = self.stage_candidate(str(session_id), str(plan_id), cloud)
            return create_response(
                command_envelope,
                self.name,
                payload={
                    "session_id": session_id,
                    "state": RecoveryState.SOLVED.value,
                    "fre_rms_mm": candidate.quality_report.fre_rms_mm,
                },
            )
        except Exception as e:
            raw_err = str(e)
            redacted = self._secret_filter.redact(raw_err) if self._secret_filter else raw_err
            return create_error_response(command_envelope, self.name, _format_error_code(type(e).__name__), redacted)

    def handle_verify_command(self, command_envelope: MessageEnvelope) -> MessageEnvelope:
        """Handle recovery.verify command."""
        payload = command_envelope.payload
        session_id = payload.get("session_id")
        auth_data = payload.get("authorization")
        chk_plan = payload.get("checkpoint_plan_mm")
        chk_meas = payload.get("checkpoint_measured_mm")

        if not session_id or not auth_data or not chk_plan or not chk_meas:
            return create_error_response(command_envelope, self.name, "ERR_INVALID_ARGS", "Missing required verify arguments")

        try:
            auth = RecoveryAuthorization(
                operator_id=auth_data["operator_id"],
                rationale=auth_data["rationale"],
                timestamp_utc=auth_data["timestamp_utc"],
                sequence_number=int(auth_data.get("sequence_number", 1)),
                authorization_reference=auth_data["authorization_reference"],
            )
            snapshot = self.verify_candidate(
                session_id=str(session_id),
                authorization=auth,
                checkpoint_plan_mm=tuple(chk_plan),  # type: ignore
                checkpoint_measured_mm=tuple(chk_meas),  # type: ignore
            )
            return create_response(
                command_envelope,
                self.name,
                payload={
                    "session_id": session_id,
                    "verification_id": snapshot.verification_id,
                    "state": RecoveryState.VERIFIED.value,
                    "measured_drift_error_mm": snapshot.measured_drift_error_mm,
                },
            )
        except Exception as e:
            raw_err = str(e)
            redacted = self._secret_filter.redact(raw_err) if self._secret_filter else raw_err
            return create_error_response(command_envelope, self.name, _format_error_code(type(e).__name__), redacted)

    def handle_activate_command(self, command_envelope: MessageEnvelope) -> MessageEnvelope:
        """Handle recovery.activate command."""
        payload = command_envelope.payload
        session_id = payload.get("session_id")

        if not session_id:
            return create_error_response(command_envelope, self.name, "ERR_INVALID_ARGS", "Missing session_id")

        try:
            status = self.activate_recovery(str(session_id))
            return create_response(
                command_envelope,
                self.name,
                payload={
                    "session_id": session_id,
                    "recovery_id": status.recovery_id,
                    "state": status.state.value,
                    "registration_revision": status.registration_revision,
                },
            )
        except Exception as e:
            raw_err = str(e)
            redacted = self._secret_filter.redact(raw_err) if self._secret_filter else raw_err
            return create_error_response(command_envelope, self.name, _format_error_code(type(e).__name__), redacted)

    def handle_get_status_query(self, query_envelope: MessageEnvelope) -> MessageEnvelope:
        """Handle recovery.status.get query."""
        payload = query_envelope.payload
        session_id = payload.get("session_id")

        if not session_id:
            return create_error_response(query_envelope, self.name, "ERR_INVALID_ARGS", "Missing session_id")

        status = self.get_recovery_status(str(session_id))
        return create_response(
            query_envelope,
            self.name,
            payload={
                "session_id": session_id,
                "recovery_id": status.recovery_id,
                "state": status.state.value,
                "registration_revision": status.registration_revision,
                "prior_registration_state": status.prior_registration_state.value if status.prior_registration_state else None,
                "updated_at_utc": status.updated_at_utc,
            },
        )
