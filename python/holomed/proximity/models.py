# -*- coding: utf-8 -*-
"""Strongly Typed Data Models for M15 Proximity Monitoring Subsystem."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum
import math
from typing import Optional, Tuple

from holomed.proximity.constants import (
    INSTRUMENT_ID_REGEX,
    MAX_CLEARANCE_MM,
    MAX_COORDINATE_MM,
    MAX_REGISTRATION_ERROR_MM,
    MAX_SHAFT_LENGTH_MM,
    MAX_SHAFT_RADIUS_MM,
    MAX_STATIC_MARGIN_MM,
    MAX_TRACKING_UNCERTAINTY_MM,
    MAX_ZONE_RADIUS_MM,
    MIN_CLEARANCE_MM,
    MIN_SHAFT_LENGTH_MM,
    MIN_SHAFT_RADIUS_MM,
    MIN_ZONE_RADIUS_MM,
    SESSION_ID_REGEX,
    ZONE_ID_REGEX,
)
from holomed.proximity.exceptions import ProximityValidationError
from holomed.workflow.models import InterlockSeverity


class ProximityState(str, Enum):
    """Proximity evaluation lifecycle state."""

    CLEAR = "CLEAR"
    WARNING = "WARNING"
    BREACHED = "BREACHED"
    INTERLOCKED = "INTERLOCKED"


class ProximitySeverity(str, Enum):
    """Proximity-specific severity classification for zone violations.

    Maps to M10 InterlockSeverity:
        WARNING   -> InterlockSeverity.WARNING
        BLOCKING  -> InterlockSeverity.BLOCKING
        CRITICAL  -> InterlockSeverity.CRITICAL
    """

    WARNING = "WARNING"
    BLOCKING = "BLOCKING"
    CRITICAL = "CRITICAL"


def proximity_severity_to_interlock(severity: ProximitySeverity) -> InterlockSeverity:
    """Map ProximitySeverity to frozen M10 InterlockSeverity."""
    _mapping = {
        ProximitySeverity.WARNING: InterlockSeverity.WARNING,
        ProximitySeverity.BLOCKING: InterlockSeverity.BLOCKING,
        ProximitySeverity.CRITICAL: InterlockSeverity.CRITICAL,
    }
    return _mapping[severity]


@dataclass(frozen=True)
class ToolClearanceGeometry:
    """Immutable instrument shaft capsule geometry for proximity evaluation.

    The shaft is modelled as a finite line segment from tip_position_mm
    along shaft_direction (unit vector) for shaft_length_mm, dilated
    by shaft_radius_mm to form a capsule.
    """

    instrument_id: str
    session_id: str
    epoch_id: int
    sequence_number: int
    tip_position_mm: Tuple[float, float, float]
    shaft_direction: Tuple[float, float, float]  # unit vector, tip to tail
    shaft_length_mm: float
    shaft_radius_mm: float
    confidence: float
    tracking_uncertainty_mm: float
    timestamp_utc: str
    coordinate_frame: str

    def __post_init__(self) -> None:
        if not isinstance(self.instrument_id, str) or not INSTRUMENT_ID_REGEX.match(self.instrument_id):
            raise ProximityValidationError(f"Invalid instrument_id syntax: {self.instrument_id!r}")
        if not isinstance(self.session_id, str) or not SESSION_ID_REGEX.match(self.session_id):
            raise ProximityValidationError(f"Invalid session_id syntax: {self.session_id!r}")
        if not isinstance(self.epoch_id, int) or self.epoch_id < 0:
            raise ProximityValidationError("epoch_id must be non-negative int")
        if not isinstance(self.sequence_number, int) or self.sequence_number < 0:
            raise ProximityValidationError("sequence_number must be non-negative int")

        # Tip position validation
        if not isinstance(self.tip_position_mm, (tuple, list)) or len(self.tip_position_mm) != 3:
            raise ProximityValidationError("tip_position_mm must be a 3-element tuple of floats")
        for val in self.tip_position_mm:
            if not isinstance(val, (int, float)) or not math.isfinite(val):
                raise ProximityValidationError("tip_position_mm coordinates must be finite floats")
            if abs(val) > MAX_COORDINATE_MM:
                raise ProximityValidationError(
                    f"tip_position_mm coordinate ({val:.1f} mm) exceeds operational bound ({MAX_COORDINATE_MM} mm)"
                )

        # Shaft direction validation (unit vector)
        if not isinstance(self.shaft_direction, (tuple, list)) or len(self.shaft_direction) != 3:
            raise ProximityValidationError("shaft_direction must be a 3-element tuple of floats")
        for val in self.shaft_direction:
            if not isinstance(val, (int, float)) or not math.isfinite(val):
                raise ProximityValidationError("shaft_direction elements must be finite floats")

        dx, dy, dz = self.shaft_direction
        norm_sq = dx * dx + dy * dy + dz * dz
        if abs(norm_sq - 1.0) > 1e-4:
            raise ProximityValidationError(
                f"shaft_direction must be unit-normalized (norm^2 = {norm_sq:.6f})"
            )

        # Shaft geometry validation
        if not isinstance(self.shaft_length_mm, (int, float)) or not math.isfinite(self.shaft_length_mm):
            raise ProximityValidationError("shaft_length_mm must be finite float")
        if self.shaft_length_mm < MIN_SHAFT_LENGTH_MM or self.shaft_length_mm > MAX_SHAFT_LENGTH_MM:
            raise ProximityValidationError(
                f"shaft_length_mm ({self.shaft_length_mm:.1f}) must be in [{MIN_SHAFT_LENGTH_MM}, {MAX_SHAFT_LENGTH_MM}]"
            )

        if not isinstance(self.shaft_radius_mm, (int, float)) or not math.isfinite(self.shaft_radius_mm):
            raise ProximityValidationError("shaft_radius_mm must be finite float")
        if self.shaft_radius_mm < MIN_SHAFT_RADIUS_MM or self.shaft_radius_mm > MAX_SHAFT_RADIUS_MM:
            raise ProximityValidationError(
                f"shaft_radius_mm ({self.shaft_radius_mm:.2f}) must be in [{MIN_SHAFT_RADIUS_MM}, {MAX_SHAFT_RADIUS_MM}]"
            )

        # Confidence and tracking uncertainty
        if not isinstance(self.confidence, (int, float)) or not (0.0 <= self.confidence <= 1.0) or not math.isfinite(self.confidence):
            raise ProximityValidationError("confidence must be finite float in [0.0, 1.0]")
        if not isinstance(self.tracking_uncertainty_mm, (int, float)) or not math.isfinite(self.tracking_uncertainty_mm):
            raise ProximityValidationError("tracking_uncertainty_mm must be finite float")
        if self.tracking_uncertainty_mm < 0.0 or self.tracking_uncertainty_mm > MAX_TRACKING_UNCERTAINTY_MM:
            raise ProximityValidationError(
                f"tracking_uncertainty_mm ({self.tracking_uncertainty_mm:.2f}) must be in [0.0, {MAX_TRACKING_UNCERTAINTY_MM}]"
            )

        # Timestamp validation
        if not isinstance(self.timestamp_utc, str) or not self.timestamp_utc.strip():
            raise ProximityValidationError("timestamp_utc must be non-empty string")
        try:
            ts = self.timestamp_utc.replace("Z", "+00:00")
            datetime.fromisoformat(ts)
        except Exception as e:
            raise ProximityValidationError(f"Invalid timestamp_utc ISO-8601 format: {self.timestamp_utc!r}") from e

        # Coordinate frame
        if not isinstance(self.coordinate_frame, str) or not self.coordinate_frame.strip():
            raise ProximityValidationError("coordinate_frame must be non-empty string")


@dataclass(frozen=True)
class ZoneBreachRecord:
    """Immutable record of a single exclusion zone proximity evaluation."""

    zone_id: str
    anatomical_structure: str
    raw_clearance_mm: float
    effective_radius_mm: float
    effective_clearance_mm: float
    severity: ProximitySeverity
    state: ProximityState
    approach_rate_mm_s: float
    instrument_id: str
    evaluated_at_utc: str

    def __post_init__(self) -> None:
        if not isinstance(self.zone_id, str) or not self.zone_id.strip():
            raise ProximityValidationError("zone_id must be non-empty string")
        if not isinstance(self.anatomical_structure, str) or not self.anatomical_structure.strip():
            raise ProximityValidationError("anatomical_structure must be non-empty string")
        for name, val in (
            ("raw_clearance_mm", self.raw_clearance_mm),
            ("effective_radius_mm", self.effective_radius_mm),
            ("effective_clearance_mm", self.effective_clearance_mm),
            ("approach_rate_mm_s", self.approach_rate_mm_s),
        ):
            if not isinstance(val, (int, float)) or not math.isfinite(val):
                raise ProximityValidationError(f"{name} must be finite float, got {val!r}")
        if not isinstance(self.severity, ProximitySeverity):
            raise ProximityValidationError(f"Invalid severity: {self.severity}")
        if not isinstance(self.state, ProximityState):
            raise ProximityValidationError(f"Invalid state: {self.state}")


@dataclass(frozen=True)
class ProximityEvaluationRecord:
    """Immutable aggregate proximity evaluation record across all monitored zones."""

    record_id: str
    session_id: str
    instrument_id: str
    epoch_id: int
    zone_results: Tuple[ZoneBreachRecord, ...]
    worst_state: ProximityState
    worst_severity: Optional[ProximitySeverity]
    is_safe: bool
    evaluated_at_utc: str

    def __post_init__(self) -> None:
        if not isinstance(self.record_id, str) or not self.record_id.strip():
            raise ProximityValidationError("record_id must be non-empty string")
        if not isinstance(self.worst_state, ProximityState):
            raise ProximityValidationError(f"Invalid worst_state: {self.worst_state}")


@dataclass(frozen=True)
class ProximityStatusRecord:
    """Active proximity monitoring status snapshot."""

    session_id: str
    epoch_id: int
    state: ProximityState
    monitored_zone_count: int
    active_instrument_id: Optional[str]
    last_evaluation: Optional[ProximityEvaluationRecord]
    is_monitoring_active: bool
    updated_at_utc: str
