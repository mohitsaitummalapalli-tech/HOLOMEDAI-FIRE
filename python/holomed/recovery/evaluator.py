# -*- coding: utf-8 -*-
"""Pure Mathematical & Pre-Validation Engine for M17 Spatial Recovery."""

from __future__ import annotations

from datetime import datetime, timezone
import math
from typing import Any, Optional, Tuple
import uuid

from holomed.recovery.constants import (
    DEFAULT_PATIENT_TRACKER_FRAME,
    DEFAULT_PLAN_FRAME,
    MAX_ALLOWED_RECOVERY_DRIFT_MM,
    MAX_ALLOWED_RECOVERY_FRE_MM,
    MAX_AUTHORIZATION_AGE_MS,
    MAX_FUTURE_TIMESTAMP_SKEW_MS,
)
from holomed.recovery.exceptions import (
    RecoveryAccuracyError,
    RecoveryAuthorizationError,
    RecoveryConsistencyError,
    RecoveryValidationError,
    RecoveryVerificationError,
)
from holomed.recovery.models import (
    RecoveryAuthorization,
    RecoveryVerificationSnapshot,
    StagedRegistrationCandidate,
)
from holomed.registration.metrics import compute_registration_metrics
from holomed.registration.models import (
    FiducialCloud,
    RegistrationState,
    RigidRegistrationTransform3D,
)
from holomed.registration.solver import HornRigidRegistrationSolver
from holomed.registration.transforms import transform_point


class RecoveryEvaluator:
    """Pure, stateless evaluation engine for candidate registration and post-activation consistency."""

    @staticmethod
    def stage_and_solve_candidate(
        session_id: str,
        plan_id: str,
        cloud: FiducialCloud,
        epoch_id: int,
        now_utc: Optional[str] = None,
    ) -> StagedRegistrationCandidate:
        """Stage candidate fiducials in isolated memory, solve 3D transform, and validate FRE <= 1.5 mm."""
        if len(cloud.pairs) < 3:
            raise RecoveryValidationError("Candidate fiducial cloud must contain at least 3 point pairs")

        # Solve candidate transform using pure static Horn solver
        transform = HornRigidRegistrationSolver.solve(
            cloud=cloud,
            source_frame=DEFAULT_PLAN_FRAME,
            target_frame=DEFAULT_PATIENT_TRACKER_FRAME,
            epoch_id=epoch_id,
        )

        # Compute FRE metrics
        quality = compute_registration_metrics(cloud, transform)

        if quality.fre_rms_mm > MAX_ALLOWED_RECOVERY_FRE_MM:
            raise RecoveryAccuracyError(
                f"Candidate registration FRE RMS ({quality.fre_rms_mm:.2f} mm) exceeds limit ({MAX_ALLOWED_RECOVERY_FRE_MM} mm)",
                details={"fre_rms_mm": quality.fre_rms_mm, "fre_max_mm": quality.fre_max_mm},
            )

        ts = now_utc or datetime.now(timezone.utc).isoformat()
        return StagedRegistrationCandidate(
            session_id=session_id,
            plan_id=plan_id,
            fiducial_cloud=cloud,
            candidate_transform=transform,
            quality_report=quality,
            staged_at_utc=ts,
        )

    @staticmethod
    def verify_checkpoint(
        candidate: StagedRegistrationCandidate,
        checkpoint_plan_mm: Tuple[float, float, float],
        checkpoint_measured_mm: Tuple[float, float, float],
        operator_id: str,
        now_utc: Optional[str] = None,
    ) -> RecoveryVerificationSnapshot:
        """Evaluate physical checkpoint drift against the candidate transform without mutating M13."""
        for val in checkpoint_plan_mm + checkpoint_measured_mm:
            if not isinstance(val, (int, float)) or not math.isfinite(val):
                raise RecoveryValidationError(f"Checkpoint coordinates must be finite floats, got {val!r}")

        # Compute Euclidean drift distance
        p_trans = transform_point(candidate.candidate_transform, checkpoint_plan_mm)
        dx = p_trans[0] - checkpoint_measured_mm[0]
        dy = p_trans[1] - checkpoint_measured_mm[1]
        dz = p_trans[2] - checkpoint_measured_mm[2]
        drift_mm = math.sqrt(dx * dx + dy * dy + dz * dz)

        ts = now_utc or datetime.now(timezone.utc).isoformat()
        vid = f"rec_verif_{uuid.uuid4().hex[:12]}"

        if drift_mm > MAX_ALLOWED_RECOVERY_DRIFT_MM:
            raise RecoveryVerificationError(
                f"Candidate checkpoint drift ({drift_mm:.2f} mm) exceeds recovery threshold ({MAX_ALLOWED_RECOVERY_DRIFT_MM} mm)",
                details={"drift_error_mm": drift_mm},
            )

        return RecoveryVerificationSnapshot(
            verification_id=vid,
            session_id=candidate.session_id,
            operator_id=operator_id,
            measured_drift_error_mm=drift_mm,
            passed=True,
            verified_at_utc=ts,
        )

    @staticmethod
    def validate_authorization(
        auth: RecoveryAuthorization,
        now_utc: Optional[str] = None,
    ) -> None:
        """Validate operator authorization record and temporal freshness."""
        eval_dt = (
            datetime.fromisoformat(now_utc.replace("Z", "+00:00"))
            if now_utc is not None
            else datetime.now(timezone.utc)
        )
        try:
            auth_dt = datetime.fromisoformat(auth.timestamp_utc.replace("Z", "+00:00"))
        except Exception as e:
            raise RecoveryAuthorizationError(f"Malformed authorization timestamp: {auth.timestamp_utc!r}") from e

        age_ms = (eval_dt - auth_dt).total_seconds() * 1000.0

        if age_ms > MAX_AUTHORIZATION_AGE_MS:
            raise RecoveryAuthorizationError(
                f"Operator authorization is stale: age {age_ms:.1f} ms exceeds limit {MAX_AUTHORIZATION_AGE_MS} ms"
            )

        if age_ms < -MAX_FUTURE_TIMESTAMP_SKEW_MS:
            raise RecoveryAuthorizationError(
                f"Operator authorization timestamp is future-dated: skew {-age_ms:.1f} ms exceeds limit {MAX_FUTURE_TIMESTAMP_SKEW_MS} ms"
            )

    @staticmethod
    def verify_post_activation_consistency(
        session_id: str,
        epoch_id: int,
        registration_service: Any,
        drift_service: Optional[Any] = None,
        proximity_service: Optional[Any] = None,
        navigation_service: Optional[Any] = None,
    ) -> None:
        """Verify that M13 and all attached downstream services are aligned to active verified registration."""
        # 1. M13 Registration Service Check
        if registration_service is None:
            raise RecoveryConsistencyError("RegistrationService is unavailable")

        reg = registration_service.get_registration(session_id)
        if reg is None or reg.state != RegistrationState.VERIFIED or reg.transform is None:
            raise RecoveryConsistencyError(
                f"M13 registration for session {session_id!r} is not VERIFIED after activation"
            )
        if reg.epoch_id != epoch_id:
            raise RecoveryConsistencyError(
                f"M13 registration epoch ({reg.epoch_id}) does not match runtime epoch ({epoch_id})"
            )

        # 2. M16 Drift Service Check
        if drift_service is not None:
            drift_status = drift_service.get_drift_status(session_id)
            if drift_status.state.value != "READY":
                raise RecoveryConsistencyError(
                    f"M16 DriftService state is {drift_status.state.value!r}, expected 'READY'"
                )
            if drift_status.epoch_id != epoch_id:
                raise RecoveryConsistencyError(
                    f"M16 DriftService epoch ({drift_status.epoch_id}) does not match runtime epoch ({epoch_id})"
                )

        # 3. M15 Proximity Service Check
        if proximity_service is not None:
            prox_status = proximity_service.get_proximity_status(session_id)
            if prox_status.state.value != "SAFE":
                raise RecoveryConsistencyError(
                    f"M15 ProximityService state is {prox_status.state.value!r}, expected 'SAFE'"
                )
            if prox_status.epoch_id != epoch_id:
                raise RecoveryConsistencyError(
                    f"M15 ProximityService epoch ({prox_status.epoch_id}) does not match runtime epoch ({epoch_id})"
                )

        # 4. M14 Navigation Service Check
        if navigation_service is not None:
            nav_status = navigation_service.get_navigation_status(session_id)
            if nav_status.state.value != "IDLE":
                raise RecoveryConsistencyError(
                    f"M14 NavigationService state is {nav_status.state.value!r}, expected 'IDLE'"
                )
            if nav_status.epoch_id != epoch_id:
                raise RecoveryConsistencyError(
                    f"M14 NavigationService epoch ({nav_status.epoch_id}) does not match runtime epoch ({epoch_id})"
                )
            if not nav_status.has_bound_trajectory:
                raise RecoveryConsistencyError(
                    f"M14 NavigationService for session {session_id!r} does not have a bound trajectory"
                )
