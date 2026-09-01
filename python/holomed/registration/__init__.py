# -*- coding: utf-8 -*-
"""HoloMed AI Spatial Patient-to-Plan Registration & Fiducial Alignment Foundation (M13)."""

from __future__ import annotations

from holomed.registration.constants import (
    COLLINEARITY_AREA_EPSILON,
    DETERMINANT_TOLERANCE,
    FIDUCIAL_ID_REGEX,
    JACOBI_MAX_SWEEPS,
    JACOBI_TOLERANCE,
    MAX_ACTIVE_REGISTRATIONS,
    MAX_ALLOWED_FRE_MM,
    MAX_FIDUCIAL_POINTS,
    MAX_POINT_COORDINATE_MM,
    MIN_FIDUCIAL_POINTS,
    MIN_FIDUCIAL_SPACING_MM,
    ORTHONORMALITY_TOLERANCE,
    WARNING_FRE_THRESHOLD_MM,
)
from holomed.registration.exceptions import (
    RegistrationAccuracyError,
    RegistrationAuthorizationError,
    RegistrationCapacityError,
    RegistrationDegeneracyError,
    RegistrationEpochMismatchError,
    RegistrationError,
    RegistrationLifecycleError,
    RegistrationPlanMismatchError,
    RegistrationShutdownError,
    RegistrationValidationError,
    RegistrationVerificationError,
)
from holomed.registration.geometry import (
    cross_3d,
    dot_3d,
    matrix_det_3x3,
    matrix_mult_3x3,
    matrix_mult_vector_3x3,
    matrix_transpose_3x3,
    norm_3d,
)
from holomed.registration.metrics import compute_registration_metrics
from holomed.registration.models import (
    FiducialCloud,
    FiducialPointPair,
    RegistrationQualityReport,
    RegistrationState,
    RegistrationStatusRecord,
    RegistrationVerificationSnapshot,
    RigidRegistrationTransform3D,
)
from holomed.registration.service import RegistrationService
from holomed.registration.solver import HornRigidRegistrationSolver
from holomed.registration.transforms import (
    compose_transforms,
    invert_transform,
    transform_point,
    transform_trajectory,
    transform_vector,
)

__all__ = [
    # Constants
    "MIN_FIDUCIAL_POINTS",
    "MAX_FIDUCIAL_POINTS",
    "MAX_ACTIVE_REGISTRATIONS",
    "MAX_ALLOWED_FRE_MM",
    "WARNING_FRE_THRESHOLD_MM",
    "MIN_FIDUCIAL_SPACING_MM",
    "MAX_POINT_COORDINATE_MM",
    "COLLINEARITY_AREA_EPSILON",
    "JACOBI_MAX_SWEEPS",
    "JACOBI_TOLERANCE",
    "ORTHONORMALITY_TOLERANCE",
    "DETERMINANT_TOLERANCE",
    "FIDUCIAL_ID_REGEX",
    # Exceptions
    "RegistrationError",
    "RegistrationLifecycleError",
    "RegistrationValidationError",
    "RegistrationCapacityError",
    "RegistrationDegeneracyError",
    "RegistrationAccuracyError",
    "RegistrationPlanMismatchError",
    "RegistrationVerificationError",
    "RegistrationEpochMismatchError",
    "RegistrationAuthorizationError",
    "RegistrationShutdownError",
    # Models & Enums
    "RegistrationState",
    "FiducialPointPair",
    "FiducialCloud",
    "RigidRegistrationTransform3D",
    "RegistrationQualityReport",
    "RegistrationStatusRecord",
    "RegistrationVerificationSnapshot",
    # Geometry & Transforms
    "dot_3d",
    "cross_3d",
    "norm_3d",
    "matrix_mult_3x3",
    "matrix_mult_vector_3x3",
    "matrix_det_3x3",
    "matrix_transpose_3x3",
    "transform_point",
    "transform_vector",
    "invert_transform",
    "compose_transforms",
    "transform_trajectory",
    # Solver & Metrics
    "HornRigidRegistrationSolver",
    "compute_registration_metrics",
    "RegistrationService",
]
