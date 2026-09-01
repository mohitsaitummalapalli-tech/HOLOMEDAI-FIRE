# -*- coding: utf-8 -*-
"""Unit tests for AnatomyService Lifecycle, Dispatcher Routes, and Public API."""

from __future__ import annotations

import pytest

from holomed.anatomy.exceptions import (
    AnatomyEpochMismatchError,
    AnatomyLifecycleError,
    AnatomyShutdownError,
)
from holomed.anatomy.models import (
    AnatomicalEntity,
    Point3D,
    Vector3D,
)
from holomed.anatomy.service import STRUCTURAL_RESOURCE_IDS, AnatomyService
from holomed.core.dispatcher import MessageDispatcher
from holomed.devices.manager import DeviceManager
from holomed.protocol.builders import create_command, create_query
from holomed.runtime.context import RuntimeContext
from holomed.runtime.service import ServiceState


def test_anatomy_service_lifecycle_full_flow(
    runtime_context: RuntimeContext,
    device_manager: DeviceManager,
    message_dispatcher: MessageDispatcher,
) -> None:
    """Verify standard UNINITIALIZED -> INITIALIZED -> STARTED -> STOPPED lifecycle with 5 handles."""
    service = AnatomyService(device_manager=device_manager, dispatcher=message_dispatcher)
    assert service.state == ServiceState.UNINITIALIZED

    # Stop before initialize is safe no-op
    service.stop()
    assert service.state == ServiceState.UNINITIALIZED

    # Initialize acquires exactly 5 structural handles
    service.initialize(runtime_context)
    assert service.state == ServiceState.INITIALIZED
    assert len(service.resources.outstanding_handles) == len(STRUCTURAL_RESOURCE_IDS)

    # Start service (acquires zero new handles)
    service.start()
    assert service.state == ServiceState.STARTED
    assert len(service.resources.outstanding_handles) == 5

    # Double start raises AnatomyLifecycleError
    with pytest.raises(AnatomyLifecycleError):
        service.start()

    # Health check
    h = service.health()
    assert h.status.name == "HEALTHY"

    # Stop releases all 5 handles
    service.stop()
    assert service.state == ServiceState.STOPPED
    assert service.resources.is_empty is True

    # Idempotent stop
    service.stop()
    assert service.state == ServiceState.STOPPED


def test_anatomy_service_teardown_failure_handling(runtime_context: RuntimeContext) -> None:
    """Verify resource release failure transitions service to FAILED and raises AnatomyShutdownError."""
    service = AnatomyService()
    service.initialize(runtime_context)
    service.start()

    def failing_release(res_id: str) -> None:
        raise RuntimeError(f"Cleanup failed for {res_id}")

    service.resources.release = failing_release  # type: ignore

    with pytest.raises(AnatomyShutdownError) as exc_info:
        service.stop()

    assert service.state == ServiceState.FAILED
    assert len(exc_info.value.failures) == 5
    assert isinstance(exc_info.value.failures, tuple)


def test_anatomy_service_public_api(
    runtime_context: RuntimeContext,
    sample_organ_entity: AnatomicalEntity,
) -> None:
    """Verify register_entity, get_entity, step_simulation, apply_tissue_constraint, reset."""
    service = AnatomyService()
    service.initialize(runtime_context)
    service.start()

    # Register entity
    service.register_entity(sample_organ_entity)
    retrieved = service.get_entity(sample_organ_entity.entity_id)
    assert retrieved.entity_id == sample_organ_entity.entity_id

    # Step simulation
    sim_state = service.step_simulation(count=5)
    assert sim_state.step_count == 5
    assert sim_state.simulation_time_s == 0.05

    # Apply tissue constraint
    snap = service.apply_tissue_constraint(
        entity_id=sample_organ_entity.entity_id,
        target_displacement=Vector3D(0.02, 0.0, 0.0),
        stiffness_kpa=15.0,
    )
    assert snap.is_deformed is True

    # Reset
    service.reset(epoch_id=1)
    assert service.hierarchy.entity_count == 0

    with pytest.raises(AnatomyEpochMismatchError):
        service.reset(epoch_id=999)

    service.stop()


def test_anatomy_service_dispatcher_routes(
    runtime_context: RuntimeContext,
    device_manager: DeviceManager,
    message_dispatcher: MessageDispatcher,
    sample_organ_entity: AnatomicalEntity,
) -> None:
    """Verify dispatcher query and command routes."""
    service = AnatomyService(device_manager=device_manager, dispatcher=message_dispatcher)
    service.initialize(runtime_context)
    service.start()
    message_dispatcher.start()

    service.register_entity(sample_organ_entity)
    service.step_simulation(count=2)

    # 1. anatomy.status query
    q_status = create_query("anatomy.status", "client", target="anatomy_service", payload={})
    res_status = service.handle_status_query(q_status)
    assert res_status.payload["service_name"] == "anatomy_service"
    assert res_status.payload["entity_count"] == 1

    # 2. anatomy.entity query
    q_ent = create_query(
        "anatomy.entity",
        "client",
        target="anatomy_service",
        payload={"entity_id": sample_organ_entity.entity_id},
    )
    res_ent = service.handle_entity_query(q_ent)
    assert res_ent.payload["entity_id"] == sample_organ_entity.entity_id

    # 3. anatomy.query query (spatial)
    q_spat = create_query(
        "anatomy.query",
        "client",
        target="anatomy_service",
        payload={"point": [0.1, 0.2, 0.5]},
    )
    res_spat = service.handle_spatial_query(q_spat)
    assert sample_organ_entity.entity_id in res_spat.payload["contained_entity_ids"]

    # 4. anatomy.simulation.status query
    q_sim = create_query("anatomy.simulation.status", "client", target="anatomy_service", payload={})
    res_sim = service.handle_simulation_status_query(q_sim)
    assert res_sim.payload["is_simulated"] is True
    assert res_sim.payload["step_count"] == 2

    # 5. anatomy.reset command
    c_rst = create_command("anatomy.reset", "client", target="anatomy_service", payload={"epoch_id": 1})
    res_rst = service.handle_reset_command(c_rst)
    assert res_rst.payload["reset_completed"] is True

    service.stop()
