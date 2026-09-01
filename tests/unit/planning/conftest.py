# -*- coding: utf-8 -*-
"""Test Fixtures for M12 Planning Test Suite."""

from __future__ import annotations

import pytest

from holomed.configuration.models import AppConfig
from holomed.core.dispatcher import MessageDispatcher
from holomed.planning.models import (
    PatientCaseContext,
    SafetyExclusionZone,
    SurgicalLaterality,
    SurgicalPlanDefinition,
    TrajectoryPlan,
)
from holomed.runtime.context import RuntimeContext
from holomed.runtime.logging import SecretFilter
from holomed.workflow.models import InterlockSeverity


@pytest.fixture
def runtime_context() -> RuntimeContext:
    config = AppConfig(
        app_name="HoloMed-Planning-Test",
        environment="TESTING",
        host="127.0.0.1",
        port=8089,
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
def sample_case_context() -> PatientCaseContext:
    return PatientCaseContext(
        case_id="case_tha_001",
        patient_hash="e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
        procedure_code="THA_POSTERIOR_APPROACH",
        laterality=SurgicalLaterality.RIGHT,
        primary_surgeon_id="surgeon_smith_01",
        scheduled_date_utc="2026-09-15T08:00:00Z",
    )


@pytest.fixture
def sample_trajectory() -> TrajectoryPlan:
    return TrajectoryPlan(
        trajectory_id="traj_cup_reaming",
        target_structure="acetabular_fossa",
        entry_point_mm=(10.0, 20.0, 30.0),
        target_point_mm=(10.0, 20.0, 80.0),
        max_lateral_deviation_mm=2.0,
        max_angular_deviation_deg=3.0,
        min_confidence=0.85,
        max_uncertainty=0.15,
    )


@pytest.fixture
def sample_exclusion_zone() -> SafetyExclusionZone:
    return SafetyExclusionZone(
        zone_id="zone_sciatic_nerve",
        anatomical_structure="sciatic_nerve_trunk",
        center_point_mm=(35.0, 40.0, 75.0),
        bounding_radius_mm=10.0,
        min_clearance_mm=5.0,
        severity_if_breached=InterlockSeverity.BLOCKING,
    )


@pytest.fixture
def sample_plan(
    sample_case_context: PatientCaseContext,
    sample_trajectory: TrajectoryPlan,
    sample_exclusion_zone: SafetyExclusionZone,
) -> SurgicalPlanDefinition:
    return SurgicalPlanDefinition(
        plan_id="plan_tha_right_01",
        version="1.0",
        case_context=sample_case_context,
        trajectories=(sample_trajectory,),
        exclusion_zones=(sample_exclusion_zone,),
    )
