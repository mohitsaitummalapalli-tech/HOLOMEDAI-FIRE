# -*- coding: utf-8 -*-
"""Unit Tests for M15 ProximityService — Lifecycle, Bind, Evaluate, Dispatch."""

from __future__ import annotations

import math
from datetime import datetime, timezone
from typing import Tuple
from unittest.mock import MagicMock

import pytest

from holomed.planning.models import SafetyExclusionZone
from holomed.proximity.constants import (
    DEFAULT_PATIENT_TRACKER_FRAME,
    MAX_ACTIVE_PROXIMITY_SESSIONS,
    MAX_MONITORED_ZONES,
    MAX_REGISTRATION_ERROR_MM,
)
from holomed.proximity.exceptions import (
    ProximityCapacityError,
    ProximityLifecycleError,
    ProximityRegistrationError,
    ProximitySequenceError,
    ProximityShutdownError,
    ProximityValidationError,
)
from holomed.proximity.models import ProximityState
from holomed.proximity.service import ProximityService, STRUCTURAL_RESOURCE_IDS
from holomed.registration.models import RegistrationState
from holomed.runtime.models import HealthStatus
from holomed.runtime.service import ServiceState
from holomed.workflow.models import InterlockSeverity

from tests.unit.proximity.conftest import (
    make_registration_record,
    make_tool_geometry,
    make_zone,
)


def _make_started_service(
    mock_dispatcher=None,
    mock_registration_service=None,
    secret_filter=None,
    logger=None,
    runtime_context=None,
) -> ProximityService:
    """Helper to create and start a ProximityService for testing."""
    svc = ProximityService(
        dispatcher=mock_dispatcher,
        registration_service=mock_registration_service,
        secret_filter=secret_filter,
        logger=logger,
    )
    svc.initialize(runtime_context)
    svc.start()
    return svc


# ===========================================================================
# Lifecycle Tests
# ===========================================================================

class TestProximityServiceLifecycle:
    """Tests for ProximityService IService lifecycle."""

    def test_initial_state_uninitialized(self, mock_dispatcher, mock_registration_service, secret_filter, logger) -> None:
        svc = ProximityService(mock_dispatcher, mock_registration_service, secret_filter, logger)
        assert svc.state == ServiceState.UNINITIALIZED

    def test_initialize_acquires_4_handles(self, mock_dispatcher, mock_registration_service, secret_filter, logger, runtime_context) -> None:
        svc = ProximityService(mock_dispatcher, mock_registration_service, secret_filter, logger)
        svc.initialize(runtime_context)
        assert svc.state == ServiceState.INITIALIZED
        assert len(svc.resources.outstanding_handles) == 4

    def test_start_transitions_to_started(self, mock_dispatcher, mock_registration_service, secret_filter, logger, runtime_context) -> None:
        svc = ProximityService(mock_dispatcher, mock_registration_service, secret_filter, logger)
        svc.initialize(runtime_context)
        svc.start()
        assert svc.state == ServiceState.STARTED

    def test_stop_releases_all_handles(self, mock_dispatcher, mock_registration_service, secret_filter, logger, runtime_context) -> None:
        svc = _make_started_service(mock_dispatcher, mock_registration_service, secret_filter, logger, runtime_context)
        svc.stop()
        assert svc.state == ServiceState.STOPPED
        assert svc.resources.is_empty

    def test_stop_idempotent(self, mock_dispatcher, mock_registration_service, secret_filter, logger, runtime_context) -> None:
        svc = _make_started_service(mock_dispatcher, mock_registration_service, secret_filter, logger, runtime_context)
        svc.stop()
        svc.stop()  # Second stop should be no-op
        assert svc.state == ServiceState.STOPPED

    def test_cannot_initialize_when_started(self, mock_dispatcher, mock_registration_service, secret_filter, logger, runtime_context) -> None:
        svc = _make_started_service(mock_dispatcher, mock_registration_service, secret_filter, logger, runtime_context)
        with pytest.raises(ProximityLifecycleError, match="Cannot initialize"):
            svc.initialize(runtime_context)

    def test_cannot_start_when_uninitialized(self, mock_dispatcher, mock_registration_service, secret_filter, logger) -> None:
        svc = ProximityService(mock_dispatcher, mock_registration_service, secret_filter, logger)
        with pytest.raises(ProximityLifecycleError, match="Cannot start"):
            svc.start()

    def test_resources_unavailable_before_init(self, mock_dispatcher, mock_registration_service, secret_filter, logger) -> None:
        svc = ProximityService(mock_dispatcher, mock_registration_service, secret_filter, logger)
        with pytest.raises(ProximityLifecycleError, match="resources unavailable"):
            _ = svc.resources

    def test_name_is_proximity_service(self, mock_dispatcher, mock_registration_service, secret_filter, logger) -> None:
        svc = ProximityService(mock_dispatcher, mock_registration_service, secret_filter, logger)
        assert svc.name == "proximity_service"

    def test_dependencies(self, mock_dispatcher, mock_registration_service, secret_filter, logger) -> None:
        svc = ProximityService(mock_dispatcher, mock_registration_service, secret_filter, logger)
        assert "dispatcher" in svc.dependencies
        assert "registration_service" in svc.dependencies

    def test_structural_resource_ids(self) -> None:
        assert len(STRUCTURAL_RESOURCE_IDS) == 4
        assert "proximity.registry" in STRUCTURAL_RESOURCE_IDS
        assert "proximity.evaluator" in STRUCTURAL_RESOURCE_IDS
        assert "proximity.zones" in STRUCTURAL_RESOURCE_IDS
        assert "proximity.interlock" in STRUCTURAL_RESOURCE_IDS


# ===========================================================================
# Health Check Tests
# ===========================================================================

class TestProximityServiceHealth:
    """Tests for health() reporting."""

    def test_healthy_when_started(self, mock_dispatcher, mock_registration_service, secret_filter, logger, runtime_context) -> None:
        svc = _make_started_service(mock_dispatcher, mock_registration_service, secret_filter, logger, runtime_context)
        health = svc.health()
        assert health.status == HealthStatus.HEALTHY

    def test_unhealthy_when_not_started(self, mock_dispatcher, mock_registration_service, secret_filter, logger, runtime_context) -> None:
        svc = ProximityService(mock_dispatcher, mock_registration_service, secret_filter, logger)
        svc.initialize(runtime_context)
        health = svc.health()
        assert health.status == HealthStatus.UNHEALTHY


# ===========================================================================
# Bind Zones Tests
# ===========================================================================

class TestProximityServiceBindZones:
    """Tests for bind_zones()."""

    def test_bind_zones_success(self, mock_dispatcher, mock_registration_service, secret_filter, logger, runtime_context, default_zones) -> None:
        svc = _make_started_service(mock_dispatcher, mock_registration_service, secret_filter, logger, runtime_context)
        svc.bind_zones("test-session-01", default_zones, registration_error_mm=1.0)
        assert svc.active_sessions_count == 1

    def test_bind_zones_without_registration_fails(self, mock_dispatcher, mock_unverified_registration_service, secret_filter, logger, runtime_context) -> None:
        svc = _make_started_service(mock_dispatcher, mock_unverified_registration_service, secret_filter, logger, runtime_context)
        zone = make_zone("zone-a", center=(100.0, 0.0, 0.0))
        with pytest.raises(ProximityRegistrationError, match="verified M13"):
            svc.bind_zones("test-session-01", (zone,), registration_error_mm=1.0)

    def test_bind_zones_negative_registration_error(self, mock_dispatcher, mock_registration_service, secret_filter, logger, runtime_context) -> None:
        svc = _make_started_service(mock_dispatcher, mock_registration_service, secret_filter, logger, runtime_context)
        zone = make_zone("zone-a", center=(100.0, 0.0, 0.0))
        with pytest.raises(ProximityValidationError, match="non-negative"):
            svc.bind_zones("test-session-01", (zone,), registration_error_mm=-1.0)

    def test_bind_zones_excessive_registration_error(self, mock_dispatcher, mock_registration_service, secret_filter, logger, runtime_context) -> None:
        svc = _make_started_service(mock_dispatcher, mock_registration_service, secret_filter, logger, runtime_context)
        zone = make_zone("zone-a", center=(100.0, 0.0, 0.0))
        with pytest.raises(ProximityValidationError, match="exceeds maximum"):
            svc.bind_zones("test-session-01", (zone,), registration_error_mm=MAX_REGISTRATION_ERROR_MM + 1.0)

    def test_bind_zones_duplicate_zone_ids(self, mock_dispatcher, mock_registration_service, secret_filter, logger, runtime_context) -> None:
        svc = _make_started_service(mock_dispatcher, mock_registration_service, secret_filter, logger, runtime_context)
        zones = (
            make_zone("zone-a", center=(100.0, 0.0, 0.0)),
            make_zone("zone-a", center=(0.0, 100.0, 0.0)),
        )
        with pytest.raises(ProximityValidationError, match="Duplicate zone_id"):
            svc.bind_zones("test-session-01", zones, registration_error_mm=1.0)

    def test_bind_when_not_started(self, mock_dispatcher, mock_registration_service, secret_filter, logger, runtime_context) -> None:
        svc = ProximityService(mock_dispatcher, mock_registration_service, secret_filter, logger)
        svc.initialize(runtime_context)
        zone = make_zone("zone-a", center=(100.0, 0.0, 0.0))
        with pytest.raises(ProximityLifecycleError, match="Cannot bind"):
            svc.bind_zones("test-session-01", (zone,), registration_error_mm=1.0)

    def test_session_capacity_limit(self, mock_dispatcher, mock_registration_service, secret_filter, logger, runtime_context) -> None:
        svc = _make_started_service(mock_dispatcher, mock_registration_service, secret_filter, logger, runtime_context)
        zone = make_zone("zone-a", center=(100.0, 0.0, 0.0))
        for i in range(MAX_ACTIVE_PROXIMITY_SESSIONS):
            svc.bind_zones(f"session-{i:03d}", (zone,), registration_error_mm=1.0)
        with pytest.raises(ProximityCapacityError, match="Max active"):
            svc.bind_zones("session-overflow", (zone,), registration_error_mm=1.0)


# ===========================================================================
# Submit Geometry & Evaluate Tests
# ===========================================================================

class TestProximityServiceEvaluate:
    """Tests for submit_geometry() and evaluate()."""

    def test_full_evaluate_flow(self, mock_dispatcher, mock_registration_service, secret_filter, logger, runtime_context) -> None:
        svc = _make_started_service(mock_dispatcher, mock_registration_service, secret_filter, logger, runtime_context)
        zone = make_zone("zone-a", center=(100.0, 0.0, 0.0), radius=10.0, min_clearance=5.0)
        svc.bind_zones("test-session-01", (zone,), registration_error_mm=1.0)

        tool = make_tool_geometry(tip=(0.0, 0.0, 0.0))
        svc.submit_geometry(tool)

        result = svc.evaluate("test-session-01")
        assert result.worst_state == ProximityState.CLEAR
        assert result.is_safe is True

    def test_evaluate_without_geometry_fails(self, mock_dispatcher, mock_registration_service, secret_filter, logger, runtime_context) -> None:
        svc = _make_started_service(mock_dispatcher, mock_registration_service, secret_filter, logger, runtime_context)
        zone = make_zone("zone-a", center=(100.0, 0.0, 0.0))
        svc.bind_zones("test-session-01", (zone,), registration_error_mm=1.0)

        with pytest.raises(ProximityValidationError, match="No tool geometry"):
            svc.evaluate("test-session-01")

    def test_evaluate_without_bound_zones_fails(self, mock_dispatcher, mock_registration_service, secret_filter, logger, runtime_context) -> None:
        svc = _make_started_service(mock_dispatcher, mock_registration_service, secret_filter, logger, runtime_context)
        with pytest.raises(ProximityLifecycleError, match="No proximity zones"):
            svc.evaluate("unknown-session")

    def test_sequence_number_monotonicity(self, mock_dispatcher, mock_registration_service, secret_filter, logger, runtime_context) -> None:
        svc = _make_started_service(mock_dispatcher, mock_registration_service, secret_filter, logger, runtime_context)
        zone = make_zone("zone-a", center=(100.0, 0.0, 0.0))
        svc.bind_zones("test-session-01", (zone,), registration_error_mm=1.0)

        tool1 = make_tool_geometry(sequence_number=5)
        svc.submit_geometry(tool1)

        tool2 = make_tool_geometry(sequence_number=3)
        with pytest.raises(ProximitySequenceError, match="Non-monotonic"):
            svc.submit_geometry(tool2)

    def test_submit_geometry_before_bind_fails(self, mock_dispatcher, mock_registration_service, secret_filter, logger, runtime_context) -> None:
        svc = _make_started_service(mock_dispatcher, mock_registration_service, secret_filter, logger, runtime_context)
        tool = make_tool_geometry()
        with pytest.raises(ProximityLifecycleError, match="No proximity zones bound"):
            svc.submit_geometry(tool)

    def test_registration_invalidation_interlocks(self, mock_dispatcher, mock_registration_service, secret_filter, logger, runtime_context) -> None:
        """When registration becomes unverified mid-session, evaluate should interlock."""
        svc = _make_started_service(mock_dispatcher, mock_registration_service, secret_filter, logger, runtime_context)
        zone = make_zone("zone-a", center=(100.0, 0.0, 0.0))
        svc.bind_zones("test-session-01", (zone,), registration_error_mm=1.0)

        tool = make_tool_geometry()
        svc.submit_geometry(tool)

        # Now invalidate registration
        unverified_record = make_registration_record(state=RegistrationState.INVALIDATED)
        mock_registration_service.get_registration.return_value = unverified_record

        with pytest.raises(ProximityRegistrationError, match="not verified"):
            svc.evaluate("test-session-01")

        # Session should be INTERLOCKED
        status = svc.get_proximity_status("test-session-01")
        assert status.state == ProximityState.INTERLOCKED


# ===========================================================================
# Status Query Tests
# ===========================================================================

class TestProximityServiceStatus:
    """Tests for get_proximity_status() and get_monitored_zones()."""

    def test_status_before_bind(self, mock_dispatcher, mock_registration_service, secret_filter, logger, runtime_context) -> None:
        svc = _make_started_service(mock_dispatcher, mock_registration_service, secret_filter, logger, runtime_context)
        status = svc.get_proximity_status("unknown-session")
        assert status.state == ProximityState.CLEAR
        assert status.monitored_zone_count == 0

    def test_status_after_bind(self, mock_dispatcher, mock_registration_service, secret_filter, logger, runtime_context) -> None:
        svc = _make_started_service(mock_dispatcher, mock_registration_service, secret_filter, logger, runtime_context)
        zones = (make_zone("zone-a"), make_zone("zone-b", center=(0.0, 100.0, 0.0)))
        svc.bind_zones("test-session-01", zones, registration_error_mm=1.0)

        status = svc.get_proximity_status("test-session-01")
        assert status.state == ProximityState.CLEAR
        assert status.monitored_zone_count == 2
        assert status.is_monitoring_active is True

    def test_get_monitored_zones(self, mock_dispatcher, mock_registration_service, secret_filter, logger, runtime_context) -> None:
        svc = _make_started_service(mock_dispatcher, mock_registration_service, secret_filter, logger, runtime_context)
        zones = (make_zone("zone-a"), make_zone("zone-b", center=(0.0, 100.0, 0.0)))
        svc.bind_zones("test-session-01", zones, registration_error_mm=1.0)

        result = svc.get_monitored_zones("test-session-01")
        assert len(result) == 2

    def test_get_monitored_zones_empty(self, mock_dispatcher, mock_registration_service, secret_filter, logger, runtime_context) -> None:
        svc = _make_started_service(mock_dispatcher, mock_registration_service, secret_filter, logger, runtime_context)
        result = svc.get_monitored_zones("unknown-session")
        assert len(result) == 0


# ===========================================================================
# Clear Tests
# ===========================================================================

class TestProximityServiceClear:
    """Tests for clear()."""

    def test_clear_removes_all_state(self, mock_dispatcher, mock_registration_service, secret_filter, logger, runtime_context) -> None:
        svc = _make_started_service(mock_dispatcher, mock_registration_service, secret_filter, logger, runtime_context)
        zone = make_zone("zone-a", center=(100.0, 0.0, 0.0))
        svc.bind_zones("test-session-01", (zone,), registration_error_mm=1.0)
        tool = make_tool_geometry()
        svc.submit_geometry(tool)
        svc.evaluate("test-session-01")

        svc.clear()
        assert svc.active_sessions_count == 0
        assert svc.get_monitored_zones("test-session-01") == ()
