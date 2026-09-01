# -*- coding: utf-8 -*-
"""Strongly Typed Data Models for M13 Spatial Registration Subsystem."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import math
from typing import Any, Mapping, Optional, Tuple

from holomed.registration.constants import (
    DETERMINANT_TOLERANCE,
    FIDUCIAL_ID_REGEX,
    MAX_ALLOWED_FRE_MM,
    MAX_FIDUCIAL_POINTS,
    MAX_POINT_COORDINATE_MM,
    MIN_FIDUCIAL_POINTS,
    MIN_FIDUCIAL_SPACING_MM,
    ORTHONORMALITY_TOLERANCE,
    WARNING_FRE_THRESHOLD_MM,
)
from holomed.registration.exceptions import (
    RegistrationCapacityError,
    RegistrationDegeneracyError,
    RegistrationValidationError,
)


class RegistrationState(str, Enum):
    """Lifecycle state of spatial patient-to-plan registration."""

    DRAFT = "DRAFT"
    SOLVING = "SOLVING"
    SOLVED = "SOLVED"
    VERIFIED = "VERIFIED"
    FAILED = "FAILED"
    INVALIDATED = "INVALIDATED"


@dataclass(frozen=True)
class FiducialPointPair:
    """Paired planned virtual landmark and measured physical point."""

    fiducial_id: str
    planned_point_mm: Tuple[float, float, float]
    measured_point_mm: Tuple[float, float, float]
    label: str

    def __post_init__(self) -> None:
        if not isinstance(self.fiducial_id, str) or not FIDUCIAL_ID_REGEX.match(self.fiducial_id):
            raise RegistrationValidationError(f"Invalid fiducial_id syntax: {self.fiducial_id!r}")

        for name, pt in (("planned_point_mm", self.planned_point_mm), ("measured_point_mm", self.measured_point_mm)):
            if not isinstance(pt, (tuple, list)) or len(pt) != 3:
                raise RegistrationValidationError(f"{name} must be a 3-element tuple of floats")
            if not all(isinstance(v, (int, float)) and math.isfinite(v) for v in pt):
                raise RegistrationValidationError(f"{name} coordinates must be finite floats (no NaN or Inf)")
            if any(abs(v) > MAX_POINT_COORDINATE_MM for v in pt):
                raise RegistrationValidationError(
                    f"{name} coordinate magnitude exceeds operational bound ({MAX_POINT_COORDINATE_MM} mm)"
                )

        if not isinstance(self.label, str) or not self.label.strip():
            raise RegistrationValidationError("label must be non-empty string")


@dataclass(frozen=True)
class FiducialCloud:
    """Ordered collection of paired fiducials with geometric spacing and rank validation."""

    pairs: Tuple[FiducialPointPair, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.pairs, (tuple, list)):
            raise RegistrationValidationError("pairs must be a tuple/list of FiducialPointPair")

        n = len(self.pairs)
        if n < MIN_FIDUCIAL_POINTS:
            raise RegistrationValidationError(
                f"FiducialCloud requires at least {MIN_FIDUCIAL_POINTS} points, got {n}"
            )
        if n > MAX_FIDUCIAL_POINTS:
            raise RegistrationCapacityError(
                f"FiducialCloud exceeds MAX_FIDUCIAL_POINTS ({MAX_FIDUCIAL_POINTS}), got {n}"
            )

        # Pairwise distance spacing checks
        for i in range(n):
            for j in range(i + 1, n):
                # Check planned points spacing
                p1 = self.pairs[i].planned_point_mm
                p2 = self.pairs[j].planned_point_mm
                dx_p = p2[0] - p1[0]
                dy_p = p2[1] - p1[1]
                dz_p = p2[2] - p1[2]
                dist_p = math.sqrt(dx_p * dx_p + dy_p * dy_p + dz_p * dz_p)
                if dist_p < MIN_FIDUCIAL_SPACING_MM:
                    raise RegistrationValidationError(
                        f"Planned fiducials '{self.pairs[i].fiducial_id}' and '{self.pairs[j].fiducial_id}' "
                        f"spacing ({dist_p:.2f} mm) is less than minimum {MIN_FIDUCIAL_SPACING_MM} mm"
                    )

                # Check measured points spacing
                q1 = self.pairs[i].measured_point_mm
                q2 = self.pairs[j].measured_point_mm
                dx_q = q2[0] - q1[0]
                dy_q = q2[1] - q1[1]
                dz_q = q2[2] - q1[2]
                dist_q = math.sqrt(dx_q * dx_q + dy_q * dy_q + dz_q * dz_q)
                if dist_q < MIN_FIDUCIAL_SPACING_MM:
                    raise RegistrationValidationError(
                        f"Measured fiducials '{self.pairs[i].fiducial_id}' and '{self.pairs[j].fiducial_id}' "
                        f"spacing ({dist_q:.2f} mm) is less than minimum {MIN_FIDUCIAL_SPACING_MM} mm"
                    )

        # Validate non-collinearity in both point sets
        self._validate_non_collinear([p.planned_point_mm for p in self.pairs], "planned")
        self._validate_non_collinear([p.measured_point_mm for p in self.pairs], "measured")

    @staticmethod
    def _validate_non_collinear(points: list[Tuple[float, float, float]], frame_name: str) -> None:
        """Verify point cloud spans at least a 2D plane (triangle area >= 10.0 mm^2)."""
        n = len(points)
        max_area_sq = 0.0
        for i in range(n):
            for j in range(i + 1, n):
                for k in range(j + 1, n):
                    # Cross product (p_j - p_i) x (p_k - p_i)
                    v1x = points[j][0] - points[i][0]
                    v1y = points[j][1] - points[i][1]
                    v1z = points[j][2] - points[i][2]

                    v2x = points[k][0] - points[i][0]
                    v2y = points[k][1] - points[i][1]
                    v2z = points[k][2] - points[i][2]

                    cx = v1y * v2z - v1z * v2y
                    cy = v1z * v2x - v1x * v2z
                    cz = v1x * v2y - v1y * v2x

                    area_sq = (cx * cx + cy * cy + cz * cz) / 4.0
                    if area_sq > max_area_sq:
                        max_area_sq = area_sq

        max_area = math.sqrt(max_area_sq)
        if max_area < 10.0:  # COLLINEARITY_AREA_EPSILON
            raise RegistrationDegeneracyError(
                f"{frame_name.capitalize()} fiducial point cloud is collinear or near-collinear "
                f"(max triangle area {max_area:.2f} mm^2 < 10.0 mm^2)"
            )


@dataclass(frozen=True)
class RigidRegistrationTransform3D:
    """Immutable 6-DOF rigid spatial transformation [R | t] mapping plan space to patient space."""

    rotation_matrix: Tuple[Tuple[float, float, float], ...]
    translation_vector_mm: Tuple[float, float, float]
    source_frame: str
    target_frame: str
    epoch_id: int
    created_at_utc: str

    def __post_init__(self) -> None:
        if len(self.rotation_matrix) != 3 or any(len(r) != 3 for r in self.rotation_matrix):
            raise RegistrationValidationError("rotation_matrix must be a 3x3 tuple of floats")
        if len(self.translation_vector_mm) != 3:
            raise RegistrationValidationError("translation_vector_mm must be a 3-element tuple of floats")

        # Verify all entries finite
        for r in self.rotation_matrix:
            if not all(isinstance(v, (int, float)) and math.isfinite(v) for v in r):
                raise RegistrationValidationError("rotation_matrix entries must be finite floats")
        if not all(isinstance(v, (int, float)) and math.isfinite(v) for v in self.translation_vector_mm):
            raise RegistrationValidationError("translation_vector_mm entries must be finite floats")

        # Verify orthonormality: R * R^T = I
        R = self.rotation_matrix
        for i in range(3):
            for j in range(3):
                dot = sum(R[i][k] * R[j][k] for k in range(3))
                expected = 1.0 if i == j else 0.0
                if abs(dot - expected) > ORTHONORMALITY_TOLERANCE:
                    raise RegistrationValidationError(
                        f"rotation_matrix is not orthonormal at index ({i},{j}): deviation {abs(dot - expected)}"
                    )

        # Verify determinant: det(R) = +1.0
        det = (
            R[0][0] * (R[1][1] * R[2][2] - R[1][2] * R[2][1])
            - R[0][1] * (R[1][0] * R[2][2] - R[1][2] * R[2][0])
            + R[0][2] * (R[1][0] * R[2][1] - R[1][1] * R[2][0])
        )
        if abs(det - 1.0) > DETERMINANT_TOLERANCE:
            raise RegistrationValidationError(f"rotation_matrix determinant ({det:.6f}) deviates from +1.0")


@dataclass(frozen=True)
class RegistrationQualityReport:
    """Statistical report evaluating registration precision and residual errors."""

    fre_rms_mm: float
    fre_max_mm: float
    per_fiducial_residuals_mm: Tuple[float, ...]
    passed_clinical_threshold: bool
    warning_threshold_exceeded: bool
    target_registration_error_estimate_mm: Optional[float] = None

    def __post_init__(self) -> None:
        if not math.isfinite(self.fre_rms_mm) or self.fre_rms_mm < 0.0:
            raise RegistrationValidationError("fre_rms_mm must be non-negative finite float")
        if not math.isfinite(self.fre_max_mm) or self.fre_max_mm < 0.0:
            raise RegistrationValidationError("fre_max_mm must be non-negative finite float")
        if self.target_registration_error_estimate_mm is not None:
            if not math.isfinite(self.target_registration_error_estimate_mm) or self.target_registration_error_estimate_mm < 0.0:
                raise RegistrationValidationError("target_registration_error_estimate_mm must be non-negative finite float")


@dataclass(frozen=True)
class RegistrationStatusRecord:
    """Lifecycle record capturing the active registration state of a clinical session."""

    session_id: str
    plan_id: str
    epoch_id: int
    state: RegistrationState
    transform: Optional[RigidRegistrationTransform3D]
    quality_report: Optional[RegistrationQualityReport]
    locked: bool
    verified_at_utc: Optional[str] = None


@dataclass(frozen=True)
class RegistrationVerificationSnapshot:
    """Formal snapshot capturing registration verification during WHO Safety Timeout."""

    verification_id: str
    session_id: str
    epoch_id: int
    verifier_operator_id: str
    measured_drift_error_mm: float
    passed: bool
    verified_at_utc: str
