# -*- coding: utf-8 -*-
"""Strongly Typed Data Models for M14 Surgical Navigation Subsystem."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum
import math
from typing import Any, Mapping, Optional, Tuple

from holomed.navigation.constants import (
    INSTRUMENT_ID_REGEX,
    MAX_TOOL_COORDINATE_MM,
    QUATERNION_UNIT_NORM_TOLERANCE,
    SESSION_ID_REGEX,
)
from holomed.navigation.exceptions import NavigationValidationError
from holomed.workflow.models import SafetyInterlock


class NavigationState(str, Enum):
    """Lifecycle and safety state of real-time surgical navigation."""

    UNINITIALIZED = "UNINITIALIZED"
    IDLE = "IDLE"
    TRACKING = "TRACKING"
    PRE_ENTRY = "PRE_ENTRY"
    ALIGNED = "ALIGNED"
    DEVIATED = "DEVIATED"
    STALE = "STALE"
    INTERLOCKED = "INTERLOCKED"


class PositionClass(str, Enum):
    """Geometric axial location of the instrument tip relative to the planned trajectory segment."""

    PRE_ENTRY = "PRE_ENTRY"
    IN_TRAJECTORY = "IN_TRAJECTORY"
    POST_TARGET = "POST_TARGET"


@dataclass(frozen=True)
class TrackedInstrumentPose:
    """Immutable physical 6-DOF tracked instrument pose telemetry packet."""

    instrument_id: str
    session_id: str
    epoch_id: int
    sequence_number: int
    tip_position_mm: Tuple[float, float, float]
    orientation_quaternion: Tuple[float, float, float, float]  # (qw, qx, qy, qz)
    coordinate_frame: str
    confidence: float
    uncertainty: float
    timestamp_utc: str

    def __post_init__(self) -> None:
        if not isinstance(self.instrument_id, str) or not INSTRUMENT_ID_REGEX.match(self.instrument_id):
            raise NavigationValidationError(f"Invalid instrument_id syntax: {self.instrument_id!r}")
        if not isinstance(self.session_id, str) or not SESSION_ID_REGEX.match(self.session_id):
            raise NavigationValidationError(f"Invalid session_id syntax: {self.session_id!r}")
        if not isinstance(self.epoch_id, int) or self.epoch_id < 0:
            raise NavigationValidationError("epoch_id must be non-negative int")
        if not isinstance(self.sequence_number, int) or self.sequence_number < 0:
            raise NavigationValidationError("sequence_number must be non-negative int")

        # 1. Coordinate checks (finite & operational bound)
        if not isinstance(self.tip_position_mm, (tuple, list)) or len(self.tip_position_mm) != 3:
            raise NavigationValidationError("tip_position_mm must be a 3-element tuple of floats")
        for val in self.tip_position_mm:
            if not isinstance(val, (int, float)) or not math.isfinite(val):
                raise NavigationValidationError("tip_position_mm coordinates must be finite floats (no NaN/Inf)")
            if abs(val) > MAX_TOOL_COORDINATE_MM:
                raise NavigationValidationError(
                    f"tip_position_mm coordinate ({val:.1f} mm) exceeds operational bound ({MAX_TOOL_COORDINATE_MM} mm)"
                )

        # 2. Quaternion checks (finite, unit norm, canonical hemisphere)
        if not isinstance(self.orientation_quaternion, (tuple, list)) or len(self.orientation_quaternion) != 4:
            raise NavigationValidationError("orientation_quaternion must be a 4-element tuple (qw, qx, qy, qz)")
        for val in self.orientation_quaternion:
            if not isinstance(val, (int, float)) or not math.isfinite(val):
                raise NavigationValidationError("orientation_quaternion elements must be finite floats (no NaN/Inf)")

        qw, qx, qy, qz = self.orientation_quaternion
        norm_sq = qw * qw + qx * qx + qy * qy + qz * qz
        if abs(norm_sq - 1.0) > QUATERNION_UNIT_NORM_TOLERANCE:
            raise NavigationValidationError(
                f"orientation_quaternion must be unit-normalized (norm^2 = {norm_sq:.6f})"
            )

        # Enforce canonical hemisphere (qw >= 0.0)
        if qw < 0.0:
            qw, qx, qy, qz = -qw, -qx, -qy, -qz
            object.__setattr__(self, "orientation_quaternion", (qw, qx, qy, qz))

        if not isinstance(self.coordinate_frame, str) or not self.coordinate_frame.strip():
            raise NavigationValidationError("coordinate_frame must be non-empty string")

        # 3. Confidence & Uncertainty in [0.0, 1.0]
        if not isinstance(self.confidence, (int, float)) or not (0.0 <= self.confidence <= 1.0) or not math.isfinite(self.confidence):
            raise NavigationValidationError("confidence must be finite float in [0.0, 1.0]")
        if not isinstance(self.uncertainty, (int, float)) or not (0.0 <= self.uncertainty <= 1.0) or not math.isfinite(self.uncertainty):
            raise NavigationValidationError("uncertainty must be finite float in [0.0, 1.0]")

        # 4. Timestamp format check
        if not isinstance(self.timestamp_utc, str) or not self.timestamp_utc.strip():
            raise NavigationValidationError("timestamp_utc must be non-empty string")
        try:
            # Parse ISO-8601 UTC timestamp
            ts = self.timestamp_utc.replace("Z", "+00:00")
            datetime.fromisoformat(ts)
        except Exception as e:
            raise NavigationValidationError(f"Invalid timestamp_utc ISO-8601 format: {self.timestamp_utc!r}") from e


@dataclass(frozen=True)
class TrajectoryDeviationRecord:
    """Immutable quantitative spatial deviation assessment of a tracked tool against a registered trajectory."""

    record_id: str
    session_id: str
    trajectory_id: str
    instrument_id: str
    lateral_deviation_mm: float
    angular_deviation_deg: float
    axial_distance_mm: float
    position_class: PositionClass
    pose_age_ms: float
    state: NavigationState
    is_aligned: bool
    is_safe: bool
    safety_interlock: Optional[SafetyInterlock]
    evaluated_at_utc: str

    def __post_init__(self) -> None:
        for name, val in (
            ("lateral_deviation_mm", self.lateral_deviation_mm),
            ("angular_deviation_deg", self.angular_deviation_deg),
            ("axial_distance_mm", self.axial_distance_mm),
            ("pose_age_ms", self.pose_age_ms),
        ):
            if not isinstance(val, (int, float)) or not math.isfinite(val):
                raise NavigationValidationError(f"{name} must be finite float, got {val!r}")
        if self.lateral_deviation_mm < 0.0:
            raise NavigationValidationError("lateral_deviation_mm must be non-negative")
        if not (0.0 <= self.angular_deviation_deg <= 180.0):
            raise NavigationValidationError("angular_deviation_deg must be in [0.0, 180.0]")
        if not isinstance(self.position_class, PositionClass):
            raise NavigationValidationError(f"Invalid position_class: {self.position_class}")
        if not isinstance(self.state, NavigationState):
            raise NavigationValidationError(f"Invalid state: {self.state}")


@dataclass(frozen=True)
class NavigationStatusRecord:
    """Active navigation status snapshot for a surgical session."""

    session_id: str
    trajectory_id: Optional[str]
    epoch_id: int
    state: NavigationState
    active_instrument_id: Optional[str]
    last_deviation: Optional[TrajectoryDeviationRecord]
    is_guidance_active: bool
    updated_at_utc: str
