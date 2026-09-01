# -*- coding: utf-8 -*-
"""Deterministic Pure Standard-Library 3D Rigid Registration Solver (Horn 1987)."""

from __future__ import annotations

from datetime import datetime, timezone
import math
from typing import Optional, Tuple

from holomed.registration.constants import (
    JACOBI_MAX_SWEEPS,
    JACOBI_TOLERANCE,
)
from holomed.registration.exceptions import (
    RegistrationAccuracyError,
    RegistrationDegeneracyError,
)
from holomed.registration.geometry import check_proper_rotation
from holomed.registration.models import (
    FiducialCloud,
    RigidRegistrationTransform3D,
)


class HornRigidRegistrationSolver:
    """Closed-form 3D point-set registration solver using Horn's quaternion method."""

    @classmethod
    def solve(
        cls,
        cloud: FiducialCloud,
        source_frame: str = "plan_frame",
        target_frame: str = "patient_tracker_frame",
        epoch_id: int = 0,
        max_sweeps: Optional[int] = None,
    ) -> RigidRegistrationTransform3D:
        """Compute optimal rigid transform [R | t] mapping plan points to physical patient points."""
        n = len(cloud.pairs)
        if n < 3:
            raise RegistrationDegeneracyError("At least 3 fiducials are required to solve registration")

        # 1. Compute Centroids
        src_cx = sum(p.planned_point_mm[0] for p in cloud.pairs) / float(n)
        src_cy = sum(p.planned_point_mm[1] for p in cloud.pairs) / float(n)
        src_cz = sum(p.planned_point_mm[2] for p in cloud.pairs) / float(n)

        tgt_cx = sum(p.measured_point_mm[0] for p in cloud.pairs) / float(n)
        tgt_cy = sum(p.measured_point_mm[1] for p in cloud.pairs) / float(n)
        tgt_cz = sum(p.measured_point_mm[2] for p in cloud.pairs) / float(n)

        # 2. Centered Coordinates and Cross-Covariance Matrix M
        Sxx = Sxy = Sxz = 0.0
        Syx = Syy = Syz = 0.0
        Szx = Szy = Szz = 0.0

        for p in cloud.pairs:
            x = p.planned_point_mm[0] - src_cx
            y = p.planned_point_mm[1] - src_cy
            z = p.planned_point_mm[2] - src_cz

            xp = p.measured_point_mm[0] - tgt_cx
            yp = p.measured_point_mm[1] - tgt_cy
            zp = p.measured_point_mm[2] - tgt_cz

            Sxx += x * xp
            Sxy += x * yp
            Sxz += x * zp
            Syx += y * xp
            Syy += y * yp
            Syz += y * zp
            Szx += z * xp
            Szy += z * yp
            Szz += z * zp

        # 3. Construct Horn's 4x4 Symmetric Matrix N_4
        trace = Sxx + Syy + Szz

        A23 = Syz - Szy
        A31 = Szx - Sxz
        A12 = Sxy - Syx

        N = [
            [trace, A23, A31, A12],
            [A23, Sxx - Syy - Szz, Sxy + Syx, Szx + Sxz],
            [A31, Sxy + Syx, -Sxx + Syy - Szz, Syz + Szy],
            [A12, Szx + Sxz, Syz + Szy, -Sxx - Syy + Szz],
        ]

        # 4. Cyclic Jacobi Eigenvalue Solver
        eigenvalues, eigenvectors = cls._solve_jacobi_4x4(N, max_sweeps=max_sweeps)

        # 5. Select Eigenvector Corresponding to Maximum Eigenvalue
        max_idx = 0
        max_val = eigenvalues[0]
        for i in range(1, 4):
            if eigenvalues[i] > max_val:
                max_val = eigenvalues[i]
                max_idx = i

        q_raw = [eigenvectors[r][max_idx] for r in range(4)]
        q_norm = math.sqrt(sum(v * v for v in q_raw))
        if q_norm < 1e-12:
            raise RegistrationAccuracyError("Degenerate quaternion norm in registration solver")

        w, qx, qy, qz = [v / q_norm for v in q_raw]

        # 6. Convert Unit Quaternion to Proper 3x3 Rotation Matrix
        R: Tuple[Tuple[float, float, float], ...] = (
            (1.0 - 2.0 * (qy * qy + qz * qz), 2.0 * (qx * qy - w * qz), 2.0 * (qx * qz + w * qy)),
            (2.0 * (qx * qy + w * qz), 1.0 - 2.0 * (qx * qx + qz * qz), 2.0 * (qy * qz - w * qx)),
            (2.0 * (qx * qz - w * qy), 2.0 * (qy * qz + w * qx), 1.0 - 2.0 * (qx * qx + qy * qy)),
        )

        # Verify proper rotation (det = +1, orthonormal)
        check_proper_rotation(R)

        # 7. Translation: t = target_centroid - R * source_centroid
        R_src_cx = R[0][0] * src_cx + R[0][1] * src_cy + R[0][2] * src_cz
        R_src_cy = R[1][0] * src_cx + R[1][1] * src_cy + R[1][2] * src_cz
        R_src_cz = R[2][0] * src_cx + R[2][1] * src_cy + R[2][2] * src_cz

        tx = tgt_cx - R_src_cx
        ty = tgt_cy - R_src_cy
        tz = tgt_cz - R_src_cz

        now_utc = datetime.now(timezone.utc).isoformat()
        return RigidRegistrationTransform3D(
            rotation_matrix=R,
            translation_vector_mm=(tx, ty, tz),
            source_frame=source_frame,
            target_frame=target_frame,
            epoch_id=epoch_id,
            created_at_utc=now_utc,
        )

    @classmethod
    def _solve_jacobi_4x4(
        cls,
        A_in: list[list[float]],
        max_sweeps: Optional[int] = None,
    ) -> Tuple[list[float], list[list[float]]]:
        """Deterministic cyclic Jacobi eigensolver for 4x4 real symmetric matrix."""
        effective_max_sweeps = JACOBI_MAX_SWEEPS if max_sweeps is None else max_sweeps
        A = [[A_in[i][j] for j in range(4)] for i in range(4)]
        V = [[1.0 if i == j else 0.0 for j in range(4)] for i in range(4)]

        converged = False
        for sweep in range(effective_max_sweeps):
            off_diag_sum = 0.0
            for i in range(4):
                for j in range(i + 1, 4):
                    off_diag_sum += A[i][j] * A[i][j]

            if math.sqrt(2.0 * off_diag_sum) < JACOBI_TOLERANCE:
                converged = True
                break

            for p in range(4):
                for q in range(p + 1, 4):
                    Apq = A[p][q]
                    if abs(Apq) < 1e-15:
                        continue

                    App = A[p][p]
                    Aqq = A[q][q]
                    tau = (Aqq - App) / (2.0 * Apq)
                    if tau >= 0.0:
                        t = 1.0 / (tau + math.sqrt(1.0 + tau * tau))
                    else:
                        t = -1.0 / (-tau + math.sqrt(1.0 + tau * tau))

                    c = 1.0 / math.sqrt(1.0 + t * t)
                    s = t * c
                    theta = s / (1.0 + c)

                    A[p][p] = App - t * Apq
                    A[q][q] = Aqq + t * Apq
                    A[p][q] = 0.0
                    A[q][p] = 0.0

                    for r in range(4):
                        if r != p and r != q:
                            Apr = A[r][p]
                            Aqr = A[r][q]
                            A[r][p] = Apr - s * (Aqr + theta * Apr)
                            A[p][r] = A[r][p]
                            A[r][q] = Aqr + s * (Apr - theta * Aqr)
                            A[q][r] = A[r][q]

                    for r in range(4):
                        Vrp = V[r][p]
                        Vrq = V[r][q]
                        V[r][p] = Vrp - s * (Vrq + theta * Vrp)
                        V[r][q] = Vrq + s * (Vrp - theta * Vrq)

        if not converged:
            raise RegistrationAccuracyError(
                f"Cyclic Jacobi eigensolver failed to converge within {effective_max_sweeps} sweeps"
            )

        eigenvalues = [A[i][i] for i in range(4)]
        return eigenvalues, V
