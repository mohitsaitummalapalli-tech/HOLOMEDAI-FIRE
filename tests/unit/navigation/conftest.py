# -*- coding: utf-8 -*-
"""Test Fixtures for M14 Navigation Test Suite."""

from __future__ import annotations

import pytest

from holomed.configuration.models import AppConfig
from holomed.core.dispatcher import MessageDispatcher
from holomed.planning.models import (
    PatientCaseContext,
    SurgicalLaterality,
    SurgicalPlanDefinition,
    TrajectoryPlan,
)
from holomed.planning.service import PlanningService
from holomed.registration.models import (
    FiducialCloud,
    FiducialPointPair,
)
from holomed.registration.service import RegistrationService
from holomed.runtime.context import RuntimeContext
from holomed.runtime.logging import SecretFilter


@pytest.fixture
def runtime_context() -> RuntimeContext:
    config = AppConfig(
        app_name="HoloMed-Navigation-Test",
        environment="TESTING",
        host="127.0.0.1",
        port=8090,
        log_level="DEBUG",
        gemini_api_key=None,
        protocol_version="1.0",
    )
    return RuntimeContext(app_config=config, epoch_id=1)


@pytest.fixture
def secret_filter() -> SecretFilter:
    return SecretFilter(secrets=["TOP_SECRET_PATIENT_TOKEN"])


@pytest.fixture
def message_dispatcher(runtime_context: RuntimeContext) -> MessageDispatcher:
    disp = MessageDispatcher()
    disp.initialize(runtime_context)
    return disp


@pytest.fixture
def sample_plan_trajectory() -> TrajectoryPlan:
    """Planned trajectory: entry (0, 0, 0), target (0, 0, 100)."""
    return TrajectoryPlan(
        trajectory_id="traj_pedicle_screw_l4",
        target_structure="pedicle_l4",
        entry_point_mm=(0.0, 0.0, 0.0),
        target_point_mm=(0.0, 0.0, 100.0),
        max_lateral_deviation_mm=2.0,
        max_angular_deviation_deg=5.0,
        min_confidence=0.80,
        max_uncertainty=0.20,
    )


@pytest.fixture
def verified_registration_service(
    runtime_context: RuntimeContext,
    secret_filter: SecretFilter,
    sample_plan_trajectory: TrajectoryPlan,
) -> RegistrationService:
    """A fully verified RegistrationService with verified registration for 'sess_nav_01'."""
    # 1. Setup Planning Service with locked plan
    plan_srv = PlanningService(secret_filter=secret_filter)
    plan_srv.initialize(runtime_context)
    plan_srv.start()

    ctx = PatientCaseContext(
        case_id="case_nav_01",
        patient_hash="a" * 64,
        procedure_code="SPINE_FUSION",
        laterality=SurgicalLaterality.LEFT,
        primary_surgeon_id="dr_curie",
        scheduled_date_utc="2026-09-01T12:00:00Z",
    )
    plan = SurgicalPlanDefinition(
        plan_id="plan_nav_01",
        version="1.0",
        case_context=ctx,
        trajectories=(sample_plan_trajectory,),
        exclusion_zones=(),
        is_locked=False,
    )
    plan_srv.submit_plan(plan, "sess_nav_01")
    plan_srv.lock_plan("plan_nav_01")

    # 2. Setup Registration Service (translation +10 X, +20 Y, +30 Z)
    reg_srv = RegistrationService(planning_service=plan_srv, secret_filter=secret_filter)
    reg_srv.initialize(runtime_context)
    reg_srv.start()

    pairs = (
        FiducialPointPair("f1", (0.0, 0.0, 0.0), (10.0, 20.0, 30.0), "p1"),
        FiducialPointPair("f2", (50.0, 0.0, 0.0), (60.0, 20.0, 30.0), "p2"),
        FiducialPointPair("f3", (0.0, 50.0, 0.0), (10.0, 70.0, 30.0), "p3"),
    )
    cloud = FiducialCloud(pairs=pairs)
    reg_srv.submit_fiducials("sess_nav_01", "plan_nav_01", cloud)
    reg_srv.solve_registration("sess_nav_01", "plan_nav_01")

    # Verify registration during WHO timeout (checkpoint matches transform)
    reg_srv.verify_registration(
        session_id="sess_nav_01",
        operator_id="surgeon_console",
        checkpoint_plan_mm=(0.0, 0.0, 0.0),
        checkpoint_measured_mm=(10.0, 20.0, 30.0),
    )
    assert reg_srv.is_registration_verified("sess_nav_01") is True
    return reg_srv


@pytest.fixture
def make_nav_capability():
    from holomed.execution._capability import _create_execution_capability

    def _factory(srv, session_id, seq=1):
        return _create_execution_capability(
            service_instance_id=id(srv),
            session_id=session_id,
            action="TOOL_NAVIGATION",
            sequence_number=seq,
        )

    return _factory
