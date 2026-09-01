# -*- coding: utf-8 -*-
"""Registration Error Metrics and Statistical Evaluation for M13."""

from __future__ import annotations

import math
from typing import Optional, Tuple

from holomed.registration.constants import MAX_ALLOWED_FRE_MM, WARNING_FRE_THRESHOLD_MM
from holomed.registration.models import (
    FiducialCloud,
    RegistrationQualityReport,
    RigidRegistrationTransform3D,
)


def compute_registration_metrics(
    cloud: FiducialCloud,
    transform: RigidRegistrationTransform3D,
    target_point_mm: Optional[Tuple[float, float, float]] = None,
) -> RegistrationQualityReport:
    """Compute exact FRE residuals and model-based TRE estimate.
    
    NOTE ON METRIC CLASSIFICATION:
    - FRE is an EXACT measured residual metric for the supplied fiducial pairs.
    - TRE is a MODEL-BASED ESTIMATE / APPROXIMATION, NOT an exact clinical measurement.
    """
    R = transform.rotation_matrix
    t = transform.translation_vector_mm

    residuals: list[float] = []
    sq_sum = 0.0
    max_res = 0.0

    for pair in cloud.pairs:
        p = pair.planned_point_mm
        q = pair.measured_point_mm

        # Transformed planned point: p' = R * p + t
        px = R[0][0] * p[0] + R[0][1] * p[1] + R[0][2] * p[2] + t[0]
        py = R[1][0] * p[0] + R[1][1] * p[1] + R[1][2] * p[2] + t[1]
        pz = R[2][0] * p[0] + R[2][1] * p[1] + R[2][2] * p[2] + t[2]

        # Residual: p' - q
        rx = px - q[0]
        ry = py - q[1]
        rz = pz - q[2]
        res = math.sqrt(rx * rx + ry * ry + rz * rz)

        residuals.append(res)
        sq_sum += res * res
        if res > max_res:
            max_res = res

    n = len(cloud.pairs)
    fre_rms = math.sqrt(sq_sum / float(n))

    passed = fre_rms <= MAX_ALLOWED_FRE_MM
    warning = WARNING_FRE_THRESHOLD_MM < fre_rms <= MAX_ALLOWED_FRE_MM

    # Model-based TRE estimate (Maurer & Fitzpatrick heuristic approximation)
    tre_est: Optional[float] = None
    if target_point_mm is not None and n >= 3:
        # Centroid of planned fiducials
        cx = sum(p.planned_point_mm[0] for p in cloud.pairs) / float(n)
        cy = sum(p.planned_point_mm[1] for p in cloud.pairs) / float(n)
        cz = sum(p.planned_point_mm[2] for p in cloud.pairs) / float(n)

        # Distance from target to centroid
        dtx = target_point_mm[0] - cx
        dty = target_point_mm[1] - cy
        dtz = target_point_mm[2] - cz
        d_target_sq = dtx * dtx + dty * dty + dtz * dtz

        # RMS distance of fiducials from centroid
        r_fid_sq_sum = sum(
            (p.planned_point_mm[0] - cx) ** 2
            + (p.planned_point_mm[1] - cy) ** 2
            + (p.planned_point_mm[2] - cz) ** 2
            for p in cloud.pairs
        )
        r_fid_rms_sq = r_fid_sq_sum / float(n)

        if r_fid_rms_sq > 1e-6:
            factor = (1.0 / float(n)) + (d_target_sq / (3.0 * r_fid_rms_sq))
            tre_est = fre_rms * math.sqrt(max(0.0, factor))

    return RegistrationQualityReport(
        fre_rms_mm=fre_rms,
        fre_max_mm=max_res,
        per_fiducial_residuals_mm=tuple(residuals),
        passed_clinical_threshold=passed,
        warning_threshold_exceeded=warning,
        target_registration_error_estimate_mm=tre_est,
    )
