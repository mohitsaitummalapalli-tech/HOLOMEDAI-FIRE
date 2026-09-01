# -*- coding: utf-8 -*-
"""Master Anatomy & Simulation Domain Service implementing IService for M05."""

from __future__ import annotations

from datetime import datetime, timezone
import time
from typing import Any, Mapping, Optional, Sequence
import uuid

from holomed.anatomy.events import RecordingAnatomyEventSink
from holomed.anatomy.exceptions import (
    AnatomyCapacityError,
    AnatomyEpochMismatchError,
    AnatomyHierarchyError,
    AnatomyLifecycleError,
    AnatomyResourceIntegrityError,
    AnatomyShutdownError,
    AnatomyValidationError,
)
from holomed.anatomy.hierarchy import AnatomyHierarchy
from holomed.anatomy.models import (
    AnatomicalEntity,
    Point3D,
    SimulationState,
    TissueStateSnapshot,
    Vector3D,
)
from holomed.anatomy.queries import AnatomyQueryEngine
from holomed.anatomy.serialization import serialize_anatomy_payload
from holomed.anatomy.simulation import AnatomicalSimulationEngine
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

STRUCTURAL_RESOURCE_IDS: tuple[str, ...] = (
    "anatomy.model",
    "anatomy.hierarchy",
    "anatomy.simulation",
    "anatomy.solver",
    "anatomy.metrics",
)


class AnatomyService(IService):
    """Anatomy & Simulation Service implementing IService."""

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
        self._logger = logger or StructuredLogger("anatomy_service", secret_filter=secret_filter)

        self._state: ServiceState = ServiceState.UNINITIALIZED
        self._context: Optional[RuntimeContext] = None
        self._resources: Optional[OwnedResourceSet] = None
        self._epoch_id: int = 0

        # Subsystems
        self._hierarchy: Optional[AnatomyHierarchy] = None
        self._simulation: Optional[AnatomicalSimulationEngine] = None
        self._query_engine: Optional[AnatomyQueryEngine] = None
        self._event_sink: Optional[RecordingAnatomyEventSink] = None

        # Metrics
        self._total_queries: int = 0
        self._total_sim_steps: int = 0
        self._in_transaction: bool = False

    @property
    def name(self) -> str:
        return "anatomy_service"

    @property
    def dependencies(self) -> tuple[str, ...]:
        return (
            "device_manager",
            "ultron_service",
        )

    @property
    def state(self) -> ServiceState:
        return self._state

    @property
    def resources(self) -> OwnedResourceSet:
        if self._resources is None:
            raise AnatomyLifecycleError("Service has not been initialized; resources unavailable")
        return self._resources

    @property
    def hierarchy(self) -> Optional[AnatomyHierarchy]:
        return self._hierarchy

    @property
    def simulation(self) -> Optional[AnatomicalSimulationEngine]:
        return self._simulation

    @property
    def event_sink(self) -> Optional[RecordingAnatomyEventSink]:
        return self._event_sink

    def initialize(self, context: RuntimeContext) -> None:
        """Initialize service state and acquire 5 structural handles."""
        if self._state not in (ServiceState.UNINITIALIZED, ServiceState.STOPPED):
            raise AnatomyLifecycleError(f"Cannot initialize AnatomyService in state {self._state.name}")

        self._context = context
        self._epoch_id = context.epoch_id
        self._resources = OwnedResourceSet(self.name, self._epoch_id)

        # Acquire 5 structural handles
        for res_id in STRUCTURAL_RESOURCE_IDS:
            self._resources.acquire(res_id)

        # Instantiate components
        self._hierarchy = AnatomyHierarchy()
        self._simulation = AnatomicalSimulationEngine(epoch_id=self._epoch_id)
        self._query_engine = AnatomyQueryEngine(self._hierarchy)
        self._event_sink = RecordingAnatomyEventSink()

        # Register Dispatcher Routes strictly during INITIALIZED
        if self._dispatcher is not None:
            self._dispatcher.register_query_handler(
                "anatomy.status",
                self.handle_status_query,
                self.name,
            )
            self._dispatcher.register_query_handler(
                "anatomy.entity",
                self.handle_entity_query,
                self.name,
            )
            self._dispatcher.register_query_handler(
                "anatomy.query",
                self.handle_spatial_query,
                self.name,
            )
            self._dispatcher.register_query_handler(
                "anatomy.simulation.status",
                self.handle_simulation_status_query,
                self.name,
            )
            self._dispatcher.register_command_handler(
                "anatomy.reset",
                self.handle_reset_command,
                self.name,
            )

        self._state = ServiceState.INITIALIZED

    def start(self) -> None:
        """Transition service to STARTED. Acquires zero new resources."""
        if self._state != ServiceState.INITIALIZED:
            raise AnatomyLifecycleError(f"Cannot start AnatomyService in state {self._state.name}")
        self._state = ServiceState.STARTED

    def stop(self) -> None:
        """Release all acquired resources and teardown state cleanly."""
        if self._state in (ServiceState.STOPPED, ServiceState.UNINITIALIZED):
            return

        if self._in_transaction:
            raise AnatomyLifecycleError("Cannot stop AnatomyService during an active transaction")

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
                raise AnatomyShutdownError(
                    f"AnatomyService teardown encountered {len(failures)} resource failure(s)",
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

        if any(rec.status == ResourceStatus.UNRELEASED_FAILURE for rec in self._resources.records.values()):
            return ServiceHealth(
                name=self.name,
                status=HealthStatus.UNHEALTHY,
                message="Resource release failure detected",
                timestamp_utc=now_utc,
            )

        entities_count = self._hierarchy.entity_count if self._hierarchy else 0
        return ServiceHealth(
            name=self.name,
            status=HealthStatus.HEALTHY,
            message=f"Active with {entities_count} entities, {self._total_sim_steps} sim steps",
            timestamp_utc=now_utc,
        )

    # -------------------------------------------------------------------------
    # Public Core API
    # -------------------------------------------------------------------------

    def register_entity(self, entity: AnatomicalEntity) -> None:
        """Register an anatomical entity into the hierarchy and simulation tracker."""
        if self._state != ServiceState.STARTED:
            raise AnatomyLifecycleError(f"Cannot register entity in state {self._state.name}")

        if self._in_transaction:
            raise AnatomyLifecycleError("Reentrant call to register_entity rejected by transaction guard")

        self._in_transaction = True
        try:
            if self._hierarchy is None or self._simulation is None:
                raise AnatomyResourceIntegrityError("Hierarchy or simulation engine is uninitialized")

            self._hierarchy.add_entity(entity)
            self._simulation.register_entity_tissue(entity.entity_id)

            self._emit_event(
                "anatomy.entity.registered",
                {
                    "entity_id": entity.entity_id,
                    "name": entity.name,
                    "classification": entity.classification.value,
                    "parent_id": entity.parent_id,
                },
            )
        finally:
            self._in_transaction = False

    def get_entity(self, entity_id: str) -> AnatomicalEntity:
        """Retrieve an anatomical entity by ID."""
        if self._hierarchy is None:
            raise AnatomyResourceIntegrityError("Hierarchy is uninitialized")
        return self._hierarchy.get_entity(entity_id)

    def step_simulation(self, count: int = 1) -> SimulationState:
        """Advance discrete anatomical simulation time."""
        if self._state != ServiceState.STARTED:
            raise AnatomyLifecycleError(f"Cannot step simulation in state {self._state.name}")

        if self._in_transaction:
            raise AnatomyLifecycleError("Reentrant call to step_simulation rejected by transaction guard")

        self._in_transaction = True
        try:
            if self._simulation is None:
                raise AnatomyResourceIntegrityError("Simulation engine is uninitialized")

            state = self._simulation.step(count=count)
            self._total_sim_steps += count

            self._emit_event(
                "anatomy.simulation.stepped",
                {
                    "step_count": state.step_count,
                    "simulation_time_s": state.simulation_time_s,
                    "active_constraints": state.active_constraints_count,
                },
            )
            return state
        finally:
            self._in_transaction = False

    def apply_tissue_constraint(
        self,
        entity_id: str,
        target_displacement: Vector3D,
        stiffness_kpa: float = 10.0,
    ) -> TissueStateSnapshot:
        """Apply displacement relaxation constraint to an anatomical entity."""
        if self._state != ServiceState.STARTED:
            raise AnatomyLifecycleError(f"Cannot apply constraint in state {self._state.name}")

        if self._in_transaction:
            raise AnatomyLifecycleError("Reentrant call to apply_tissue_constraint rejected by transaction guard")

        self._in_transaction = True
        try:
            if self._simulation is None:
                raise AnatomyResourceIntegrityError("Simulation engine is uninitialized")

            snapshot = self._simulation.apply_displacement_constraint(
                entity_id=entity_id,
                target_displacement=target_displacement,
                stiffness_kpa=stiffness_kpa,
            )
            return snapshot
        finally:
            self._in_transaction = False

    def reset(self, epoch_id: int) -> None:
        """Reset service state for a new epoch."""
        if epoch_id != self._epoch_id:
            raise AnatomyEpochMismatchError(
                f"Reset epoch {epoch_id} does not match service epoch {self._epoch_id}"
            )
        self.clear()

    def clear(self) -> None:
        """Clear all entities, simulation states, and event sink history."""
        if self._hierarchy is not None:
            self._hierarchy.clear()
        if self._simulation is not None:
            self._simulation.reset(self._epoch_id)
        if self._event_sink is not None:
            self._event_sink.clear()
        self._total_queries = 0
        self._total_sim_steps = 0

    # -------------------------------------------------------------------------
    # Dispatcher Handlers
    # -------------------------------------------------------------------------

    def handle_status_query(self, query_envelope: MessageEnvelope) -> MessageEnvelope:
        """Handle anatomy.status query."""
        entities_count = self._hierarchy.entity_count if self._hierarchy else 0
        sim_time = self._simulation.simulation_time_s if self._simulation else 0.0

        payload = serialize_anatomy_payload(
            {
                "service_name": self.name,
                "state": self._state.name,
                "epoch_id": self._epoch_id,
                "entity_count": entities_count,
                "simulation_time_s": sim_time,
                "total_sim_steps": self._total_sim_steps,
                "total_queries": self._total_queries,
            }
        )
        return create_response(query_envelope, self.name, payload=dict(payload))

    def handle_entity_query(self, query_envelope: MessageEnvelope) -> MessageEnvelope:
        """Handle anatomy.entity query."""
        entity_id = query_envelope.payload.get("entity_id")
        if not entity_id or self._hierarchy is None:
            return create_error_response(
                query_envelope,
                self.name,
                error_code="INVALID_ENTITY_QUERY",
                error_message="Missing or invalid entity_id",
            )

        try:
            ent = self._hierarchy.get_entity(str(entity_id))
            ancestry = self._hierarchy.get_ancestry(str(entity_id))
            children = self._hierarchy.get_children(str(entity_id))
            payload = serialize_anatomy_payload(
                {
                    "entity_id": ent.entity_id,
                    "name": ent.name,
                    "classification": ent.classification.value,
                    "parent_id": ent.parent_id,
                    "ancestry": list(ancestry),
                    "children": list(children),
                    "landmarks_count": len(ent.landmarks),
                }
            )
            return create_response(query_envelope, self.name, payload=dict(payload))
        except AnatomyHierarchyError as e:
            return create_error_response(
                query_envelope,
                self.name,
                error_code="ENTITY_NOT_FOUND",
                error_message=str(e),
            )

    def handle_spatial_query(self, query_envelope: MessageEnvelope) -> MessageEnvelope:
        """Handle anatomy.query spatial query."""
        self._total_queries += 1
        point_data = query_envelope.payload.get("point")
        if not point_data or len(point_data) != 3 or self._query_engine is None:
            return create_error_response(
                query_envelope,
                self.name,
                error_code="INVALID_SPATIAL_QUERY",
                error_message="point must be a list of 3 coordinates",
            )

        pt = Point3D(float(point_data[0]), float(point_data[1]), float(point_data[2]))
        matched_ids = self._query_engine.find_entity_by_point(pt)
        nearest = self._query_engine.find_nearest_landmark(pt)

        payload = serialize_anatomy_payload(
            {
                "point": [pt.x, pt.y, pt.z],
                "contained_entity_ids": list(matched_ids),
                "nearest_landmark": list(nearest) if nearest else None,
            }
        )
        return create_response(query_envelope, self.name, payload=dict(payload))

    def handle_simulation_status_query(self, query_envelope: MessageEnvelope) -> MessageEnvelope:
        """Handle anatomy.simulation.status query."""
        if self._simulation is None:
            return create_error_response(
                query_envelope,
                self.name,
                error_code="SIMULATION_UNINITIALIZED",
                error_message="Simulation engine is uninitialized",
            )

        state = self._simulation.state
        tissue_summary = [
            {
                "entity_id": s.entity_id,
                "displacement_mag": s.displacement.magnitude,
                "stress_kpa": s.stress_kpa,
                "strain": s.strain,
                "is_deformed": s.is_deformed,
            }
            for s in state.tissue_states.values()
        ]

        payload = serialize_anatomy_payload(
            {
                "epoch_id": state.epoch_id,
                "simulation_time_s": state.simulation_time_s,
                "step_count": state.step_count,
                "convergence_status": state.convergence_status.value,
                "is_simulated": state.is_simulated,
                "tissue_states": tissue_summary,
            }
        )
        return create_response(query_envelope, self.name, payload=dict(payload))

    def handle_reset_command(self, command_envelope: MessageEnvelope) -> MessageEnvelope:
        """Handle anatomy.reset command."""
        req_epoch = command_envelope.payload.get("epoch_id")
        if req_epoch != self._epoch_id:
            return create_error_response(
                command_envelope,
                self.name,
                error_code="EPOCH_MISMATCH",
                error_message=f"Command epoch {req_epoch} does not match service epoch {self._epoch_id}",
            )

        self.clear()
        payload = serialize_anatomy_payload({"reset_completed": True, "epoch_id": self._epoch_id})
        return create_response(command_envelope, self.name, payload=dict(payload))

    def _emit_event(self, topic: str, payload: Mapping[str, Any]) -> None:
        """Emit protocol event over dispatcher and record in event sink."""
        frozen_payload = serialize_anatomy_payload(payload)
        env = create_event(
            message_name=topic,
            source=self.name,
            payload=dict(frozen_payload),
        )

        if self._event_sink is not None:
            try:
                self._event_sink.record_event(env)
            except AnatomyCapacityError:
                pass  # Sink capacity exhaustion does not crash service

        if self._dispatcher is not None:
            self._dispatcher.dispatch(env)
