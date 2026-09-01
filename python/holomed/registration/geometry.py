# -*- coding: utf-8 -*-
"""Deterministic Pure Standard-Library 3D Matrix & Vector Geometry for M13."""

from __future__ import annotations

import math
from typing import Tuple

from holomed.registration.constants import DETERMINANT_TOLERANCE, ORTHONORMALITY_TOLERANCE
from holomed.registration.exceptions import RegistrationValidationError

Point3D = Tuple[float, float, float]
Vector3D = Tuple[float, float, float]
Matrix3x3 = Tuple[Tuple[float, float, float], Tuple[float, float, float], Tuple[float, float, float]]


def dot_3d(v1: Vector3D, v2: Vector3D) -> float:
    """Compute dot product of two 3D vectors."""
    return v1[0] * v2[0] + v1[1] * v2[1] + v1[2] * v2[2]


def cross_3d(v1: Vector3D, v2: Vector3D) -> Vector3D:
    """Compute cross product of two 3D vectors."""
    return (
        v1[1] * v2[2] - v1[2] * v2[1],
        v1[2] * v2[0] - v1[0] * v2[2],
        v1[0] * v2[1] - v1[1] * v2[0],
    )


def norm_3d(v: Vector3D) -> float:
    """Compute Euclidean norm of a 3D vector."""
    return math.sqrt(v[0] * v[0] + v[1] * v[1] + v[2] * v[2])


def matrix_mult_3x3(A: Matrix3x3, B: Matrix3x3) -> Matrix3x3:
    """Compute matrix product of two 3x3 matrices."""
    res: list[list[float]] = [[0.0, 0.0, 0.0] for _ in range(3)]
    for i in range(3):
        for j in range(3):
            res[i][j] = sum(A[i][k] * B[k][j] for k in range(3))
    return (
        (res[0][0], res[0][1], res[0][2]),
        (res[1][0], res[1][1], res[1][2]),
        (res[2][0], res[2][1], res[2][2]),
    )


def matrix_mult_vector_3x3(A: Matrix3x3, v: Vector3D) -> Vector3D:
    """Multiply 3x3 matrix by 3D vector."""
    return (
        A[0][0] * v[0] + A[0][1] * v[1] + A[0][2] * v[2],
        A[1][0] * v[0] + A[1][1] * v[1] + A[1][2] * v[2],
        A[2][0] * v[0] + A[2][1] * v[1] + A[2][2] * v[2],
    )


def matrix_det_3x3(A: Matrix3x3) -> float:
    """Compute determinant of a 3x3 matrix."""
    return (
        A[0][0] * (A[1][1] * A[2][2] - A[1][2] * A[2][1])
        - A[0][1] * (A[1][0] * A[2][2] - A[1][2] * A[2][0])
        + A[0][2] * (A[1][0] * A[2][1] - A[1][1] * A[2][0])
    )


def matrix_transpose_3x3(A: Matrix3x3) -> Matrix3x3:
    """Transpose a 3x3 matrix."""
    return (
        (A[0][0], A[1][0], A[2][0]),
        (A[0][1], A[1][1], A[2][1]),
        (A[0][2], A[1][2], A[2][2]),
    )


def check_proper_rotation(R: Matrix3x3) -> None:
    """Verify that 3x3 matrix is an orthonormal proper rotation (det = +1)."""
    det = matrix_det_3x3(R)
    if abs(det - 1.0) > DETERMINANT_TOLERANCE:
        raise RegistrationValidationError(f"Improper rotation: determinant is {det:.6f}, expected +1.0")

    for i in range(3):
        for j in range(3):
            dot = sum(R[i][k] * R[j][k] for k in range(3))
            expected = 1.0 if i == j else 0.0
            if abs(dot - expected) > ORTHONORMALITY_TOLERANCE:
                raise RegistrationValidationError(
                    f"Orthonormality violation at ({i},{j}): deviation {abs(dot - expected)}"
                )
