# -*- coding: utf-8 -*-
"""End-to-End Tests for M15 Proximity Monitoring — Full Evaluation Pipeline."""

from __future__ import annotations

import math
from datetime import datetime, timezone, timedelta
from unittest.mock import MagicMock

import pytest

from holomed.planning.models import SafetyExclusionZone
from holomed.proximity.constants import DEFAULT_PATIENT_TRACKER_FRAME
from holomed.proximity.models import ProximityState, ProximitySeverity
from holomed.proximity.service import ProximityService
from holomed.registration.models import RegistrationState
from holomed.runtime.service import ServiceState
from holomed.workflow.models import InterlockSeverity

from tests.unit.proximity.conftest import (
    make_registration_record,
    make_tool_geometry,
    make_zone,
)


class TestProximityE2E:
    """End-to-end integration tests through the full proximity pipeline."""

    def test_full_lifecycle(self, mock_dispatcher, mock_registration_service, secret_filter, logger, runtime_context) -> None:
        """Full lifecycle: init -> start -> bind -> submit -> evaluate -> stop."""
        svc = ProximityService(mock_dispatcher, mock_registration_service, secret_filter, logger)
        svc.initialize(runtime_context)
        svc.start()

        # Bind 2 zones
        zones = (
            make_zone("zone-a", center=(100.0, 0.0, 0.0), radius=10.0, min_clearance=5.0, severity=InterlockSeverity.BLOCKING),
            make_zone("zone-b", center=(0.0, 100.0, 0.0), radius=15.0, min_clearance=8.0, severity=InterlockSeverity.CRITICAL),
        )
        svc.bind_zones("session-01", zones, registration_error_mm=1.0, static_margin_mm=0.5)

        # Submit geometry - tool far from both zones
        tool = make_tool_geometry(session_id="session-01", tip=(0.0, 0.0, 0.0), sequence_number=1)
        svc.submit_geometry(tool)

        # Evaluate - should be CLEAR
        result = svc.evaluate("session-01")
        assert result.worst_state == ProximityState.CLEAR
        assert result.is_safe is True
        assert len(result.zone_results) == 2

        # Status check
        status = svc.get_proximity_status("session-01")
        assert status.state == ProximityState.CLEAR
        assert status.monitored_zone_count == 2
        assert status.is_monitoring_active is True

        # Stop
        svc.stop()
        assert svc.state == ServiceState.STOPPED

    def test_approach_and_breach_sequence(self, mock_dispatcher, mock_registration_service, secret_filter, logger, runtime_context) -> None:
        """Simulate tool approaching a zone: CLEAR -> WARNING -> BREACHED."""
        svc = ProximityService(mock_dispatcher, mock_registration_service, secret_filter, logger)
        svc.initialize(runtime_context)
        svc.start()

        # Zone at (50, 0, 0), radius=10, min_clearance=5
        zone = make_zone("zone-a", center=(50.0, 0.0, 0.0), radius=10.0, min_clearance=5.0)
        svc.bind_zones("session-01", (zone,), registration_error_mm=1.0, static_margin_mm=0.0)

        t0 = datetime(2025, 1, 1, 0, 0, 0, tzinfo=timezone.utc)

        # Step 1: Far away -> CLEAR
        tool1 = make_tool_geometry(
            session_id="session-01", tip=(0.0, 0.0, 0.0),
            sequence_number=1, timestamp_utc=t0.isoformat(),
        )
        svc.submit_geometry(tool1)
        r1 = svc.evaluate("session-01", now_utc=t0.isoformat())
        assert r1.worst_state == ProximityState.CLEAR

        # Step 2: Closer but still outside effective radius warning band
        t1 = t0 + timedelta(milliseconds=100)
        # R_eff = 10 + 0 + 1 + 2*0.5 = 12
        # Need eff_clearance > 0 but <= min_clearance(5) -> WARNING
        # capsule_clearance = dist - R_eff - shaft_radius
        # dist = |tip_x - 50| = 50 - tip_x
        # clearance = (50 - tip_x) - 12 - 1 = 37 - tip_x
        # For clearance = 3 (WARNING): tip_x = 34
        tool2 = make_tool_geometry(
            session_id="session-01", tip=(34.0, 0.0, 0.0),
            sequence_number=2, timestamp_utc=t1.isoformat(),
        )
        svc.submit_geometry(tool2)
        r2 = svc.evaluate("session-01", now_utc=t1.isoformat())
        assert r2.worst_state == ProximityState.WARNING

        # Step 3: Inside effective radius -> BREACHED
        t2 = t1 + timedelta(milliseconds=100)
        # clearance = 37 - tip_x. For clearance < 0: tip_x > 37
        tool3 = make_tool_geometry(
            session_id="session-01", tip=(40.0, 0.0, 0.0),
            sequence_number=3, timestamp_utc=t2.isoformat(),
        )
        svc.submit_geometry(tool3)
        r3 = svc.evaluate("session-01", now_utc=t2.isoformat())
        assert r3.worst_state == ProximityState.BREACHED

        svc.stop()

    def test_multi_instrument_evaluation(self, mock_dispatcher, mock_registration_service, secret_filter, logger, runtime_context) -> None:
        """Evaluate with multiple instruments on the same session."""
        svc = ProximityService(mock_dispatcher, mock_registration_service, secret_filter, logger)
        svc.initialize(runtime_context)
        svc.start()

        zone = make_zone("zone-a", center=(100.0, 0.0, 0.0), radius=10.0, min_clearance=5.0)
        svc.bind_zones("session-01", (zone,), registration_error_mm=1.0)

        # Submit geometry for instrument A
        tool_a = make_tool_geometry(
            instrument_id="probe.a", session_id="session-01",
            tip=(0.0, 0.0, 0.0), sequence_number=1,
        )
        svc.submit_geometry(tool_a)

        # Submit geometry for instrument B
        tool_b = make_tool_geometry(
            instrument_id="probe.b", session_id="session-01",
            tip=(95.0, 0.0, 0.0), sequence_number=1,  # Close to zone
        )
        svc.submit_geometry(tool_b)

        # Evaluate instrument A -> CLEAR
        r_a = svc.evaluate("session-01", instrument_id="probe.a")
        assert r_a.worst_state == ProximityState.CLEAR

        # Evaluate instrument B -> likely BREACHED/WARNING
        r_b = svc.evaluate("session-01", instrument_id="probe.b")
        assert r_b.worst_state != ProximityState.CLEAR

        svc.stop()

    def test_rate_tracking_across_evaluations(self, mock_dispatcher, mock_registration_service, secret_filter, logger, runtime_context) -> None:
        """Rate-of-approach should be tracked across successive evaluations."""
        svc = ProximityService(mock_dispatcher, mock_registration_service, secret_filter, logger)
        svc.initialize(runtime_context)
        svc.start()

        zone = make_zone("zone-a", center=(100.0, 0.0, 0.0), radius=10.0, min_clearance=5.0)
        svc.bind_zones("session-01", (zone,), registration_error_mm=0.0, static_margin_mm=0.0)

        t0 = datetime(2025, 1, 1, 0, 0, 0, tzinfo=timezone.utc)

        # First eval: establishes baseline
        tool1 = make_tool_geometry(
            session_id="session-01", tip=(0.0, 0.0, 0.0),
            sequence_number=1, timestamp_utc=t0.isoformat(),
            tracking_uncertainty_mm=0.0,
        )
        svc.submit_geometry(tool1)
        r1 = svc.evaluate("session-01", now_utc=t0.isoformat())
        assert r1.zone_results[0].approach_rate_mm_s == 0.0  # No history yet

        # Second eval: 100ms later, moved closer
        t1 = t0 + timedelta(milliseconds=100)
        tool2 = make_tool_geometry(
            session_id="session-01", tip=(50.0, 0.0, 0.0),
            sequence_number=2, timestamp_utc=t1.isoformat(),
            tracking_uncertainty_mm=0.0,
        )
        svc.submit_geometry(tool2)
        r2 = svc.evaluate("session-01", now_utc=t1.isoformat())
        # Now rate should be non-zero (approaching)
        assert r2.zone_results[0].approach_rate_mm_s != 0.0

        svc.stop()

    def test_xr_meter_conversion_in_status(self, mock_dispatcher, mock_registration_service, secret_filter, logger, runtime_context) -> None:
        """XR presentation should convert mm to meters."""
        svc = ProximityService(mock_dispatcher, mock_registration_service, secret_filter, logger)
        svc.initialize(runtime_context)
        svc.start()

        zone = make_zone("zone-a", center=(100.0, 0.0, 0.0))
        svc.bind_zones("session-01", (zone,), registration_error_mm=1.0)

        tool = make_tool_geometry(
            session_id="session-01",
            tip=(1000.0, 0.0, 0.0),
            sequence_number=1,
        )
        svc.submit_geometry(tool)

        # Build a command envelope for proximity.evaluate
        from holomed.protocol.builders import create_command
        cmd = create_command(
            message_name="proximity.evaluate",
            source="test",
            target="proximity_service",
            payload={"session_id": "session-01"},
        )
        response = svc.handle_evaluate_command(cmd)
        # Response should include xr_presentation
        assert "xr_presentation" in response.payload
        tip_m = response.payload["xr_presentation"]["tip_position_m"]
        assert math.isclose(tip_m[0], 1.0, abs_tol=1e-6)

        svc.stop()

    def test_clear_and_rebind(self, mock_dispatcher, mock_registration_service, secret_filter, logger, runtime_context) -> None:
        """After clear(), service should accept new bindings."""
        svc = ProximityService(mock_dispatcher, mock_registration_service, secret_filter, logger)
        svc.initialize(runtime_context)
        svc.start()

        zone = make_zone("zone-a", center=(100.0, 0.0, 0.0))
        svc.bind_zones("session-01", (zone,), registration_error_mm=1.0)
        assert svc.active_sessions_count == 1

        svc.clear()
        assert svc.active_sessions_count == 0

        # Re-bind
        svc.bind_zones("session-02", (zone,), registration_error_mm=0.5)
        assert svc.active_sessions_count == 1

        svc.stop()
