# -*- coding: utf-8 -*-
"""Shared Test Fixtures for M16 Registration Drift Tests."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Dict, Optional, Tuple
from unittest.mock import MagicMock

import pytest

from holomed.configuration.models import AppConfig
from holomed.core.dispatcher import MessageDispatcher
from holomed.core.models import DispatcherState
from holomed.drift.constants import DEFAULT_PATIENT_TRACKER_FRAME
from holomed.drift.models import LandmarkDefinition, LandmarkObservation
from holomed.registration.models import (
    RegistrationQualityReport,
    RegistrationState,
    RegistrationStatusRecord,
    RigidRegistrationTransform3D,
)
from holomed.registration.service import RegistrationService
from holomed.runtime.context import RuntimeContext
from holomed.runtime.logging import SecretFilter, StructuredLogger


def make_identity_transform(epoch_id: int = 1) -> RigidRegistrationTransform3D:
    """Create identity transform for testing."""
    return RigidRegistrationTransform3D(
        rotation_matrix=((1.0, 0.0, 0.0), (0.0, 1.0, 0.0), (0.0, 0.0, 1.0)),
        translation_vector_mm=(0.0, 0.0, 0.0),
        source_frame="plan_space",
        target_frame=DEFAULT_PATIENT_TRACKER_FRAME,
        epoch_id=epoch_id,
        created_at_utc=datetime.now(timezone.utc).isoformat(),
    )


def make_registration_record(
    session_id: str = "test-session-01",
    state: RegistrationState = RegistrationState.VERIFIED,
    epoch_id: int = 1,
) -> RegistrationStatusRecord:
    """Create verified registration record for testing."""
    transform = make_identity_transform(epoch_id)
    quality = RegistrationQualityReport(
        fre_rms_mm=0.5,
        fre_max_mm=0.8,
        per_fiducial_residuals_mm=(0.3, 0.4, 0.5),
        passed_clinical_threshold=True,
        warning_threshold_exceeded=False,
    )
    return RegistrationStatusRecord(
        session_id=session_id,
        plan_id="test-plan-01",
        epoch_id=epoch_id,
        state=state,
        transform=transform,
        quality_report=quality,
        locked=True,
        verified_at_utc=datetime.now(timezone.utc).isoformat(),
    )


def make_landmark_definition(
    landmark_id: str = "lm-nasion",
    name: str = "Nasion",
    planned_point_mm: Tuple[float, float, float] = (100.0, 0.0, 0.0),
    max_tolerance_mm: float = 2.0,
    min_confidence: float = 0.85,
    max_uncertainty_mm: float = 1.5,
) -> LandmarkDefinition:
    """Create a LandmarkDefinition for testing."""
    return LandmarkDefinition(
        landmark_id=landmark_id,
        name=name,
        planned_point_mm=planned_point_mm,
        max_tolerance_mm=max_tolerance_mm,
        min_confidence=min_confidence,
        max_uncertainty_mm=max_uncertainty_mm,
    )


def make_landmark_observation(
    landmark_id: str = "lm-nasion",
    session_id: str = "test-session-01",
    observed_point_mm: Tuple[float, float, float] = (100.0, 0.0, 0.0),
    confidence: float = 0.95,
    uncertainty_mm: float = 0.5,
    sequence_number: int = 1,
    epoch_id: int = 1,
    timestamp_utc: Optional[str] = None,
    coordinate_frame: str = DEFAULT_PATIENT_TRACKER_FRAME,
) -> LandmarkObservation:
    """Create a LandmarkObservation for testing."""
    if timestamp_utc is None:
        timestamp_utc = datetime.now(timezone.utc).isoformat()
    return LandmarkObservation(
        landmark_id=landmark_id,
        session_id=session_id,
        epoch_id=epoch_id,
        sequence_number=sequence_number,
        observed_point_mm=observed_point_mm,
        confidence=confidence,
        uncertainty_mm=uncertainty_mm,
        timestamp_utc=timestamp_utc,
        coordinate_frame=coordinate_frame,
    )


@pytest.fixture
def mock_dispatcher() -> MagicMock:
    """Create mock MessageDispatcher in STARTED state."""
    dispatcher = MagicMock(spec=MessageDispatcher)
    dispatcher.state = DispatcherState.STARTED
    return dispatcher


@pytest.fixture
def mock_registration_service() -> MagicMock:
    """Create mock RegistrationService with verified registration."""
    reg_svc = MagicMock(spec=RegistrationService)
    reg_record = make_registration_record()
    reg_svc.is_registration_verified.return_value = True
    reg_svc.get_registration.return_value = reg_record
    return reg_svc


@pytest.fixture
def mock_unverified_registration_service() -> MagicMock:
    """Create mock RegistrationService with unverified registration."""
    reg_svc = MagicMock(spec=RegistrationService)
    reg_svc.is_registration_verified.return_value = False
    return reg_svc


@pytest.fixture
def secret_filter() -> SecretFilter:
    return SecretFilter()


@pytest.fixture
def logger(secret_filter: SecretFilter) -> StructuredLogger:
    return StructuredLogger("test_drift", secret_filter=secret_filter)


@pytest.fixture
def runtime_context() -> RuntimeContext:
    """Create RuntimeContext for testing."""
    config = AppConfig(
        app_name="HoloMed-Drift-Test",
        environment="TESTING",
        host="127.0.0.1",
        port=8090,
        log_level="DEBUG",
        gemini_api_key=None,
        protocol_version="1.0",
    )
    return RuntimeContext(app_config=config, epoch_id=1)


@pytest.fixture
def default_landmarks() -> Tuple[LandmarkDefinition, ...]:
    """Create standard set of 3 test landmarks."""
    return (
        make_landmark_definition("lm-nasion", "Nasion", (100.0, 0.0, 0.0), max_tolerance_mm=2.0),
        make_landmark_definition("lm-tragus-r", "Right Tragus", (0.0, 100.0, 0.0), max_tolerance_mm=1.5),
        make_landmark_definition("lm-tragus-l", "Left Tragus", (0.0, -100.0, 0.0), max_tolerance_mm=1.8),
    )
