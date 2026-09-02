# -*- coding: utf-8 -*-
"""Shared Pytest Fixtures and Mock Factories for M18 Safety Gate Tests."""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import MagicMock

import pytest

from holomed.configuration.models import AppConfig
from holomed.core.dispatcher import MessageDispatcher
from holomed.core.models import DispatcherState
from holomed.drift.service import DriftService
from holomed.navigation.service import NavigationService
from holomed.persistence.service import PersistenceService
from holomed.proximity.service import ProximityService
from holomed.recovery.service import RecoveryService
from holomed.registration.models import RegistrationState
from holomed.registration.service import RegistrationService
from holomed.runtime.context import RuntimeContext
from holomed.runtime.logging import SecretFilter, StructuredLogger
from holomed.safety_gate.models import GateRequest, SafetyGateAction
from holomed.workflow.models import WorkflowPhase
from holomed.workflow.service import WorkflowService


@pytest.fixture
def secret_filter() -> SecretFilter:
    return SecretFilter()


@pytest.fixture
def logger(secret_filter: SecretFilter) -> StructuredLogger:
    return StructuredLogger("test_safety_gate_logger", secret_filter=secret_filter)


@pytest.fixture
def runtime_context() -> RuntimeContext:
    config = AppConfig(
        app_name="HoloMed-SafetyGate-Test",
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


def make_gate_request(
    session_id: str = "session-01",
    action: SafetyGateAction = SafetyGateAction.TOOL_NAVIGATION,
    sequence_number: int = 1,
    now_utc: str = "",
    instrument_id: str = "inst-01",
    target_trajectory_id: str = "traj-01",
) -> GateRequest:
    ts = now_utc or datetime.now(timezone.utc).isoformat()
    return GateRequest(
        session_id=session_id,
        action=action,
        sequence_number=sequence_number,
        now_utc=ts,
        instrument_id=instrument_id,
        target_trajectory_id=target_trajectory_id,
    )


@pytest.fixture
def mock_workflow_service() -> MagicMock:
    wf_svc = MagicMock(spec=WorkflowService)
    wf_snapshot = MagicMock()
    wf_snapshot.current_phase = WorkflowPhase.NAVIGATION
    wf_snapshot.is_terminal = False
    wf_svc.get_workflow_state.return_value = wf_snapshot
    return wf_svc


@pytest.fixture
def mock_registration_service() -> MagicMock:
    reg_svc = MagicMock(spec=RegistrationService)
    reg_rec = MagicMock()
    reg_rec.state = RegistrationState.VERIFIED
    reg_rec.epoch_id = 1
    reg_rec.locked = True
    reg_rec.transform = MagicMock()
    reg_svc.get_registration.return_value = reg_rec
    reg_svc.is_registration_verified.return_value = True
    return reg_svc


@pytest.fixture
def mock_navigation_service() -> MagicMock:
    nav_svc = MagicMock(spec=NavigationService)
    nav_status = MagicMock()
    nav_status.state.value = "TRACKING"
    nav_status.epoch_id = 1
    nav_status.has_bound_trajectory = True
    nav_svc.get_navigation_status.return_value = nav_status
    return nav_svc


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
def mock_drift_service() -> MagicMock:
    drift_svc = MagicMock(spec=DriftService)
    drift_status = MagicMock()
    drift_status.state.value = "STABLE"
    drift_status.epoch_id = 1
    drift_status.bound_landmark_count = 3
    drift_svc.get_drift_status.return_value = drift_status
    return drift_svc


@pytest.fixture
def mock_recovery_service() -> MagicMock:
    rec_svc = MagicMock(spec=RecoveryService)
    rec_status = MagicMock()
    rec_status.state.value = "IDLE"
    rec_status.registration_revision = 0
    rec_svc.get_recovery_status.return_value = rec_status
    return rec_svc


@pytest.fixture
def mock_persistence_service() -> MagicMock:
    return MagicMock(spec=PersistenceService)
