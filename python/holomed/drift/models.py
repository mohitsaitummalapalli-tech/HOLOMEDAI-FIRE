# -*- coding: utf-8 -*-
"""Strongly Typed Data Models for M16 Registration Drift & Landmark Monitoring."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum
import math
from typing import Optional, Tuple

from holomed.drift.constants import (
    DEFAULT_PATIENT_TRACKER_FRAME,
    LANDMARK_ID_REGEX,
    MAX_LANDMARK_TOLERANCE_MM,
    MAX_POINT_COORDINATE_MM,
    MIN_LANDMARK_TOLERANCE_MM,
    SESSION_ID_REGEX,
)
from holomed.drift.exceptions import DriftValidationError


class DriftState(str, Enum):
    """Lifecycle and verification state for registration drift monitoring."""

    UNINITIALIZED = "UNINITIALIZED"
    READY = "READY"
    STABLE = "STABLE"
    WARNING = "WARNING"
    DRIFT_EXCEEDED = "DRIFT_EXCEEDED"
    INTERLOCKED = "INTERLOCKED"


@dataclass(frozen=True)
class LandmarkDefinition:
    """Immutable planned anatomical landmark / checkpoint definition."""

    landmark_id: str
    name: str
    planned_point_mm: Tuple[float, float, float]
    max_tolerance_mm: float
    min_confidence: float
    max_uncertainty_mm: float

    def __post_init__(self) -> None:
        if not isinstance(self.landmark_id, str) or not LANDMARK_ID_REGEX.match(self.landmark_id):
            raise DriftValidationError(f"Invalid landmark_id syntax: {self.landmark_id!r}")
        if not isinstance(self.name, str) or not self.name.strip():
            raise DriftValidationError("name must be non-empty string")

        # Planned point validation
        if not isinstance(self.planned_point_mm, (tuple, list)) or len(self.planned_point_mm) != 3:
            raise DriftValidationError("planned_point_mm must be a 3-element tuple of floats")
        for val in self.planned_point_mm:
            if not isinstance(val, (int, float)) or not math.isfinite(val):
                raise DriftValidationError("planned_point_mm coordinates must be finite floats")
            if abs(val) > MAX_POINT_COORDINATE_MM:
                raise DriftValidationError(
                    f"planned_point_mm coordinate ({val:.1f} mm) exceeds operational bound ({MAX_POINT_COORDINATE_MM} mm)"
                )

        # Tolerance validation: (0.0, 2.0]
        if not isinstance(self.max_tolerance_mm, (int, float)) or not math.isfinite(self.max_tolerance_mm):
            raise DriftValidationError("max_tolerance_mm must be finite float")
        if self.max_tolerance_mm <= 0.0 or self.max_tolerance_mm > MAX_LANDMARK_TOLERANCE_MM:
            raise DriftValidationError(
                f"max_tolerance_mm ({self.max_tolerance_mm:.2f}) must be in (0.0, {MAX_LANDMARK_TOLERANCE_MM}] mm"
            )

        # Confidence validation: [0.0, 1.0]
        if not isinstance(self.min_confidence, (int, float)) or not math.isfinite(self.min_confidence):
            raise DriftValidationError("min_confidence must be finite float")
        if not (0.0 <= self.min_confidence <= 1.0):
            raise DriftValidationError("min_confidence must be in [0.0, 1.0]")

        # Uncertainty validation: >= 0.0
        if not isinstance(self.max_uncertainty_mm, (int, float)) or not math.isfinite(self.max_uncertainty_mm):
            raise DriftValidationError("max_uncertainty_mm must be finite float")
        if self.max_uncertainty_mm < 0.0:
            raise DriftValidationError("max_uncertainty_mm must be non-negative")


@dataclass(frozen=True)
class LandmarkObservation:
    """Immutable physical pointer observation of an anatomical landmark."""

    landmark_id: str
    session_id: str
    epoch_id: int
    sequence_number: int
    observed_point_mm: Tuple[float, float, float]
    confidence: float
    uncertainty_mm: float
    timestamp_utc: str
    coordinate_frame: str = DEFAULT_PATIENT_TRACKER_FRAME

    def __post_init__(self) -> None:
        if not isinstance(self.landmark_id, str) or not LANDMARK_ID_REGEX.match(self.landmark_id):
            raise DriftValidationError(f"Invalid landmark_id syntax: {self.landmark_id!r}")
        if not isinstance(self.session_id, str) or not SESSION_ID_REGEX.match(self.session_id):
            raise DriftValidationError(f"Invalid session_id syntax: {self.session_id!r}")
        if not isinstance(self.epoch_id, int) or self.epoch_id < 0:
            raise DriftValidationError("epoch_id must be non-negative int")
        if not isinstance(self.sequence_number, int) or self.sequence_number < 0:
            raise DriftValidationError("sequence_number must be non-negative int")

        # Observed point validation
        if not isinstance(self.observed_point_mm, (tuple, list)) or len(self.observed_point_mm) != 3:
            raise DriftValidationError("observed_point_mm must be a 3-element tuple of floats")
        for val in self.observed_point_mm:
            if not isinstance(val, (int, float)) or not math.isfinite(val):
                raise DriftValidationError("observed_point_mm coordinates must be finite floats")
            if abs(val) > MAX_POINT_COORDINATE_MM:
                raise DriftValidationError(
                    f"observed_point_mm coordinate ({val:.1f} mm) exceeds operational bound ({MAX_POINT_COORDINATE_MM} mm)"
                )

        # Confidence: [0.0, 1.0]
        if not isinstance(self.confidence, (int, float)) or not math.isfinite(self.confidence):
            raise DriftValidationError("confidence must be finite float")
        if not (0.0 <= self.confidence <= 1.0):
            raise DriftValidationError("confidence must be in [0.0, 1.0]")

        # Uncertainty: >= 0.0
        if not isinstance(self.uncertainty_mm, (int, float)) or not math.isfinite(self.uncertainty_mm):
            raise DriftValidationError("uncertainty_mm must be finite float")
        if self.uncertainty_mm < 0.0:
            raise DriftValidationError("uncertainty_mm must be non-negative")

        # Timestamp validation
        if not isinstance(self.timestamp_utc, str) or not self.timestamp_utc.strip():
            raise DriftValidationError("timestamp_utc must be non-empty string")
        try:
            ts = self.timestamp_utc.replace("Z", "+00:00")
            datetime.fromisoformat(ts)
        except Exception as e:
            raise DriftValidationError(f"Invalid timestamp_utc ISO-8601 format: {self.timestamp_utc!r}") from e

        # Coordinate frame
        if not isinstance(self.coordinate_frame, str) or not self.coordinate_frame.strip():
            raise DriftValidationError("coordinate_frame must be non-empty string")


@dataclass(frozen=True)
class LandmarkVerificationRecord:
    """Immutable record of an anatomical landmark verification evaluation."""

    record_id: str
    session_id: str
    landmark_id: str
    epoch_id: int
    expected_point_mm: Tuple[float, float, float]
    observed_point_mm: Tuple[float, float, float]
    residual_error_mm: float
    effective_tolerance_mm: float
    state: DriftState
    passed: bool
    evaluated_at_utc: str

    def __post_init__(self) -> None:
        if not isinstance(self.record_id, str) or not self.record_id.strip():
            raise DriftValidationError("record_id must be non-empty string")
        if not isinstance(self.landmark_id, str) or not self.landmark_id.strip():
            raise DriftValidationError("landmark_id must be non-empty string")
        if not isinstance(self.residual_error_mm, (int, float)) or not math.isfinite(self.residual_error_mm) or self.residual_error_mm < 0.0:
            raise DriftValidationError("residual_error_mm must be non-negative finite float")
        if not isinstance(self.effective_tolerance_mm, (int, float)) or not math.isfinite(self.effective_tolerance_mm):
            raise DriftValidationError("effective_tolerance_mm must be finite float")
        if not isinstance(self.state, DriftState):
            raise DriftValidationError(f"Invalid state: {self.state}")


@dataclass(frozen=True)
class DriftStatusRecord:
    """Active registration drift monitoring status snapshot."""

    session_id: str
    epoch_id: int
    state: DriftState
    bound_landmark_count: int
    verified_landmark_count: int
    last_verification: Optional[LandmarkVerificationRecord]
    is_safe: bool
    updated_at_utc: str
