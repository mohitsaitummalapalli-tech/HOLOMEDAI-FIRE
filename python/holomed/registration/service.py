# -*- coding: utf-8 -*-
"""Spatial Registration Service for M13 Implementing IService."""

from __future__ import annotations

from datetime import datetime, timezone
import math
from typing import Any, Dict, Mapping, Optional
import uuid

from holomed.core.dispatcher import MessageDispatcher
from holomed.core.models import DispatcherState
from holomed.devices.models import DeviceShutdownFailureRecord
from holomed.planning.service import PlanningService
from holomed.protocol.builders import (
    create_error_response,
    create_event,
    create_response,
)
from holomed.protocol.models import MessageEnvelope
from holomed.registration.constants import MAX_ACTIVE_REGISTRATIONS, MAX_ALLOWED_FRE_MM
from holomed.registration.exceptions import (
    RegistrationAccuracyError,
    RegistrationAuthorizationError,
    RegistrationCapacityError,
    RegistrationError,
    RegistrationLifecycleError,
    RegistrationPlanMismatchError,
    RegistrationShutdownError,
    RegistrationValidationError,
    RegistrationVerificationError,
)
from holomed.registration.metrics import compute_registration_metrics
from holomed.registration.models import (
    FiducialCloud,
    FiducialPointPair,
    RegistrationQualityReport,
    RegistrationState,
    RegistrationStatusRecord,
    RegistrationVerificationSnapshot,
    RigidRegistrationTransform3D,
)
from holomed.registration.solver import HornRigidRegistrationSolver
from holomed.registration.transforms import transform_point
from holomed.runtime.context import RuntimeContext
from holomed.runtime.logging import SecretFilter, StructuredLogger
from holomed.runtime.models import (
    HealthStatus,
    OwnedResourceSet,
    ServiceHealth,
)
from holomed.runtime.service import IService, ServiceState

STRUCTURAL_RESOURCE_IDS: tuple[str, ...] = (
    "registration.registry",
    "registration.solver",
    "registration.transform",
    "registration.metrics",
)


def _format_error_code(exc_type_name: str) -> str:
    """Format exception name into protocol-compliant ^ERR_[A-Z0-9_]+$ error code."""
    clean = exc_type_name.upper().replace("ERROR", "")
    return f"ERR_{clean}" if not clean.startswith("ERR_") else clean


class RegistrationService(IService):
    """Spatial Patient-to-Plan Registration Service implementing IService."""

    def __init__(
        self,
        dispatcher: Optional[MessageDispatcher] = None,
        planning_service: Optional[PlanningService] = None,
        secret_filter: Optional[SecretFilter] = None,
        logger: Optional[StructuredLogger] = None,
    ) -> None:
        self._dispatcher = dispatcher
        self._planning_service = planning_service
        self._secret_filter = secret_filter
        self._logger = logger or StructuredLogger("registration_service", secret_filter=secret_filter)

        self._state: ServiceState = ServiceState.UNINITIALIZED
        self._context: Optional[RuntimeContext] = None
        self._resources: Optional[OwnedResourceSet] = None
        self._epoch_id: int = 0

        # Session state: session_id -> RegistrationStatusRecord
        self._registrations: Dict[str, RegistrationStatusRecord] = {}
        # session_id -> FiducialCloud
        self._fiducial_clouds: Dict[str, FiducialCloud] = {}

        self._in_transaction: bool = False

    @property
    def name(self) -> str:
        return "registration_service"

    @property
    def dependencies(self) -> tuple[str, ...]:
        return ("dispatcher", "planning_service")

    @property
    def state(self) -> ServiceState:
        return self._state

    @property
    def resources(self) -> OwnedResourceSet:
        if self._resources is None:
            raise RegistrationLifecycleError("Service has not been initialized; resources unavailable")
        return self._resources

    @property
    def active_registrations_count(self) -> int:
        return len(self._registrations)

    def initialize(self, context: RuntimeContext) -> None:
        """Initialize registration service and acquire 4 structural handles."""
        if self._state not in (ServiceState.UNINITIALIZED, ServiceState.STOPPED):
            raise RegistrationLifecycleError(f"Cannot initialize RegistrationService in state {self._state.name}")

        self._context = context
        self._epoch_id = context.epoch_id
        self._resources = OwnedResourceSet(self.name, self._epoch_id)

        for res_id in STRUCTURAL_RESOURCE_IDS:
            self._resources.acquire(res_id)

        # Register Dispatcher Routes strictly during INITIALIZED (M23: Read-only query only)
        if self._dispatcher is not None:
            self._dispatcher.register_query_handler("registration.get", self.handle_get_query, self.name)

        self._state = ServiceState.INITIALIZED

    def start(self) -> None:
        """Transition service to STARTED."""
        if self._state != ServiceState.INITIALIZED:
            raise RegistrationLifecycleError(f"Cannot start RegistrationService in state {self._state.name}")

        self._state = ServiceState.STARTED
        self._emit_event("registration.started", {"epoch_id": self._epoch_id})

    def stop(self) -> None:
        """Clear transient state and release 4 handles cleanly."""
        if self._state in (ServiceState.STOPPED, getattr(ServiceState, "UNINITIALIZED")):
            return

        if self._in_transaction:
            raise RegistrationLifecycleError("Cannot stop RegistrationService during an active transaction")

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
                raise RegistrationShutdownError(
                    f"RegistrationService teardown encountered {len(failures)} resource failure(s)",
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
            message=f"Active registrations: {len(self._registrations)}",
            timestamp_utc=now_utc,
        )

    # -------------------------------------------------------------------------
    # Core Registration Management APIs
    # -------------------------------------------------------------------------

    def submit_fiducials(
        self,
        session_id: str,
        plan_id: str,
        cloud: FiducialCloud,
        *,
        capability: Optional[Any] = None,
        sequence_number: Optional[int] = None,
    ) -> RegistrationStatusRecord:
        """Ingest fiducial point cloud for a session."""
        if self._state != ServiceState.STARTED:
            raise RegistrationLifecycleError(f"Cannot submit fiducials in state {self._state.name}")

        # Capability Validation (M23)
        if capability is None or not getattr(capability, "is_active", False):
            raise RegistrationAuthorizationError("Registration execution requires an active capability")
        if getattr(capability, "action", None) != "REGISTRATION_ALIGNMENT":
            raise RegistrationAuthorizationError(
                f"Capability action mismatch: expected 'REGISTRATION_ALIGNMENT', got {getattr(capability, 'action', None)!r}"
            )
        if getattr(capability, "session_id", None) != session_id:
            raise RegistrationAuthorizationError(
                f"Capability session mismatch: expected {session_id!r}, got {getattr(capability, 'session_id', None)!r}"
            )
        if sequence_number is not None and getattr(capability, "sequence_number", None) != sequence_number:
            raise RegistrationAuthorizationError(
                f"Capability sequence mismatch: expected {sequence_number}, got {getattr(capability, 'sequence_number', None)}"
            )
        if getattr(capability, "service_instance_id", None) != id(self):
            raise RegistrationAuthorizationError(
                f"Capability service binding mismatch: expected {id(self)}, got {getattr(capability, 'service_instance_id', None)}"
            )

        if self._in_transaction:
            raise RegistrationLifecycleError("Reentrant call to submit_fiducials rejected")

        self._in_transaction = True
        try:
            # Check locked plan dependency (D315)
            self._verify_locked_plan(plan_id)

            if session_id not in self._registrations and len(self._registrations) >= MAX_ACTIVE_REGISTRATIONS:
                raise RegistrationCapacityError(f"Maximum active registrations limit ({MAX_ACTIVE_REGISTRATIONS}) reached")

            self._fiducial_clouds[session_id] = cloud
            record = RegistrationStatusRecord(
                session_id=session_id,
                plan_id=plan_id,
                epoch_id=self._epoch_id,
                state=RegistrationState.DRAFT,
                transform=None,
                quality_report=None,
                locked=False,
            )
            self._registrations[session_id] = record

            self._emit_event(
                "registration.fiducials.submitted",
                {
                    "session_id": session_id,
                    "plan_id": plan_id,
                    "point_count": len(cloud.pairs),
                    "epoch_id": self._epoch_id,
                },
            )
            return record
        finally:
            self._in_transaction = False

    def solve_registration(
        self,
        session_id: str,
        plan_id: str,
        *,
        capability: Optional[Any] = None,
        sequence_number: Optional[int] = None,
    ) -> RegistrationStatusRecord:
        """Solve 3D rigid transform and evaluate FRE."""
        if self._state != ServiceState.STARTED:
            raise RegistrationLifecycleError(f"Cannot solve registration in state {self._state.name}")

        # Capability Validation (M23)
        if capability is None or not getattr(capability, "is_active", False):
            raise RegistrationAuthorizationError("Registration execution requires an active capability")
        if getattr(capability, "action", None) != "REGISTRATION_ALIGNMENT":
            raise RegistrationAuthorizationError(
                f"Capability action mismatch: expected 'REGISTRATION_ALIGNMENT', got {getattr(capability, 'action', None)!r}"
            )
        if getattr(capability, "session_id", None) != session_id:
            raise RegistrationAuthorizationError(
                f"Capability session mismatch: expected {session_id!r}, got {getattr(capability, 'session_id', None)!r}"
            )
        if sequence_number is not None and getattr(capability, "sequence_number", None) != sequence_number:
            raise RegistrationAuthorizationError(
                f"Capability sequence mismatch: expected {sequence_number}, got {getattr(capability, 'sequence_number', None)}"
            )
        if getattr(capability, "service_instance_id", None) != id(self):
            raise RegistrationAuthorizationError(
                f"Capability service binding mismatch: expected {id(self)}, got {getattr(capability, 'service_instance_id', None)}"
            )

        if self._in_transaction:
            raise RegistrationLifecycleError("Reentrant call to solve_registration rejected")

        self._in_transaction = True
        try:
            self._verify_locked_plan(plan_id)

            if session_id not in self._fiducial_clouds:
                raise RegistrationValidationError(f"No fiducials submitted for session {session_id!r}")

            cloud = self._fiducial_clouds[session_id]

            # Solve transform using Horn's method
            transform = HornRigidRegistrationSolver.solve(
                cloud=cloud,
                source_frame="plan_frame",
                target_frame="patient_tracker_frame",
                epoch_id=self._epoch_id,
            )

            # Compute metrics
            metrics = compute_registration_metrics(cloud, transform)

            if not metrics.passed_clinical_threshold:
                # FRE > 1.5 mm -> FAILED (Fail-Closed)
                record = RegistrationStatusRecord(
                    session_id=session_id,
                    plan_id=plan_id,
                    epoch_id=self._epoch_id,
                    state=RegistrationState.FAILED,
                    transform=None,
                    quality_report=metrics,
                    locked=False,
                )
                self._registrations[session_id] = record
                self._emit_event(
                    "registration.failed",
                    {
                        "session_id": session_id,
                        "plan_id": plan_id,
                        "reason": f"FRE {metrics.fre_rms_mm:.2f} mm exceeded {MAX_ALLOWED_FRE_MM} mm",
                        "fre_rms_mm": metrics.fre_rms_mm,
                        "epoch_id": self._epoch_id,
                    },
                )
                raise RegistrationAccuracyError(
                    f"Registration failed: FRE RMS ({metrics.fre_rms_mm:.2f} mm) exceeds limit ({MAX_ALLOWED_FRE_MM} mm)",
                    details={"fre_rms_mm": metrics.fre_rms_mm, "fre_max_mm": metrics.fre_max_mm},
                )

            # Successfully solved: state = SOLVED (not yet VERIFIED!)
            record = RegistrationStatusRecord(
                session_id=session_id,
                plan_id=plan_id,
                epoch_id=self._epoch_id,
                state=RegistrationState.SOLVED,
                transform=transform,
                quality_report=metrics,
                locked=False,
            )
            self._registrations[session_id] = record

            self._emit_event(
                "registration.solved",
                {
                    "session_id": session_id,
                    "plan_id": plan_id,
                    "fre_rms_mm": metrics.fre_rms_mm,
                    "fre_max_mm": metrics.fre_max_mm,
                    "epoch_id": self._epoch_id,
                },
            )
            return record
        finally:
            self._in_transaction = False

    def verify_registration(
        self,
        session_id: str,
        operator_id: str,
        checkpoint_plan_mm: tuple[float, float, float],
        checkpoint_measured_mm: tuple[float, float, float],
        *,
        capability: Optional[Any] = None,
        sequence_number: Optional[int] = None,
    ) -> RegistrationVerificationSnapshot:
        """Formally verify registration during WHO Safety Timeout and check drift."""
        if self._state != ServiceState.STARTED:
            raise RegistrationLifecycleError(f"Cannot verify registration in state {self._state.name}")

        # Capability Validation (M23)
        if capability is None or not getattr(capability, "is_active", False):
            raise RegistrationAuthorizationError("Registration execution requires an active capability")
        if getattr(capability, "action", None) != "REGISTRATION_ALIGNMENT":
            raise RegistrationAuthorizationError(
                f"Capability action mismatch: expected 'REGISTRATION_ALIGNMENT', got {getattr(capability, 'action', None)!r}"
            )
        if getattr(capability, "session_id", None) != session_id:
            raise RegistrationAuthorizationError(
                f"Capability session mismatch: expected {session_id!r}, got {getattr(capability, 'session_id', None)!r}"
            )
        if sequence_number is not None and getattr(capability, "sequence_number", None) != sequence_number:
            raise RegistrationAuthorizationError(
                f"Capability sequence mismatch: expected {sequence_number}, got {getattr(capability, 'sequence_number', None)}"
            )
        if getattr(capability, "service_instance_id", None) != id(self):
            raise RegistrationAuthorizationError(
                f"Capability service binding mismatch: expected {id(self)}, got {getattr(capability, 'service_instance_id', None)}"
            )

        if session_id not in self._registrations:
            raise RegistrationValidationError(f"Registration not found for session {session_id!r}")

        reg = self._registrations[session_id]
        if reg.state not in (RegistrationState.SOLVED, RegistrationState.VERIFIED):
            raise RegistrationVerificationError(
                f"Registration in state {reg.state.value} cannot be verified; must be SOLVED"
            )

        if reg.transform is None:
            raise RegistrationVerificationError("Missing transform in registration record")

        # Compute drift distance between transformed plan checkpoint and measured physical checkpoint
        p_trans = transform_point(reg.transform, checkpoint_plan_mm)
        dx = p_trans[0] - checkpoint_measured_mm[0]
        dy = p_trans[1] - checkpoint_measured_mm[1]
        dz = p_trans[2] - checkpoint_measured_mm[2]
        drift_mm = math.sqrt(dx * dx + dy * dy + dz * dz)

        now_utc = datetime.now(timezone.utc).isoformat()
        vid = f"reg_verif_{uuid.uuid4().hex[:12]}"

        if drift_mm > MAX_ALLOWED_FRE_MM:
            # Drift exceeded limit -> INVALIDATED! Transform is dropped.
            invalidated_record = RegistrationStatusRecord(
                session_id=session_id,
                plan_id=reg.plan_id,
                epoch_id=reg.epoch_id,
                state=RegistrationState.INVALIDATED,
                transform=None,
                quality_report=reg.quality_report,
                locked=False,
            )
            self._registrations[session_id] = invalidated_record
            self._emit_event(
                "registration.invalidated",
                {
                    "session_id": session_id,
                    "reason": f"Drift {drift_mm:.2f} mm exceeded {MAX_ALLOWED_FRE_MM} mm",
                    "drift_error_mm": drift_mm,
                    "epoch_id": self._epoch_id,
                },
            )
            raise RegistrationVerificationError(
                f"Registration verification failed: drift ({drift_mm:.2f} mm) exceeds limit ({MAX_ALLOWED_FRE_MM} mm)",
                details={"drift_error_mm": drift_mm},
            )

        # Verification succeeded: state = VERIFIED
        verified_record = RegistrationStatusRecord(
            session_id=session_id,
            plan_id=reg.plan_id,
            epoch_id=reg.epoch_id,
            state=RegistrationState.VERIFIED,
            transform=reg.transform,
            quality_report=reg.quality_report,
            locked=True,
            verified_at_utc=now_utc,
        )
        self._registrations[session_id] = verified_record

        snapshot = RegistrationVerificationSnapshot(
            verification_id=vid,
            session_id=session_id,
            epoch_id=self._epoch_id,
            verifier_operator_id=operator_id,
            measured_drift_error_mm=drift_mm,
            passed=True,
            verified_at_utc=now_utc,
        )

        self._emit_event(
            "registration.verified",
            {
                "session_id": session_id,
                "verification_id": vid,
                "operator_id": operator_id,
                "drift_error_mm": drift_mm,
                "epoch_id": self._epoch_id,
            },
        )
        return snapshot

    def get_registration(self, session_id: str) -> Optional[RegistrationStatusRecord]:
        """Retrieve registration record by session_id."""
        return self._registrations.get(session_id)

    def is_registration_verified(self, session_id: str) -> bool:
        """Check if registration is verified and ready for navigation (M10 gate)."""
        reg = self._registrations.get(session_id)
        return bool(reg and reg.state == RegistrationState.VERIFIED and reg.transform is not None)

    def clear(self) -> None:
        """Clear all active registrations and fiducial clouds."""
        self._registrations.clear()
        self._fiducial_clouds.clear()

    # -------------------------------------------------------------------------
    # Helper & Verification Methods
    # -------------------------------------------------------------------------

    def _verify_locked_plan(self, plan_id: str) -> None:
        """Verify that referenced plan exists and is locked."""
        if self._planning_service is not None:
            plan = self._planning_service.get_plan(plan_id)
            if not plan.is_locked:
                raise RegistrationPlanMismatchError(
                    f"Registration requires plan {plan_id!r} to be locked (D315)"
                )

    def _emit_event(self, topic: str, payload: Mapping[str, Any]) -> None:
        """Emit protocol event over dispatcher."""
        env = create_event(
            message_name=topic,
            source=self.name,
            payload=dict(payload),
        )
        if self._dispatcher is not None and getattr(self._dispatcher, "state", None) == DispatcherState.STARTED:
            self._dispatcher.dispatch(env)

    # -------------------------------------------------------------------------
    # Dispatcher Route Handlers
    # -------------------------------------------------------------------------

    def handle_submit_command(self, command_envelope: MessageEnvelope) -> MessageEnvelope:
        payload = command_envelope.payload
        session_id = payload.get("session_id")
        plan_id = payload.get("plan_id")
        fiducials_data = payload.get("fiducials")

        if not session_id or not plan_id or not fiducials_data:
            return create_error_response(command_envelope, self.name, "ERR_INVALID_ARGS", "Missing required parameters")

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
            record = self.submit_fiducials(str(session_id), str(plan_id), cloud)
            return create_response(command_envelope, self.name, payload={"point_count": len(cloud.pairs), "state": record.state.value})
        except Exception as e:
            raw_err = str(e)
            redacted = self._secret_filter.redact(raw_err) if self._secret_filter else raw_err
            return create_error_response(command_envelope, self.name, _format_error_code(type(e).__name__), redacted)

    def handle_solve_command(self, command_envelope: MessageEnvelope) -> MessageEnvelope:
        payload = command_envelope.payload
        session_id = payload.get("session_id")
        plan_id = payload.get("plan_id")

        if not session_id or not plan_id:
            return create_error_response(command_envelope, self.name, "ERR_INVALID_ARGS", "Missing session_id or plan_id")

        try:
            record = self.solve_registration(str(session_id), str(plan_id))
            fre = record.quality_report.fre_rms_mm if record.quality_report else 0.0
            return create_response(command_envelope, self.name, payload={"state": record.state.value, "fre_rms_mm": fre})
        except Exception as e:
            raw_err = str(e)
            redacted = self._secret_filter.redact(raw_err) if self._secret_filter else raw_err
            return create_error_response(command_envelope, self.name, _format_error_code(type(e).__name__), redacted)

    def handle_get_query(self, query_envelope: MessageEnvelope) -> MessageEnvelope:
        session_id = query_envelope.payload.get("session_id")
        if not session_id or session_id not in self._registrations:
            return create_error_response(query_envelope, self.name, "ERR_REGISTRATION_NOT_FOUND", "Registration not found")

        reg = self._registrations[session_id]
        payload: dict[str, Any] = {
            "session_id": reg.session_id,
            "plan_id": reg.plan_id,
            "state": reg.state.value,
            "locked": reg.locked,
        }
        if reg.transform:
            payload["transform"] = {
                "rotation_matrix": [list(r) for r in reg.transform.rotation_matrix],
                "translation_vector_mm": list(reg.transform.translation_vector_mm),
                "source_frame": reg.transform.source_frame,
                "target_frame": reg.transform.target_frame,
            }
        if reg.quality_report:
            payload["quality"] = {
                "fre_rms_mm": reg.quality_report.fre_rms_mm,
                "fre_max_mm": reg.quality_report.fre_max_mm,
                "passed": reg.quality_report.passed_clinical_threshold,
            }
        return create_response(query_envelope, self.name, payload=payload)

    def handle_verify_command(self, command_envelope: MessageEnvelope) -> MessageEnvelope:
        payload = command_envelope.payload
        session_id = payload.get("session_id")
        operator_id = payload.get("operator_id", "surgeon_console")
        chk_plan = payload.get("checkpoint_plan_mm")
        chk_meas = payload.get("checkpoint_measured_mm")

        if not session_id or not chk_plan or not chk_meas:
            return create_error_response(command_envelope, self.name, "ERR_INVALID_ARGS", "Missing verification parameters")

        try:
            snap = self.verify_registration(
                session_id=str(session_id),
                operator_id=str(operator_id),
                checkpoint_plan_mm=tuple(chk_plan),  # type: ignore
                checkpoint_measured_mm=tuple(chk_meas),  # type: ignore
            )
            return create_response(command_envelope, self.name, payload={"verified": snap.passed, "drift_mm": snap.measured_drift_error_mm})
        except Exception as e:
            raw_err = str(e)
            redacted = self._secret_filter.redact(raw_err) if self._secret_filter else raw_err
            return create_error_response(command_envelope, self.name, _format_error_code(type(e).__name__), redacted)
