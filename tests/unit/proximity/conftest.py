# -*- coding: utf-8 -*-
"""Shared Test Fixtures for M15 Proximity Monitoring Tests."""

from __future__ import annotations

import math
from datetime import datetime, timezone, timedelta
from typing import Dict, Optional, Tuple
from unittest.mock import MagicMock

import pytest

from holomed.core.dispatcher import MessageDispatcher
from holomed.core.models import DispatcherState
from holomed.planning.models import SafetyExclusionZone
from holomed.proximity.constants import DEFAULT_PATIENT_TRACKER_FRAME
from holomed.proximity.models import ToolClearanceGeometry
from holomed.registration.models import (
    RegistrationQualityReport,
    RegistrationState,
    RegistrationStatusRecord,
    RigidRegistrationTransform3D,
)
from holomed.registration.service import RegistrationService
from holomed.runtime.context import RuntimeContext
from holomed.runtime.logging import SecretFilter, StructuredLogger
from holomed.workflow.models import InterlockSeverity


# ---------------------------------------------------------------------------
# Helper Factories
# ---------------------------------------------------------------------------

def make_identity_transform(epoch_id: int = 0) -> RigidRegistrationTransform3D:
    """Create an identity rotation transform for testing."""
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
    epoch_id: int = 0,
) -> RegistrationStatusRecord:
    """Create a test registration status record."""
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


def make_zone(
    zone_id: str = "zone-a",
    center: Tuple[float, float, float] = (100.0, 0.0, 0.0),
    radius: float = 10.0,
    min_clearance: float = 5.0,
    severity: InterlockSeverity = InterlockSeverity.BLOCKING,
) -> SafetyExclusionZone:
    """Create a test exclusion zone."""
    return SafetyExclusionZone(
        zone_id=zone_id,
        anatomical_structure=f"structure_{zone_id}",
        center_point_mm=center,
        bounding_radius_mm=radius,
        min_clearance_mm=min_clearance,
        severity_if_breached=severity,
    )


def make_tool_geometry(
    instrument_id: str = "probe.01",
    session_id: str = "test-session-01",
    tip: Tuple[float, float, float] = (0.0, 0.0, 0.0),
    direction: Tuple[float, float, float] = (0.0, 0.0, 1.0),
    shaft_length: float = 100.0,
    shaft_radius: float = 1.0,
    confidence: float = 1.0,
    tracking_uncertainty_mm: float = 0.5,
    sequence_number: int = 1,
    epoch_id: int = 0,
    timestamp_utc: Optional[str] = None,
) -> ToolClearanceGeometry:
    """Create a test ToolClearanceGeometry."""
    if timestamp_utc is None:
        timestamp_utc = datetime.now(timezone.utc).isoformat()
    return ToolClearanceGeometry(
        instrument_id=instrument_id,
        session_id=session_id,
        epoch_id=epoch_id,
        sequence_number=sequence_number,
        tip_position_mm=tip,
        shaft_direction=direction,
        shaft_length_mm=shaft_length,
        shaft_radius_mm=shaft_radius,
        confidence=confidence,
        tracking_uncertainty_mm=tracking_uncertainty_mm,
        timestamp_utc=timestamp_utc,
        coordinate_frame=DEFAULT_PATIENT_TRACKER_FRAME,
    )


# ---------------------------------------------------------------------------
# Pytest Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def mock_dispatcher() -> MagicMock:
    """Create a mock MessageDispatcher in STARTED state."""
    dispatcher = MagicMock(spec=MessageDispatcher)
    dispatcher.state = DispatcherState.STARTED
    return dispatcher


@pytest.fixture
def mock_registration_service() -> MagicMock:
    """Create a mock RegistrationService with verified registration."""
    reg_svc = MagicMock(spec=RegistrationService)
    reg_record = make_registration_record()
    reg_svc.is_registration_verified.return_value = True
    reg_svc.get_registration.return_value = reg_record
    return reg_svc


@pytest.fixture
def mock_unverified_registration_service() -> MagicMock:
    """Create a mock RegistrationService with unverified registration."""
    reg_svc = MagicMock(spec=RegistrationService)
    reg_svc.is_registration_verified.return_value = False
    return reg_svc


@pytest.fixture
def secret_filter() -> SecretFilter:
    return SecretFilter()


@pytest.fixture
def logger(secret_filter: SecretFilter) -> StructuredLogger:
    return StructuredLogger("test_proximity", secret_filter=secret_filter)


@pytest.fixture
def runtime_context() -> RuntimeContext:
    """Create a RuntimeContext for testing."""
    from holomed.configuration.models import AppConfig
    config = AppConfig(
        app_name="HoloMed-Proximity-Test",
        environment="TESTING",
        host="127.0.0.1",
        port=8090,
        log_level="DEBUG",
        gemini_api_key=None,
        protocol_version="1.0",
    )
    return RuntimeContext(app_config=config, epoch_id=1)


@pytest.fixture
def default_zones() -> Tuple[SafetyExclusionZone, ...]:
    """Create a standard set of 3 test zones at different positions."""
    return (
        make_zone("zone-a", center=(100.0, 0.0, 0.0), radius=10.0, min_clearance=5.0, severity=InterlockSeverity.BLOCKING),
        make_zone("zone-b", center=(0.0, 100.0, 0.0), radius=15.0, min_clearance=8.0, severity=InterlockSeverity.CRITICAL),
        make_zone("zone-c", center=(-50.0, -50.0, 0.0), radius=8.0, min_clearance=3.0, severity=InterlockSeverity.WARNING),
    )
