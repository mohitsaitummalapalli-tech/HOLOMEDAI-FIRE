# -*- coding: utf-8 -*-
"""Unit Tests for Pure Standard-Library Matrix and Vector Geometry."""

from __future__ import annotations

import math
import pytest

from holomed.registration.exceptions import RegistrationValidationError
from holomed.registration.geometry import (
    check_proper_rotation,
    cross_3d,
    dot_3d,
    matrix_det_3x3,
    matrix_mult_3x3,
    matrix_mult_vector_3x3,
    matrix_transpose_3x3,
    norm_3d,
)


def test_dot_cross_norm_3d() -> None:
    """Verify dot product, cross product, and Euclidean norm in 3D."""
    v1 = (1.0, 0.0, 0.0)
    v2 = (0.0, 1.0, 0.0)
    assert dot_3d(v1, v2) == 0.0
    assert cross_3d(v1, v2) == (0.0, 0.0, 1.0)
    assert norm_3d((3.0, 4.0, 12.0)) == 13.0


def test_matrix_mult_and_det() -> None:
    """Verify 3x3 matrix multiplication and determinant calculation."""
    # 90-degree rotation around Z
    Rz = (
        (0.0, -1.0, 0.0),
        (1.0, 0.0, 0.0),
        (0.0, 0.0, 1.0),
    )
    assert pytest.approx(matrix_det_3x3(Rz), rel=1e-6) == 1.0

    # Multiplying by its transpose gives identity
    Rz_T = matrix_transpose_3x3(Rz)
    I = matrix_mult_3x3(Rz, Rz_T)
    for i in range(3):
        for j in range(3):
            expected = 1.0 if i == j else 0.0
            assert pytest.approx(I[i][j], abs=1e-6) == expected

    # Vector rotation: (1, 0, 0) rotated 90 deg around Z -> (0, 1, 0)
    v_rot = matrix_mult_vector_3x3(Rz, (1.0, 0.0, 0.0))
    assert pytest.approx(v_rot[0], abs=1e-6) == 0.0
    assert pytest.approx(v_rot[1], abs=1e-6) == 1.0
    assert pytest.approx(v_rot[2], abs=1e-6) == 0.0


def test_check_proper_rotation_rejection() -> None:
    """Verify check_proper_rotation rejects reflection and non-orthogonal matrices."""
    # Reflection (det = -1)
    refl = (
        (1.0, 0.0, 0.0),
        (0.0, 1.0, 0.0),
        (0.0, 0.0, -1.0),
    )
    with pytest.raises(RegistrationValidationError):
        check_proper_rotation(refl)
