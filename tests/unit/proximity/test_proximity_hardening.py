# -*- coding: utf-8 -*-
"""Hardening Tests for M15 Proximity Monitoring — Edge Cases, Determinism, Security."""

from __future__ import annotations

import math
from datetime import datetime, timezone, timedelta
from unittest.mock import MagicMock

import pytest

from holomed.planning.models import SafetyExclusionZone
from holomed.proximity.constants import (
    DEFAULT_PATIENT_TRACKER_FRAME,
    MAX_COORDINATE_MM,
)
from holomed.proximity.evaluator import ProximityEvaluator
from holomed.proximity.exceptions import (
    ProximityLifecycleError,
    ProximityValidationError,
)
from holomed.proximity.geometry import (
    capsule_to_sphere_clearance,
    compute_effective_radius,
    point_to_sphere_clearance,
)
from holomed.proximity.models import (
    ProximitySeverity,
    ProximityState,
    ToolClearanceGeometry,
)
from holomed.proximity.service import ProximityService
from holomed.runtime.service import ServiceState
from holomed.workflow.models import InterlockSeverity

from tests.unit.proximity.conftest import (
    make_tool_geometry,
    make_zone,
)


# ===========================================================================
# Geometric Edge Cases
# ===========================================================================

class TestGeometricEdgeCases:
    """Edge case tests for proximity geometry computations."""

    def test_point_at_sphere_center(self) -> None:
        """Point coincident with sphere center -> negative clearance."""
        clearance = point_to_sphere_clearance((0.0, 0.0, 0.0), (0.0, 0.0, 0.0), 10.0)
        assert clearance == -10.0

    def test_capsule_passing_through_sphere(self) -> None:
        """Shaft segment passes through sphere center."""
        clearance = capsule_to_sphere_clearance(
            tip_position=(0.0, 0.0, -50.0),
            shaft_direction=(0.0, 0.0, 1.0),
            shaft_length=100.0,
            shaft_radius=1.0,
            sphere_center=(0.0, 0.0, 0.0),
            sphere_radius=5.0,
        )
        # Closest point on segment = (0,0,0), dist = 0
        # clearance = 0 - 5 - 1 = -6
        assert math.isclose(clearance, -6.0, abs_tol=1e-9)

    def test_capsule_perpendicular_to_sphere(self) -> None:
        """Shaft perpendicular to line from tip to sphere center."""
        clearance = capsule_to_sphere_clearance(
            tip_position=(0.0, 0.0, 0.0),
            shaft_direction=(0.0, 1.0, 0.0),  # Y-axis
            shaft_length=100.0,
            shaft_radius=1.0,
            sphere_center=(20.0, 0.0, 0.0),  # Along X
            sphere_radius=5.0,
        )
        # Closest point on segment to (20,0,0) is (0,0,0)
        # Distance = 20, clearance = 20 - 5 - 1 = 14
        assert math.isclose(clearance, 14.0, abs_tol=1e-9)

    def test_very_small_clearance(self) -> None:
        """Clearance at machine epsilon level should be finite."""
        clearance = point_to_sphere_clearance((10.0 + 1e-12, 0.0, 0.0), (0.0, 0.0, 0.0), 10.0)
        assert math.isfinite(clearance)
        assert clearance > 0.0

    def test_large_coordinates(self) -> None:
        """Geometry at operational bounds should compute correctly."""
        clearance = point_to_sphere_clearance(
            (MAX_COORDINATE_MM, 0.0, 0.0),
            (0.0, 0.0, 0.0),
            10.0,
        )
        assert math.isclose(clearance, MAX_COORDINATE_MM - 10.0, abs_tol=1e-6)


# ===========================================================================
# Determinism Tests
# ===========================================================================

class TestDeterminism:
    """Tests that repeated evaluations produce identical geometric results."""

    def test_repeated_evaluation_deterministic(self) -> None:
        """Same inputs must produce identical clearance results."""
        now = datetime(2025, 6, 1, 12, 0, 0, tzinfo=timezone.utc).isoformat()
        tool = make_tool_geometry(tip=(50.0, 0.0, 0.0), timestamp_utc=now)
        zone = make_zone("zone-a", center=(100.0, 0.0, 0.0), radius=10.0, min_clearance=5.0)

        results = []
        for _ in range(10):
            result = ProximityEvaluator.evaluate(
                tool_geom=tool, zones=(zone,),
                registration_error_mm=1.0, static_margin_mm=0.0,
                now_utc=now,
            )
            results.append(result)

        # All zone results should have identical clearances
        for r in results[1:]:
            assert r.zone_results[0].raw_clearance_mm == results[0].zone_results[0].raw_clearance_mm
            assert r.zone_results[0].effective_clearance_mm == results[0].zone_results[0].effective_clearance_mm
            assert r.zone_results[0].state == results[0].zone_results[0].state
            assert r.worst_state == results[0].worst_state
            assert r.is_safe == results[0].is_safe

    def test_geometry_pure_function(self) -> None:
        """Geometry functions are pure — no side effects."""
        for _ in range(5):
            c1 = point_to_sphere_clearance((20.0, 0.0, 0.0), (0.0, 0.0, 0.0), 10.0)
            assert math.isclose(c1, 10.0, abs_tol=1e-9)

        for _ in range(5):
            r = compute_effective_radius(10.0, 2.0, 1.0, 0.5)
            assert math.isclose(r, 14.0, abs_tol=1e-9)


# ===========================================================================
# Fail-Closed Behavior
# ===========================================================================

class TestFailClosed:
    """Tests for fail-closed safety behavior."""

    def test_nan_effective_radius_params_rejected(self) -> None:
        """NaN in effective radius -> validation error, not silent pass."""
        with pytest.raises(ProximityValidationError):
            compute_effective_radius(float("nan"), 0.0, 0.0, 0.0)

    def test_inf_effective_radius_params_rejected(self) -> None:
        with pytest.raises(ProximityValidationError):
            compute_effective_radius(float("inf"), 0.0, 0.0, 0.0)

    def test_wrong_frame_fails_closed(self) -> None:
        """Wrong coordinate frame -> validation error, not silent evaluation."""
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
            coordinate_frame="alien_frame",
        )
        zone = make_zone("zone-a", center=(100.0, 0.0, 0.0))

        with pytest.raises(ProximityValidationError, match="coordinate_frame"):
            ProximityEvaluator.evaluate(
                tool_geom=tool, zones=(zone,), registration_error_mm=0.0, static_margin_mm=0.0,
            )


# ===========================================================================
# Reentrancy Protection
# ===========================================================================

class TestReentrancy:
    """Tests for reentrancy guard."""

    def test_reentrancy_guard_prevents_concurrent_bind(
        self, mock_dispatcher, mock_registration_service, secret_filter, logger, runtime_context
    ) -> None:
        svc = ProximityService(mock_dispatcher, mock_registration_service, secret_filter, logger)
        svc.initialize(runtime_context)
        svc.start()

        # Simulate reentrancy by setting the flag directly
        svc._in_transaction = True
        zone = make_zone("zone-a", center=(100.0, 0.0, 0.0))
        with pytest.raises(ProximityLifecycleError, match="Reentrant"):
            svc.bind_zones("test-session-01", (zone,), registration_error_mm=1.0)
        svc._in_transaction = False

    def test_reentrancy_guard_prevents_concurrent_submit(
        self, mock_dispatcher, mock_registration_service, secret_filter, logger, runtime_context
    ) -> None:
        svc = ProximityService(mock_dispatcher, mock_registration_service, secret_filter, logger)
        svc.initialize(runtime_context)
        svc.start()

        svc._in_transaction = True
        tool = make_tool_geometry()
        with pytest.raises(ProximityLifecycleError, match="Reentrant"):
            svc.submit_geometry(tool)
        svc._in_transaction = False

    def test_reentrancy_guard_prevents_concurrent_evaluate(
        self, mock_dispatcher, mock_registration_service, secret_filter, logger, runtime_context
    ) -> None:
        svc = ProximityService(mock_dispatcher, mock_registration_service, secret_filter, logger)
        svc.initialize(runtime_context)
        svc.start()

        svc._in_transaction = True
        with pytest.raises(ProximityLifecycleError, match="Reentrant"):
            svc.evaluate("test-session-01")
        svc._in_transaction = False

    def test_reentrancy_guard_prevents_stop_during_transaction(
        self, mock_dispatcher, mock_registration_service, secret_filter, logger, runtime_context
    ) -> None:
        svc = ProximityService(mock_dispatcher, mock_registration_service, secret_filter, logger)
        svc.initialize(runtime_context)
        svc.start()

        svc._in_transaction = True
        with pytest.raises(ProximityLifecycleError, match="Cannot stop"):
            svc.stop()
        svc._in_transaction = False


# ===========================================================================
# Capsule Model Correctness
# ===========================================================================

class TestCapsuleModelCorrectness:
    """Verify capsule model mathematical properties."""

    def test_capsule_symmetry(self) -> None:
        """Capsule clearance should be the same when sphere is equidistant from both endpoints."""
        c1 = capsule_to_sphere_clearance(
            tip_position=(0.0, 0.0, 0.0),
            shaft_direction=(0.0, 0.0, 1.0),
            shaft_length=100.0,
            shaft_radius=1.0,
            sphere_center=(20.0, 0.0, 50.0),  # Mid-shaft, 20mm lateral
            sphere_radius=5.0,
        )
        # Closest = (0,0,50), distance = 20
        assert math.isclose(c1, 14.0, abs_tol=1e-9)

    def test_capsule_clearance_decreases_with_larger_shaft_radius(self) -> None:
        base_params = dict(
            tip_position=(0.0, 0.0, 0.0),
            shaft_direction=(0.0, 0.0, 1.0),
            shaft_length=100.0,
            sphere_center=(20.0, 0.0, 0.0),
            sphere_radius=5.0,
        )
        c1 = capsule_to_sphere_clearance(**base_params, shaft_radius=1.0)
        c2 = capsule_to_sphere_clearance(**base_params, shaft_radius=5.0)
        assert c1 > c2

    def test_capsule_degenerates_to_point(self) -> None:
        """When shaft_length=0, capsule degenerates to sphere around tip."""
        c = capsule_to_sphere_clearance(
            tip_position=(20.0, 0.0, 0.0),
            shaft_direction=(0.0, 0.0, 1.0),
            shaft_length=0.0,
            shaft_radius=2.0,
            sphere_center=(0.0, 0.0, 0.0),
            sphere_radius=5.0,
        )
        # Distance = 20, clearance = 20 - 5 - 2 = 13
        assert math.isclose(c, 13.0, abs_tol=1e-9)
