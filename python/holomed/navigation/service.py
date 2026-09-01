# -*- coding: utf-8 -*-
"""Real-Time Surgical Navigation Service Implementing IService for M14."""

from __future__ import annotations

from datetime import datetime, timezone
import math
from typing import Any, Dict, Mapping, Optional, Tuple
import uuid

from holomed.core.dispatcher import MessageDispatcher
from holomed.core.models import DispatcherState
from holomed.devices.models import DeviceShutdownFailureRecord
from holomed.navigation.constants import (
    DEFAULT_PATIENT_TRACKER_FRAME,
    MAX_ACTIVE_NAVIGATION_SESSIONS,
    MAX_DEVIATION_HISTORY_RECORDS,
    MAX_TRACKED_INSTRUMENTS_PER_SESSION,
)
from holomed.navigation.evaluator import TrajectoryDeviationEvaluator
from holomed.navigation.exceptions import (
    NavigationCapacityError,
    NavigationError,
    NavigationFrameMismatchError,
    NavigationLifecycleError,
    NavigationRegistrationMismatchError,
    NavigationSequenceError,
    NavigationShutdownError,
    NavigationValidationError,
)
from holomed.navigation.geometry import convert_point_mm_to_meters
from holomed.navigation.models import (
    NavigationState,
    NavigationStatusRecord,
    PositionClass,
    TrackedInstrumentPose,
    TrajectoryDeviationRecord,
)
from holomed.planning.models import TrajectoryPlan
from holomed.protocol.builders import (
    create_error_response,
    create_event,
    create_response,
)
from holomed.protocol.models import MessageEnvelope
from holomed.registration.models import RegistrationState
from holomed.registration.service import RegistrationService
from holomed.registration.transforms import transform_trajectory
from holomed.runtime.context import RuntimeContext
from holomed.runtime.logging import SecretFilter, StructuredLogger
from holomed.runtime.models import (
    HealthStatus,
    OwnedResourceSet,
    ServiceHealth,
)
from holomed.runtime.service import IService, ServiceState

STRUCTURAL_RESOURCE_IDS: tuple[str, ...] = (
    "navigation.registry",
    "navigation.evaluator",
    "navigation.metrics",
    "navigation.interlock",
)


def _format_error_code(exc_type_name: str) -> str:
    """Format exception class name into protocol-compliant ^ERR_[A-Z0-9_]+$ error code."""
    clean = exc_type_name.upper().replace("ERROR", "")
    return f"ERR_{clean}" if not clean.startswith("ERR_") else clean


class NavigationService(IService):
    """Real-Time Surgical Navigation & Trajectory Deviation Evaluation Service."""

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
        self._logger = logger or StructuredLogger("navigation_service", secret_filter=secret_filter)

        self._state: ServiceState = ServiceState.UNINITIALIZED
        self._context: Optional[RuntimeContext] = None
        self._resources: Optional[OwnedResourceSet] = None
        self._epoch_id: int = 0

        # Session tracking state
        # session_id -> bound TrajectoryPlan (in patient_tracker_frame)
        self._bound_trajectories: Dict[str, TrajectoryPlan] = {}
        # (session_id, instrument_id) -> latest TrackedInstrumentPose
        self._latest_poses: Dict[Tuple[str, str], TrackedInstrumentPose] = {}
        # (session_id, instrument_id) -> latest sequence_number
        self._latest_sequences: Dict[Tuple[str, str], int] = {}
        # session_id -> latest TrajectoryDeviationRecord
        self._latest_deviations: Dict[str, TrajectoryDeviationRecord] = {}
        # session_id -> NavigationState
        self._session_states: Dict[str, NavigationState] = {}
        # session_id -> active instrument_id
        self._active_instruments: Dict[str, str] = {}

        self._in_transaction: bool = False

    @property
    def name(self) -> str:
        return "navigation_service"

    @property
    def dependencies(self) -> tuple[str, ...]:
        return ("dispatcher", "registration_service")

    @property
    def state(self) -> ServiceState:
        return self._state

    @property
    def resources(self) -> OwnedResourceSet:
        if self._resources is None:
            raise NavigationLifecycleError("Service has not been initialized; resources unavailable")
        return self._resources

    @property
    def active_sessions_count(self) -> int:
        return len(self._session_states)

    def initialize(self, context: RuntimeContext) -> None:
        """Initialize navigation service and acquire 4 structural handles."""
        if self._state not in (ServiceState.UNINITIALIZED, ServiceState.STOPPED):
            raise NavigationLifecycleError(f"Cannot initialize NavigationService in state {self._state.name}")

        self._context = context
        self._epoch_id = context.epoch_id
        self._resources = OwnedResourceSet(self.name, self._epoch_id)

        for res_id in STRUCTURAL_RESOURCE_IDS:
            self._resources.acquire(res_id)

        # Register Dispatcher Routes strictly during INITIALIZED
        if self._dispatcher is not None:
            self._dispatcher.register_command_handler("navigation.pose.submit", self.handle_submit_pose_command, self.name)
            self._dispatcher.register_command_handler("navigation.evaluate", self.handle_evaluate_command, self.name)
            self._dispatcher.register_query_handler("navigation.status.get", self.handle_get_status_query, self.name)

        self._state = ServiceState.INITIALIZED

    def start(self) -> None:
        """Transition service to STARTED."""
        if self._state != ServiceState.INITIALIZED:
            raise NavigationLifecycleError(f"Cannot start NavigationService in state {self._state.name}")

        self._state = ServiceState.STARTED
        self._emit_event("navigation.started", {"epoch_id": self._epoch_id})

    def stop(self) -> None:
        """Clear transient state and release 4 handles cleanly."""
        if self._state in (ServiceState.STOPPED, ServiceState.UNINITIALIZED):
            return

        if self._in_transaction:
            raise NavigationLifecycleError("Cannot stop NavigationService during an active transaction")

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
                raise NavigationShutdownError(
                    f"NavigationService teardown encountered {len(failures)} resource failure(s)",
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
            message=f"Active navigation sessions: {len(self._session_states)}",
            timestamp_utc=now_utc,
        )

    # -------------------------------------------------------------------------
    # Core Navigation Operations
    # -------------------------------------------------------------------------

    def bind_trajectory(self, session_id: str, trajectory_id: str, plan_trajectory: TrajectoryPlan) -> None:
        """Bind a surgical plan trajectory to a session using verified M13 registration."""
        if self._state != ServiceState.STARTED:
            raise NavigationLifecycleError(f"Cannot bind trajectory in state {self._state.name}")
        if self._in_transaction:
            raise NavigationLifecycleError("Reentrant call to bind_trajectory rejected")

        self._in_transaction = True
        try:
            if session_id not in self._session_states and len(self._session_states) >= MAX_ACTIVE_NAVIGATION_SESSIONS:
                raise NavigationCapacityError(f"Max active navigation sessions ({MAX_ACTIVE_NAVIGATION_SESSIONS}) exceeded")

            # Check verified M13 registration requirement
            if self._registration_service is None or not self._registration_service.is_registration_verified(session_id):
                raise NavigationRegistrationMismatchError(
                    f"Session {session_id!r} does not have a verified M13 registration; navigation cannot bind"
                )

            reg_record = self._registration_service.get_registration(session_id)
            if reg_record is None or reg_record.transform is None:
                raise NavigationRegistrationMismatchError("Missing transform in active registration record")

            # Transform trajectory into patient tracker frame (mm)
            physical_traj = transform_trajectory(reg_record.transform, plan_trajectory)
            self._bound_trajectories[session_id] = physical_traj
            self._session_states[session_id] = NavigationState.IDLE

            self._emit_event(
                "navigation.status.changed",
                {
                    "session_id": session_id,
                    "trajectory_id": trajectory_id,
                    "state": NavigationState.IDLE.value,
                    "epoch_id": self._epoch_id,
                },
            )
        finally:
            self._in_transaction = False

    def submit_pose(self, pose: TrackedInstrumentPose) -> None:
        """Validate and store the latest tracked instrument pose for a session."""
        if self._state != ServiceState.STARTED:
            raise NavigationLifecycleError(f"Cannot submit pose in state {self._state.name}")
        if self._in_transaction:
            raise NavigationLifecycleError("Reentrant call to submit_pose rejected")

        self._in_transaction = True
        try:
            session_id = pose.session_id
            instrument_id = pose.instrument_id
            key = (session_id, instrument_id)

            # Validate capacity
            session_instruments = [inst for (s, inst) in self._latest_poses.keys() if s == session_id]
            if instrument_id not in session_instruments and len(session_instruments) >= MAX_TRACKED_INSTRUMENTS_PER_SESSION:
                raise NavigationCapacityError(
                    f"Maximum tracked instruments limit ({MAX_TRACKED_INSTRUMENTS_PER_SESSION}) reached for session"
                )

            # Sequence monotonicity check
            last_seq = self._latest_sequences.get(key)
            if last_seq is not None and pose.sequence_number <= last_seq:
                raise NavigationSequenceError(
                    f"Non-monotonic sequence number {pose.sequence_number} <= previous {last_seq} for instrument {instrument_id!r}"
                )

            # Store validated pose and advance sequence
            self._latest_poses[key] = pose
            self._latest_sequences[key] = pose.sequence_number
            self._active_instruments[session_id] = instrument_id

            if session_id in self._session_states and self._session_states[session_id] == NavigationState.IDLE:
                self._session_states[session_id] = NavigationState.TRACKING

            self._emit_event(
                "navigation.pose.submitted",
                {
                    "session_id": session_id,
                    "instrument_id": instrument_id,
                    "sequence_number": pose.sequence_number,
                    "epoch_id": self._epoch_id,
                },
            )
        finally:
            self._in_transaction = False

    def evaluate(self, session_id: str, instrument_id: Optional[str] = None, now_utc: Optional[str] = None) -> TrajectoryDeviationRecord:
        """Evaluate the latest stored pose against the bound registered trajectory."""
        if self._state != ServiceState.STARTED:
            raise NavigationLifecycleError(f"Cannot evaluate navigation in state {self._state.name}")
        if self._in_transaction:
            raise NavigationLifecycleError("Reentrant call to evaluate rejected")

        self._in_transaction = True
        try:
            # Verify active M13 registration validity
            if self._registration_service is not None:
                reg = self._registration_service.get_registration(session_id)
                if reg is None or reg.state != RegistrationState.VERIFIED:
                    # Registration became invalidated or unverified -> Fail Closed to INTERLOCKED!
                    self._session_states[session_id] = NavigationState.INTERLOCKED
                    raise NavigationRegistrationMismatchError(
                        f"Registration for session {session_id!r} is not verified; navigation interlocked"
                    )

            if session_id not in self._bound_trajectories:
                raise NavigationLifecycleError(f"No trajectory bound for session {session_id!r}")

            traj = self._bound_trajectories[session_id]
            target_inst = instrument_id or self._active_instruments.get(session_id)
            if not target_inst or (session_id, target_inst) not in self._latest_poses:
                raise NavigationValidationError(f"No active pose submitted for session {session_id!r}")

            pose = self._latest_poses[(session_id, target_inst)]

            # Evaluate deviation using TrajectoryDeviationEvaluator
            deviation = TrajectoryDeviationEvaluator.evaluate(pose, traj, now_utc=now_utc)

            # Update session state and records
            self._latest_deviations[session_id] = deviation
            self._session_states[session_id] = deviation.state

            # Emit events
            self._emit_event(
                "navigation.evaluation.updated",
                {
                    "session_id": session_id,
                    "trajectory_id": traj.trajectory_id,
                    "instrument_id": pose.instrument_id,
                    "lateral_deviation_mm": deviation.lateral_deviation_mm,
                    "angular_deviation_deg": deviation.angular_deviation_deg,
                    "axial_distance_mm": deviation.axial_distance_mm,
                    "state": deviation.state.value,
                    "is_aligned": deviation.is_aligned,
                    "epoch_id": self._epoch_id,
                },
            )

            if not deviation.is_safe and deviation.safety_interlock is not None:
                self._emit_event(
                    "navigation.interlock.triggered",
                    {
                        "session_id": session_id,
                        "interlock_id": deviation.safety_interlock.interlock_id,
                        "condition": deviation.safety_interlock.condition_name,
                        "reason": deviation.safety_interlock.reason,
                        "epoch_id": self._epoch_id,
                    },
                )

            return deviation
        finally:
            self._in_transaction = False

    def get_navigation_status(self, session_id: str) -> NavigationStatusRecord:
        """Query the active navigation status for a session."""
        now_utc = datetime.now(timezone.utc).isoformat()
        state = self._session_states.get(session_id, NavigationState.UNINITIALIZED if self._state == ServiceState.UNINITIALIZED else NavigationState.IDLE)
        traj = self._bound_trajectories.get(session_id)
        last_dev = self._latest_deviations.get(session_id)
        active_inst = self._active_instruments.get(session_id)

        # Guidance active rule: only ALIGNED or PRE_ENTRY is considered active guidance!
        is_guidance_active = state in (NavigationState.ALIGNED, NavigationState.PRE_ENTRY)

        return NavigationStatusRecord(
            session_id=session_id,
            trajectory_id=traj.trajectory_id if traj else None,
            epoch_id=self._epoch_id,
            state=state,
            active_instrument_id=active_inst,
            last_deviation=last_dev,
            is_guidance_active=is_guidance_active,
            updated_at_utc=now_utc,
        )

    def clear(self) -> None:
        """Clear all session states and stored telemetry."""
        self._bound_trajectories.clear()
        self._latest_poses.clear()
        self._latest_sequences.clear()
        self._latest_deviations.clear()
        self._session_states.clear()
        self._active_instruments.clear()

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

    def handle_submit_pose_command(self, command_envelope: MessageEnvelope) -> MessageEnvelope:
        payload = command_envelope.payload
        try:
            pose = TrackedInstrumentPose(
                instrument_id=str(payload["instrument_id"]),
                session_id=str(payload["session_id"]),
                epoch_id=int(payload.get("epoch_id", self._epoch_id)),
                sequence_number=int(payload["sequence_number"]),
                tip_position_mm=tuple(payload["tip_position_mm"]),  # type: ignore
                orientation_quaternion=tuple(payload["orientation_quaternion"]),  # type: ignore
                coordinate_frame=str(payload.get("coordinate_frame", DEFAULT_PATIENT_TRACKER_FRAME)),
                confidence=float(payload.get("confidence", 1.0)),
                uncertainty=float(payload.get("uncertainty", 0.0)),
                timestamp_utc=str(payload["timestamp_utc"]),
            )
            self.submit_pose(pose)
            return create_response(
                command_envelope,
                self.name,
                payload={"session_id": pose.session_id, "sequence_number": pose.sequence_number, "status": "STORED"},
            )
        except Exception as e:
            raw_err = str(e)
            redacted = self._secret_filter.redact(raw_err) if self._secret_filter else raw_err
            return create_error_response(command_envelope, self.name, _format_error_code(type(e).__name__), redacted)

    def handle_evaluate_command(self, command_envelope: MessageEnvelope) -> MessageEnvelope:
        payload = command_envelope.payload
        session_id = payload.get("session_id")
        instrument_id = payload.get("instrument_id")
        now_utc = payload.get("now_utc")

        if not session_id:
            return create_error_response(command_envelope, self.name, "ERR_INVALID_ARGS", "Missing session_id")

        try:
            dev = self.evaluate(str(session_id), instrument_id=str(instrument_id) if instrument_id else None, now_utc=str(now_utc) if now_utc else None)
            resp_payload: dict[str, Any] = {
                "record_id": dev.record_id,
                "session_id": dev.session_id,
                "lateral_deviation_mm": dev.lateral_deviation_mm,
                "angular_deviation_deg": dev.angular_deviation_deg,
                "axial_distance_mm": dev.axial_distance_mm,
                "position_class": dev.position_class.value,
                "state": dev.state.value,
                "is_aligned": dev.is_aligned,
                "is_safe": dev.is_safe,
            }
            return create_response(command_envelope, self.name, payload=resp_payload)
        except Exception as e:
            raw_err = str(e)
            redacted = self._secret_filter.redact(raw_err) if self._secret_filter else raw_err
            return create_error_response(command_envelope, self.name, _format_error_code(type(e).__name__), redacted)

    def handle_get_status_query(self, query_envelope: MessageEnvelope) -> MessageEnvelope:
        session_id = query_envelope.payload.get("session_id")
        if not session_id:
            return create_error_response(query_envelope, self.name, "ERR_INVALID_ARGS", "Missing session_id")

        status = self.get_navigation_status(str(session_id))
        resp_payload: dict[str, Any] = {
            "session_id": status.session_id,
            "trajectory_id": status.trajectory_id,
            "state": status.state.value,
            "active_instrument_id": status.active_instrument_id,
            "is_guidance_active": status.is_guidance_active,
            "updated_at_utc": status.updated_at_utc,
        }
        if status.last_deviation:
            resp_payload["last_deviation"] = {
                "lateral_deviation_mm": status.last_deviation.lateral_deviation_mm,
                "angular_deviation_deg": status.last_deviation.angular_deviation_deg,
                "axial_distance_mm": status.last_deviation.axial_distance_mm,
                "position_class": status.last_deviation.position_class.value,
                "is_aligned": status.last_deviation.is_aligned,
                "is_safe": status.last_deviation.is_safe,
            }
            # Add XR-compatible meter coordinates
            pt_m = convert_point_mm_to_meters(self._latest_poses[(status.session_id, status.active_instrument_id)].tip_position_mm) if status.active_instrument_id and (status.session_id, status.active_instrument_id) in self._latest_poses else (0.0, 0.0, 0.0)
            resp_payload["xr_presentation"] = {
                "tip_position_m": list(pt_m),
                "lateral_deviation_m": status.last_deviation.lateral_deviation_mm / 1000.0,
            }

        return create_response(query_envelope, self.name, payload=resp_payload)
