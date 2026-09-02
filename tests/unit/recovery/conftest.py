# -*- coding: utf-8 -*-
"""Shared Pytest Fixtures and Mock Factories for M17 Spatial Recovery Tests."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Tuple
from unittest.mock import MagicMock

import pytest

from holomed.configuration.models import AppConfig
from holomed.core.dispatcher import MessageDispatcher
from holomed.core.models import DispatcherState
from holomed.drift.models import LandmarkDefinition, LandmarkObservation
from holomed.drift.service import DriftService
from holomed.navigation.models import NavigationState, NavigationStatusRecord
from holomed.navigation.service import NavigationService
from holomed.persistence.service import PersistenceService
from holomed.planning.models import (
    SafetyExclusionZone,
    TrajectoryPlan,
)
from holomed.proximity.models import ProximityState, ProximityStatusRecord
from holomed.proximity.service import ProximityService
from holomed.recovery.models import RecoveryAuthorization
from holomed.registration.models import (
    FiducialCloud,
    FiducialPointPair,
    RegistrationQualityReport,
    RegistrationState,
    RegistrationStatusRecord,
    RegistrationVerificationSnapshot,
    RigidRegistrationTransform3D,
)
from holomed.registration.service import RegistrationService
from holomed.runtime.context import RuntimeContext
from holomed.runtime.logging import SecretFilter, StructuredLogger


@pytest.fixture
def secret_filter() -> SecretFilter:
    return SecretFilter()


@pytest.fixture
def logger(secret_filter: SecretFilter) -> StructuredLogger:
    return StructuredLogger("test_recovery_logger", secret_filter=secret_filter)


@pytest.fixture
def runtime_context() -> RuntimeContext:
    config = AppConfig(
        app_name="HoloMed-Recovery-Test",
        environment="TESTING",
        host="127.0.0.1",
        port=8090,
        log_level="DEBUG",
        gemini_api_key=None,
        protocol_version="1.0",
    )
    return RuntimeContext(app_config=config, epoch_id=1, trace_context=None)


@pytest.fixture
def mock_dispatcher() -> MagicMock:
    dispatcher = MagicMock(spec=MessageDispatcher)
    dispatcher.state = DispatcherState.STARTED
    return dispatcher


def make_identity_transform(epoch_id: int = 1) -> RigidRegistrationTransform3D:
    return RigidRegistrationTransform3D(
        rotation_matrix=(
            (1.0, 0.0, 0.0),
            (0.0, 1.0, 0.0),
            (0.0, 0.0, 1.0),
        ),
        translation_vector_mm=(0.0, 0.0, 0.0),
        source_frame="plan_frame",
        target_frame="patient_tracker_frame",
        epoch_id=epoch_id,
        created_at_utc=datetime.now(timezone.utc).isoformat(),
    )


def make_fiducial_cloud(
    offset_mm: Tuple[float, float, float] = (0.0, 0.0, 0.0),
    jitter_mm: float = 0.0,
) -> FiducialCloud:
    """Create standard 4-point facial fiducial cloud."""
    p_plan = [
        (100.0, 0.0, 0.0),
        (0.0, 100.0, 0.0),
        (0.0, -100.0, 0.0),
        (0.0, 0.0, 100.0),
    ]
    pairs = []
    for i, p in enumerate(p_plan):
        j = jitter_mm if i % 2 == 1 else 0.0
        p_meas = (p[0] + offset_mm[0] + j, p[1] + offset_mm[1], p[2] + offset_mm[2])
        pairs.append(
            FiducialPointPair(
                fiducial_id=f"fid-{i+1:02d}",
                planned_point_mm=p,
                measured_point_mm=p_meas,
                label=f"Landmark {i+1}",
            )
        )
    return FiducialCloud(pairs=tuple(pairs))


def make_recovery_authorization(
    operator_id: str = "dr_chen_sr",
    rationale: str = "Intraoperative tracker bump re-alignment",
    timestamp_utc: str = "",
    sequence_number: int = 1,
    authorization_reference: str = "AUTH-REC-2025-01",
) -> RecoveryAuthorization:
    ts = timestamp_utc or datetime.now(timezone.utc).isoformat()
    return RecoveryAuthorization(
        operator_id=operator_id,
        rationale=rationale,
        timestamp_utc=ts,
        sequence_number=sequence_number,
        authorization_reference=authorization_reference,
    )


@pytest.fixture
def mock_registration_service() -> MagicMock:
    reg_svc = MagicMock(spec=RegistrationService)
    transform = make_identity_transform(epoch_id=1)
    quality = RegistrationQualityReport(
        fre_rms_mm=0.4,
        fre_max_mm=0.6,
        per_fiducial_residuals_mm=(0.3, 0.4, 0.5, 0.4),
        passed_clinical_threshold=True,
        warning_threshold_exceeded=False,
    )
    status_rec = RegistrationStatusRecord(
        session_id="session-01",
        plan_id="plan-01",
        epoch_id=1,
        state=RegistrationState.VERIFIED,
        transform=transform,
        quality_report=quality,
        locked=True,
    )
    reg_svc.get_registration.return_value = status_rec
    reg_svc.is_registration_verified.return_value = True
    reg_svc.submit_fiducials.return_value = status_rec
    reg_svc.solve_registration.return_value = status_rec
    reg_svc.verify_registration.return_value = RegistrationVerificationSnapshot(
        verification_id="verif-01",
        session_id="session-01",
        epoch_id=1,
        verifier_operator_id="dr_chen",
        measured_drift_error_mm=0.4,
        passed=True,
        verified_at_utc=datetime.now(timezone.utc).isoformat(),
    )
    return reg_svc


@pytest.fixture
def mock_drift_service() -> MagicMock:
    drift_svc = MagicMock(spec=DriftService)
    drift_status = MagicMock()
    drift_status.state.value = "READY"
    drift_status.epoch_id = 1
    drift_status.bound_landmark_count = 3
    drift_svc.get_drift_status.return_value = drift_status
    return drift_svc


@pytest.fixture
def mock_proximity_service() -> MagicMock:
    prox_svc = MagicMock(spec=ProximityService)
    prox_status = MagicMock()
    prox_status.state.value = "SAFE"
    prox_status.epoch_id = 1
    prox_status.monitored_zone_count = 2
    prox_svc.get_proximity_status.return_value = prox_status
    return prox_svc


@pytest.fixture
def mock_navigation_service() -> MagicMock:
    nav_svc = MagicMock(spec=NavigationService)
    nav_status = MagicMock()
    nav_status.state.value = "IDLE"
    nav_status.epoch_id = 1
    nav_status.has_bound_trajectory = True
    nav_svc.get_navigation_status.return_value = nav_status
    return nav_svc


@pytest.fixture
def mock_persistence_service() -> MagicMock:
    persist_svc = MagicMock(spec=PersistenceService)
    return persist_svc
