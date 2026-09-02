# -*- coding: utf-8 -*-
"""Unit Tests for M15 Proximity Evaluator — Multi-Zone, Rate, Severity, Interlock."""

from __future__ import annotations

import math
from datetime import datetime, timezone, timedelta

import pytest

from holomed.planning.models import SafetyExclusionZone
from holomed.proximity.constants import DEFAULT_PATIENT_TRACKER_FRAME
from holomed.proximity.evaluator import ProximityEvaluator
from holomed.proximity.exceptions import ProximityValidationError
from holomed.proximity.models import (
    ProximitySeverity,
    ProximityState,
    ToolClearanceGeometry,
)
from holomed.workflow.models import InterlockSeverity

from tests.unit.proximity.conftest import make_tool_geometry, make_zone


# ===========================================================================
# Single-Zone Evaluations
# ===========================================================================

class TestSingleZoneEvaluation:
    """Tests for ProximityEvaluator.evaluate() with a single zone."""

    def test_clear_state_when_far(self) -> None:
        """Tool far from zone -> CLEAR."""
        tool = make_tool_geometry(tip=(0.0, 0.0, 0.0))
        zone = make_zone("zone-a", center=(100.0, 0.0, 0.0), radius=10.0, min_clearance=5.0)

        result = ProximityEvaluator.evaluate(
            tool_geom=tool, zones=(zone,), registration_error_mm=1.0, static_margin_mm=0.0,
        )
        assert result.worst_state == ProximityState.CLEAR
        assert result.is_safe is True
        assert len(result.zone_results) == 1
        assert result.zone_results[0].state == ProximityState.CLEAR

    def test_warning_state_when_close(self) -> None:
        """Tool within min_clearance band -> WARNING."""
        # Zone at (20, 0, 0), radius=10, min_clearance=5
        # R_eff = 10 + 0 + 1 + 2*0.5 = 12
        # Tip at (14, 0, 0), shaft along +Z -> closest = tip
        # eff_clearance = dist(14, 20) - 12 - 1 = 6 - 12 - 1 = -7 ... that's breached
        # Let me recalculate:
        # Actually capsule_to_sphere uses bounding_radius in the raw clearance
        # and the evaluator uses R_effective in the effective clearance

        # Let's set up carefully:
        # Zone center=(25, 0, 0), bounding_radius=5, min_clearance=5
        # R_eff = 5 + 0 + 1 + 2*0.5 = 7
        # Tool tip at (14, 0, 0), shaft_radius=1, shaft along +Z
        # Distance from tip to center = 11
        # effective_clearance = 11 - 7 - 1 = 3 (shaft_radius subtracted in capsule calc)
        # min_clearance = 5 -> 3 < 5 -> WARNING
        zone = make_zone("zone-a", center=(25.0, 0.0, 0.0), radius=5.0, min_clearance=5.0)
        tool = make_tool_geometry(tip=(14.0, 0.0, 0.0), shaft_radius=1.0, tracking_uncertainty_mm=0.5)

        result = ProximityEvaluator.evaluate(
            tool_geom=tool, zones=(zone,), registration_error_mm=1.0, static_margin_mm=0.0,
        )
        assert result.worst_state == ProximityState.WARNING
        assert result.is_safe is False

    def test_breached_state_when_inside(self) -> None:
        """Tool inside effective radius -> BREACHED."""
        # Zone center=(12, 0, 0), bounding_radius=10, min_clearance=5
        # R_eff = 10 + 0 + 1 + 2*0.5 = 12
        # Tool tip at (0, 0, 0), shaft_radius=1
        # distance from tip to center = 12
        # effective_clearance = 12 - 12 - 1 = -1 -> BREACHED
        zone = make_zone("zone-a", center=(12.0, 0.0, 0.0), radius=10.0, min_clearance=5.0)
        tool = make_tool_geometry(tip=(0.0, 0.0, 0.0), shaft_radius=1.0, tracking_uncertainty_mm=0.5)

        result = ProximityEvaluator.evaluate(
            tool_geom=tool, zones=(zone,), registration_error_mm=1.0, static_margin_mm=0.0,
        )
        assert result.worst_state == ProximityState.BREACHED
        assert result.is_safe is False

    def test_zero_registration_error(self) -> None:
        """Zero registration error with clear result."""
        tool = make_tool_geometry(tip=(0.0, 0.0, 0.0), tracking_uncertainty_mm=0.0)
        zone = make_zone("zone-a", center=(100.0, 0.0, 0.0), radius=10.0, min_clearance=5.0)

        result = ProximityEvaluator.evaluate(
            tool_geom=tool, zones=(zone,), registration_error_mm=0.0, static_margin_mm=0.0,
        )
        assert result.worst_state == ProximityState.CLEAR
        assert result.is_safe is True


# ===========================================================================
# Multi-Zone Evaluation & Worst-Severity Aggregation
# ===========================================================================

class TestMultiZoneEvaluation:
    """Tests for multi-zone evaluation with worst-severity aggregation."""

    def test_all_clear(self) -> None:
        tool = make_tool_geometry(tip=(0.0, 0.0, 0.0))
        zones = (
            make_zone("zone-a", center=(100.0, 0.0, 0.0)),
            make_zone("zone-b", center=(0.0, 100.0, 0.0)),
            make_zone("zone-c", center=(0.0, 0.0, -100.0)),
        )
        result = ProximityEvaluator.evaluate(
            tool_geom=tool, zones=zones, registration_error_mm=1.0, static_margin_mm=0.0,
        )
        assert result.worst_state == ProximityState.CLEAR
        assert result.is_safe is True
        assert len(result.zone_results) == 3

    def test_worst_severity_propagated(self) -> None:
        """When one zone is breached with CRITICAL severity, worst = CRITICAL."""
        tool = make_tool_geometry(tip=(0.0, 0.0, 0.0), tracking_uncertainty_mm=0.0)
        zones = (
            # Far zone -> CLEAR
            make_zone("zone-a", center=(100.0, 0.0, 0.0), radius=5.0, severity=InterlockSeverity.WARNING),
            # Close zone -> BREACHED with CRITICAL
            make_zone("zone-b", center=(5.0, 0.0, 0.0), radius=10.0, severity=InterlockSeverity.CRITICAL),
        )
        result = ProximityEvaluator.evaluate(
            tool_geom=tool, zones=zones, registration_error_mm=0.0, static_margin_mm=0.0,
        )
        assert result.worst_state == ProximityState.BREACHED
        assert result.worst_severity == ProximitySeverity.CRITICAL
        assert result.is_safe is False

    def test_empty_zones_returns_clear(self) -> None:
        """No zones -> CLEAR."""
        tool = make_tool_geometry()
        result = ProximityEvaluator.evaluate(
            tool_geom=tool, zones=(), registration_error_mm=0.0, static_margin_mm=0.0,
        )
        assert result.worst_state == ProximityState.CLEAR
        assert result.is_safe is True
        assert len(result.zone_results) == 0


# ===========================================================================
# Rate-of-Approach Calculation
# ===========================================================================

class TestRateOfApproach:
    """Tests for approach rate calculation with timestamp rules."""

    def test_approach_rate_computed(self) -> None:
        t0 = datetime(2025, 1, 1, 0, 0, 0, tzinfo=timezone.utc)
        t1 = t0 + timedelta(milliseconds=100)

        tool = make_tool_geometry(tip=(70.0, 0.0, 0.0), timestamp_utc=t1.isoformat())
        zone = make_zone("zone-a", center=(100.0, 0.0, 0.0), radius=10.0, min_clearance=5.0)

        # Previous clearance was larger (farther away), now closer
        prev_clearances = {"zone-a": (20.0, t0.isoformat())}

        result = ProximityEvaluator.evaluate(
            tool_geom=tool, zones=(zone,),
            registration_error_mm=0.0, static_margin_mm=0.0,
            previous_clearances=prev_clearances,
            now_utc=t1.isoformat(),
        )
        # The rate should be computed based on delta
        assert len(result.zone_results) == 1
        # Rate is computable and finite
        assert math.isfinite(result.zone_results[0].approach_rate_mm_s)

    def test_rate_reset_on_large_gap(self) -> None:
        """Delta time > 0.5s -> rate reset to 0.0."""
        t0 = datetime(2025, 1, 1, 0, 0, 0, tzinfo=timezone.utc)
        t1 = t0 + timedelta(seconds=1.0)  # 1 second > 0.5s threshold

        tool = make_tool_geometry(tip=(70.0, 0.0, 0.0), timestamp_utc=t1.isoformat())
        zone = make_zone("zone-a", center=(100.0, 0.0, 0.0), radius=10.0, min_clearance=5.0)

        prev_clearances = {"zone-a": (20.0, t0.isoformat())}

        result = ProximityEvaluator.evaluate(
            tool_geom=tool, zones=(zone,),
            registration_error_mm=0.0, static_margin_mm=0.0,
            previous_clearances=prev_clearances,
            now_utc=t1.isoformat(),
        )
        # Rate should be reset to 0.0
        assert result.zone_results[0].approach_rate_mm_s == 0.0

    def test_rate_zero_on_negative_delta_time(self) -> None:
        """Delta time <= 0 -> rate stays 0.0."""
        t0 = datetime(2025, 1, 1, 0, 0, 0, tzinfo=timezone.utc)
        t1 = t0 - timedelta(milliseconds=100)  # Backwards

        tool = make_tool_geometry(tip=(70.0, 0.0, 0.0), timestamp_utc=t1.isoformat())
        zone = make_zone("zone-a", center=(100.0, 0.0, 0.0), radius=10.0, min_clearance=5.0)

        prev_clearances = {"zone-a": (20.0, t0.isoformat())}

        result = ProximityEvaluator.evaluate(
            tool_geom=tool, zones=(zone,),
            registration_error_mm=0.0, static_margin_mm=0.0,
            previous_clearances=prev_clearances,
            now_utc=t1.isoformat(),
        )
        assert result.zone_results[0].approach_rate_mm_s == 0.0

    def test_rate_zero_without_history(self) -> None:
        """No previous clearances -> rate is 0.0."""
        tool = make_tool_geometry(tip=(70.0, 0.0, 0.0))
        zone = make_zone("zone-a", center=(100.0, 0.0, 0.0), radius=10.0, min_clearance=5.0)

        result = ProximityEvaluator.evaluate(
            tool_geom=tool, zones=(zone,),
            registration_error_mm=0.0, static_margin_mm=0.0,
        )
        assert result.zone_results[0].approach_rate_mm_s == 0.0


# ===========================================================================
# Effective Radius Integration
# ===========================================================================

class TestEffectiveRadiusIntegration:
    """Tests for R_effective = R_zone + static_margin + reg_error + 2*tracking_unc."""

    def test_effective_radius_widens_zone(self) -> None:
        """Larger effective radius should cause breach at greater distance."""
        tool = make_tool_geometry(tip=(0.0, 0.0, 0.0), shaft_radius=1.0, tracking_uncertainty_mm=5.0)
        zone = make_zone("zone-a", center=(25.0, 0.0, 0.0), radius=10.0, min_clearance=3.0)

        # R_eff = 10 + 5 + 3 + 2*5 = 28
        # Distance = 25, capsule clearance = 25 - 28 - 1 = -4 -> BREACHED
        result = ProximityEvaluator.evaluate(
            tool_geom=tool, zones=(zone,),
            registration_error_mm=3.0, static_margin_mm=5.0,
        )
        assert result.worst_state == ProximityState.BREACHED
        assert result.is_safe is False


# ===========================================================================
# Interlock Generation
# ===========================================================================

class TestInterlockGeneration:
    """Tests for M10 SafetyInterlock generation from evaluation results."""

    def test_no_interlock_when_safe(self) -> None:
        tool = make_tool_geometry(tip=(0.0, 0.0, 0.0))
        zone = make_zone("zone-a", center=(100.0, 0.0, 0.0))

        result = ProximityEvaluator.evaluate(
            tool_geom=tool, zones=(zone,), registration_error_mm=0.0, static_margin_mm=0.0,
        )
        interlock = ProximityEvaluator.generate_interlock(result, "test-session-01", 0)
        assert interlock is None

    def test_interlock_generated_on_breach(self) -> None:
        tool = make_tool_geometry(tip=(0.0, 0.0, 0.0), tracking_uncertainty_mm=0.0)
        zone = make_zone("zone-a", center=(5.0, 0.0, 0.0), radius=10.0, severity=InterlockSeverity.CRITICAL)

        result = ProximityEvaluator.evaluate(
            tool_geom=tool, zones=(zone,), registration_error_mm=0.0, static_margin_mm=0.0,
        )
        interlock = ProximityEvaluator.generate_interlock(result, "test-session-01", 0)
        assert interlock is not None
        assert interlock.status is False  # Tripped
        assert interlock.severity == InterlockSeverity.CRITICAL
        assert interlock.source_service == "proximity_service"
        assert interlock.condition_name == "EXCLUSION_ZONE_BREACHED"

    def test_interlock_severity_matches_worst(self) -> None:
        tool = make_tool_geometry(tip=(0.0, 0.0, 0.0), tracking_uncertainty_mm=0.0)
        zones = (
            make_zone("zone-a", center=(5.0, 0.0, 0.0), radius=10.0, severity=InterlockSeverity.WARNING),
            make_zone("zone-b", center=(5.0, 0.0, 0.0), radius=10.0, severity=InterlockSeverity.BLOCKING),
        )

        result = ProximityEvaluator.evaluate(
            tool_geom=tool, zones=zones, registration_error_mm=0.0, static_margin_mm=0.0,
        )
        interlock = ProximityEvaluator.generate_interlock(result, "test-session-01", 0)
        assert interlock is not None
        assert interlock.severity == InterlockSeverity.BLOCKING


# ===========================================================================
# Coordinate Frame Validation
# ===========================================================================

class TestCoordinateFrameValidation:
    """Tests for coordinate frame checks in evaluator."""

    def test_wrong_frame_rejected(self) -> None:
        tool = ToolClearanceGeometry(
            instrument_id="probe.01",
            session_id="test-session-01",
            epoch_id=0,
            sequence_number=1,
            tip_position_mm=(0.0, 0.0, 0.0),
            shaft_direction=(0.0, 0.0, 1.0),
            shaft_length_mm=100.0,
            shaft_radius_mm=1.0,
            confidence=1.0,
            tracking_uncertainty_mm=0.5,
            timestamp_utc=datetime.now(timezone.utc).isoformat(),
            coordinate_frame="wrong_frame",
        )
        zone = make_zone("zone-a", center=(100.0, 0.0, 0.0))

        with pytest.raises(ProximityValidationError, match="coordinate_frame"):
            ProximityEvaluator.evaluate(
                tool_geom=tool, zones=(zone,), registration_error_mm=0.0, static_margin_mm=0.0,
            )


# ===========================================================================
# Input Validation
# ===========================================================================

class TestEvaluatorInputValidation:
    """Tests for parameter validation in evaluator."""

    def test_negative_registration_error_rejected(self) -> None:
        tool = make_tool_geometry()
        zone = make_zone("zone-a", center=(100.0, 0.0, 0.0))

        with pytest.raises(ProximityValidationError, match="registration_error"):
            ProximityEvaluator.evaluate(
                tool_geom=tool, zones=(zone,), registration_error_mm=-1.0, static_margin_mm=0.0,
            )

    def test_negative_static_margin_rejected(self) -> None:
        tool = make_tool_geometry()
        zone = make_zone("zone-a", center=(100.0, 0.0, 0.0))

        with pytest.raises(ProximityValidationError, match="static_margin"):
            ProximityEvaluator.evaluate(
                tool_geom=tool, zones=(zone,), registration_error_mm=0.0, static_margin_mm=-1.0,
            )

    def test_nan_registration_error_rejected(self) -> None:
        tool = make_tool_geometry()
        zone = make_zone("zone-a", center=(100.0, 0.0, 0.0))

        with pytest.raises(ProximityValidationError, match="registration_error"):
            ProximityEvaluator.evaluate(
                tool_geom=tool, zones=(zone,), registration_error_mm=float("nan"), static_margin_mm=0.0,
            )
