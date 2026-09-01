# -*- coding: utf-8 -*-
"""Test Fixtures for M13 Registration Test Suite."""

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
from holomed.runtime.context import RuntimeContext
from holomed.runtime.logging import SecretFilter


@pytest.fixture
def runtime_context() -> RuntimeContext:
    config = AppConfig(
        app_name="HoloMed-Registration-Test",
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
def sample_fiducials() -> tuple[FiducialPointPair, ...]:
    """Three non-collinear fiducial pairs with known rigid transform (translation +10 X, +20 Y, +30 Z)."""
    return (
        FiducialPointPair(
            fiducial_id="fid_01",
            planned_point_mm=(0.0, 0.0, 0.0),
            measured_point_mm=(10.0, 20.0, 30.0),
            label="anterior_superior_iliac_spine_r",
        ),
        FiducialPointPair(
            fiducial_id="fid_02",
            planned_point_mm=(50.0, 0.0, 0.0),
            measured_point_mm=(60.0, 20.0, 30.0),
            label="anterior_superior_iliac_spine_l",
        ),
        FiducialPointPair(
            fiducial_id="fid_03",
            planned_point_mm=(25.0, 50.0, 0.0),
            measured_point_mm=(35.0, 70.0, 30.0),
            label="pubic_symphysis",
        ),
    )


@pytest.fixture
def sample_cloud(sample_fiducials: tuple[FiducialPointPair, ...]) -> FiducialCloud:
    return FiducialCloud(pairs=sample_fiducials)


@pytest.fixture
def sample_locked_plan(runtime_context: RuntimeContext, secret_filter: SecretFilter) -> SurgicalPlanDefinition:
    ctx = PatientCaseContext(
        case_id="case_reg_01",
        patient_hash="b" * 64,
        procedure_code="THA_ROBOTIC",
        laterality=SurgicalLaterality.RIGHT,
        primary_surgeon_id="dr_curie",
        scheduled_date_utc="2026-09-01T12:00:00Z",
    )
    traj = TrajectoryPlan(
        trajectory_id="traj_femoral_stem",
        target_structure="femoral_canal",
        entry_point_mm=(10.0, 10.0, 10.0),
        target_point_mm=(10.0, 10.0, 60.0),
        max_lateral_deviation_mm=2.0,
        max_angular_deviation_deg=3.0,
        min_confidence=0.85,
        max_uncertainty=0.15,
    )
    plan = SurgicalPlanDefinition(
        plan_id="plan_reg_01",
        version="1.0",
        case_context=ctx,
        trajectories=(traj,),
        exclusion_zones=(),
        is_locked=True,
    )
    return plan


@pytest.fixture
def planning_service(
    runtime_context: RuntimeContext,
    secret_filter: SecretFilter,
    sample_locked_plan: SurgicalPlanDefinition,
) -> PlanningService:
    plan_srv = PlanningService(secret_filter=secret_filter)
    plan_srv.initialize(runtime_context)
    plan_srv.start()
    # submit and lock
    unlocked = SurgicalPlanDefinition(
        plan_id=sample_locked_plan.plan_id,
        version=sample_locked_plan.version,
        case_context=sample_locked_plan.case_context,
        trajectories=sample_locked_plan.trajectories,
        exclusion_zones=sample_locked_plan.exclusion_zones,
        is_locked=False,
    )
    plan_srv.submit_plan(unlocked, "sess_plan_01")
    plan_srv.lock_plan(sample_locked_plan.plan_id)
    return plan_srv
