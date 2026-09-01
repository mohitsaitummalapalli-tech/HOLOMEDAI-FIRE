# -*- coding: utf-8 -*-
"""Canonical Constants and Limits for M13 Surgical Registration Subsystem."""

from __future__ import annotations

import re

# Geometric & Capacity Limits
MIN_FIDUCIAL_POINTS: int = 3
MAX_FIDUCIAL_POINTS: int = 16
MAX_ACTIVE_REGISTRATIONS: int = 16
MAX_ALLOWED_FRE_MM: float = 1.5           # Explicit HoloMed project safety threshold
WARNING_FRE_THRESHOLD_MM: float = 1.0     # Explicit warning threshold for clinical review
MIN_FIDUCIAL_SPACING_MM: float = 10.0     # Minimum spacing between distinct fiducials in same frame
MAX_POINT_COORDINATE_MM: float = 2000.0   # Operational spatial bounds (+/- 2000 mm)
COLLINEARITY_AREA_EPSILON: float = 10.0   # Minimum triangle area (mm^2) to reject 1D collinearity

# Numerical Solver Bounds
JACOBI_MAX_SWEEPS: int = 50               # Maximum sweep iterations for cyclic Jacobi eigensolver
JACOBI_TOLERANCE: float = 1e-12           # Off-diagonal Frobenius norm stopping tolerance
ORTHONORMALITY_TOLERANCE: float = 1e-6    # Maximum allowable deviation from R^T * R = I
DETERMINANT_TOLERANCE: float = 1e-6       # Allowable deviation from det(R) = +1.0

# Identifier Grammars
FIDUCIAL_ID_REGEX = re.compile(r"^[a-z0-9_-]{1,64}$")
SESSION_ID_REGEX = re.compile(r"^[a-zA-Z0-9_-]{1,64}$")
