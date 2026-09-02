# -*- coding: utf-8 -*-
"""Unit Tests for M15 Proximity Models — ToolClearanceGeometry, ZoneBreachRecord, ProximityState."""

from __future__ import annotations

import math
from datetime import datetime, timezone

import pytest

from holomed.proximity.constants import (
    DEFAULT_PATIENT_TRACKER_FRAME,
    MAX_COORDINATE_MM,
    MAX_SHAFT_LENGTH_MM,
    MAX_SHAFT_RADIUS_MM,
    MAX_TRACKING_UNCERTAINTY_MM,
    MIN_SHAFT_LENGTH_MM,
    MIN_SHAFT_RADIUS_MM,
)
from holomed.proximity.exceptions import ProximityValidationError
from holomed.proximity.models import (
    ProximityEvaluationRecord,
    ProximitySeverity,
    ProximityState,
    ProximityStatusRecord,
    ToolClearanceGeometry,
    ZoneBreachRecord,
    proximity_severity_to_interlock,
)
from holomed.workflow.models import InterlockSeverity

from tests.unit.proximity.conftest import make_tool_geometry


# ===========================================================================
# ProximityState Enum
# ===========================================================================

class TestProximityState:
    """Tests for ProximityState enum."""

    def test_all_states_exist(self) -> None:
        assert ProximityState.CLEAR.value == "CLEAR"
        assert ProximityState.WARNING.value == "WARNING"
        assert ProximityState.BREACHED.value == "BREACHED"
        assert ProximityState.INTERLOCKED.value == "INTERLOCKED"

    def test_state_count(self) -> None:
        assert len(ProximityState) == 4


# ===========================================================================
# ProximitySeverity Enum & Mapping
# ===========================================================================

class TestProximitySeverity:
    """Tests for ProximitySeverity enum and interlock mapping."""

    def test_severity_values(self) -> None:
        assert ProximitySeverity.WARNING.value == "WARNING"
        assert ProximitySeverity.BLOCKING.value == "BLOCKING"
        assert ProximitySeverity.CRITICAL.value == "CRITICAL"

    def test_severity_count(self) -> None:
        assert len(ProximitySeverity) == 3

    def test_warning_maps_to_warning(self) -> None:
        assert proximity_severity_to_interlock(ProximitySeverity.WARNING) == InterlockSeverity.WARNING

    def test_blocking_maps_to_blocking(self) -> None:
        assert proximity_severity_to_interlock(ProximitySeverity.BLOCKING) == InterlockSeverity.BLOCKING

    def test_critical_maps_to_critical(self) -> None:
        assert proximity_severity_to_interlock(ProximitySeverity.CRITICAL) == InterlockSeverity.CRITICAL


# ===========================================================================
# ToolClearanceGeometry Validation
# ===========================================================================

class TestToolClearanceGeometry:
    """Tests for ToolClearanceGeometry frozen dataclass validation."""

    def test_valid_construction(self) -> None:
        geom = make_tool_geometry()
        assert geom.instrument_id == "probe.01"
        assert geom.shaft_radius_mm == 1.0

    def test_immutable(self) -> None:
        geom = make_tool_geometry()
        with pytest.raises(AttributeError):
            geom.shaft_radius_mm = 5.0  # type: ignore

    def test_invalid_instrument_id_empty(self) -> None:
        with pytest.raises(ProximityValidationError, match="instrument_id"):
            make_tool_geometry(instrument_id="")

    def test_invalid_instrument_id_uppercase(self) -> None:
        with pytest.raises(ProximityValidationError, match="instrument_id"):
            make_tool_geometry(instrument_id="UPPER")

    def test_invalid_session_id_empty(self) -> None:
        with pytest.raises(ProximityValidationError, match="session_id"):
            make_tool_geometry(session_id="")

    def test_negative_epoch_id(self) -> None:
        with pytest.raises(ProximityValidationError, match="epoch_id"):
            make_tool_geometry(epoch_id=-1)

    def test_negative_sequence_number(self) -> None:
        with pytest.raises(ProximityValidationError, match="sequence_number"):
            make_tool_geometry(sequence_number=-1)

    def test_tip_position_nan(self) -> None:
        with pytest.raises(ProximityValidationError, match="tip_position"):
            make_tool_geometry(tip=(float("nan"), 0.0, 0.0))

    def test_tip_position_inf(self) -> None:
        with pytest.raises(ProximityValidationError, match="tip_position"):
            make_tool_geometry(tip=(float("inf"), 0.0, 0.0))

    def test_tip_position_exceeds_bound(self) -> None:
        with pytest.raises(ProximityValidationError, match="operational bound"):
            make_tool_geometry(tip=(MAX_COORDINATE_MM + 1.0, 0.0, 0.0))

    def test_shaft_direction_not_unit(self) -> None:
        with pytest.raises(ProximityValidationError, match="unit-normalized"):
            make_tool_geometry(direction=(2.0, 0.0, 0.0))

    def test_shaft_direction_nan(self) -> None:
        with pytest.raises(ProximityValidationError, match="finite"):
            make_tool_geometry(direction=(float("nan"), 0.0, 0.0))

    def test_shaft_length_below_min(self) -> None:
        with pytest.raises(ProximityValidationError, match="shaft_length"):
            make_tool_geometry(shaft_length=MIN_SHAFT_LENGTH_MM - 0.5)

    def test_shaft_length_above_max(self) -> None:
        with pytest.raises(ProximityValidationError, match="shaft_length"):
            make_tool_geometry(shaft_length=MAX_SHAFT_LENGTH_MM + 1.0)

    def test_shaft_radius_below_min(self) -> None:
        with pytest.raises(ProximityValidationError, match="shaft_radius"):
            make_tool_geometry(shaft_radius=MIN_SHAFT_RADIUS_MM - 0.1)

    def test_shaft_radius_above_max(self) -> None:
        with pytest.raises(ProximityValidationError, match="shaft_radius"):
            make_tool_geometry(shaft_radius=MAX_SHAFT_RADIUS_MM + 1.0)

    def test_confidence_out_of_range(self) -> None:
        with pytest.raises(ProximityValidationError, match="confidence"):
            make_tool_geometry(confidence=1.5)

    def test_confidence_negative(self) -> None:
        with pytest.raises(ProximityValidationError, match="confidence"):
            make_tool_geometry(confidence=-0.1)

    def test_tracking_uncertainty_negative(self) -> None:
        with pytest.raises(ProximityValidationError, match="tracking_uncertainty"):
            make_tool_geometry(tracking_uncertainty_mm=-1.0)

    def test_tracking_uncertainty_exceeds_max(self) -> None:
        with pytest.raises(ProximityValidationError, match="tracking_uncertainty"):
            make_tool_geometry(tracking_uncertainty_mm=MAX_TRACKING_UNCERTAINTY_MM + 1.0)

    def test_invalid_timestamp(self) -> None:
        with pytest.raises(ProximityValidationError, match="timestamp"):
            make_tool_geometry(timestamp_utc="not-a-date")

    def test_empty_coordinate_frame(self) -> None:
        with pytest.raises(ProximityValidationError, match="coordinate_frame"):
            ToolClearanceGeometry(
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
                coordinate_frame="",
            )


# ===========================================================================
# ZoneBreachRecord Validation
# ===========================================================================

class TestZoneBreachRecord:
    """Tests for ZoneBreachRecord frozen dataclass."""

    def test_valid_construction(self) -> None:
        now = datetime.now(timezone.utc).isoformat()
        rec = ZoneBreachRecord(
            zone_id="zone-a",
            anatomical_structure="optic_nerve",
            raw_clearance_mm=15.0,
            effective_radius_mm=12.0,
            effective_clearance_mm=3.0,
            severity=ProximitySeverity.WARNING,
            state=ProximityState.WARNING,
            approach_rate_mm_s=2.5,
            instrument_id="probe.01",
            evaluated_at_utc=now,
        )
        assert rec.zone_id == "zone-a"
        assert rec.raw_clearance_mm == 15.0

    def test_nan_clearance_rejected(self) -> None:
        now = datetime.now(timezone.utc).isoformat()
        with pytest.raises(ProximityValidationError, match="raw_clearance"):
            ZoneBreachRecord(
                zone_id="zone-a",
                anatomical_structure="optic_nerve",
                raw_clearance_mm=float("nan"),
                effective_radius_mm=12.0,
                effective_clearance_mm=3.0,
                severity=ProximitySeverity.WARNING,
                state=ProximityState.WARNING,
                approach_rate_mm_s=0.0,
                instrument_id="probe.01",
                evaluated_at_utc=now,
            )

    def test_empty_zone_id_rejected(self) -> None:
        now = datetime.now(timezone.utc).isoformat()
        with pytest.raises(ProximityValidationError, match="zone_id"):
            ZoneBreachRecord(
                zone_id="",
                anatomical_structure="optic_nerve",
                raw_clearance_mm=15.0,
                effective_radius_mm=12.0,
                effective_clearance_mm=3.0,
                severity=ProximitySeverity.WARNING,
                state=ProximityState.WARNING,
                approach_rate_mm_s=0.0,
                instrument_id="probe.01",
                evaluated_at_utc=now,
            )

    def test_empty_anatomical_structure_rejected(self) -> None:
        now = datetime.now(timezone.utc).isoformat()
        with pytest.raises(ProximityValidationError, match="anatomical_structure"):
            ZoneBreachRecord(
                zone_id="zone-a",
                anatomical_structure="",
                raw_clearance_mm=15.0,
                effective_radius_mm=12.0,
                effective_clearance_mm=3.0,
                severity=ProximitySeverity.WARNING,
                state=ProximityState.WARNING,
                approach_rate_mm_s=0.0,
                instrument_id="probe.01",
                evaluated_at_utc=now,
            )


# ===========================================================================
# ProximityEvaluationRecord Validation
# ===========================================================================

class TestProximityEvaluationRecord:
    """Tests for ProximityEvaluationRecord frozen dataclass."""

    def test_valid_construction(self) -> None:
        now = datetime.now(timezone.utc).isoformat()
        rec = ProximityEvaluationRecord(
            record_id="prox_abc123",
            session_id="test-session-01",
            instrument_id="probe.01",
            epoch_id=0,
            zone_results=(),
            worst_state=ProximityState.CLEAR,
            worst_severity=None,
            is_safe=True,
            evaluated_at_utc=now,
        )
        assert rec.is_safe is True
        assert rec.worst_state == ProximityState.CLEAR

    def test_invalid_record_id(self) -> None:
        now = datetime.now(timezone.utc).isoformat()
        with pytest.raises(ProximityValidationError, match="record_id"):
            ProximityEvaluationRecord(
                record_id="",
                session_id="test-session-01",
                instrument_id="probe.01",
                epoch_id=0,
                zone_results=(),
                worst_state=ProximityState.CLEAR,
                worst_severity=None,
                is_safe=True,
                evaluated_at_utc=now,
            )
