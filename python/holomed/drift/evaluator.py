# -*- coding: utf-8 -*-
"""Deterministic Drift & Landmark Evaluation Engine for M16."""

from __future__ import annotations

from datetime import datetime, timezone
import math
from typing import Optional, Sequence
import uuid

from holomed.drift.constants import (
    DEFAULT_PATIENT_TRACKER_FRAME,
    MAX_ALLOWED_DRIFT_MM,
    MAX_FUTURE_TIMESTAMP_SKEW_MS,
    MAX_OBSERVATION_AGE_MS,
    MAX_TIP_JITTER_MM,
    MIN_DWELL_DURATION_MS,
    MIN_STABLE_SAMPLES,
    WARNING_DRIFT_THRESHOLD_MM,
)
from holomed.drift.exceptions import (
    DriftEpochMismatchError,
    DriftValidationError,
)
from holomed.drift.geometry import (
    compute_dwell_stability,
    compute_effective_tolerance,
    compute_euclidean_residual,
)
from holomed.drift.models import (
    DriftState,
    LandmarkDefinition,
    LandmarkObservation,
    LandmarkVerificationRecord,
)
from holomed.registration.models import RigidRegistrationTransform3D
from holomed.registration.transforms import transform_point
from holomed.workflow.models import InterlockSeverity, SafetyInterlock


class DriftEvaluator:
    """Deterministic evaluation engine for registration drift & landmark verification."""

    @classmethod
    def evaluate(
        cls,
        observation: LandmarkObservation,
        landmark: LandmarkDefinition,
        transform: RigidRegistrationTransform3D,
        dwell_history: Optional[Sequence[LandmarkObservation]] = None,
        now_utc: Optional[str] = None,
    ) -> LandmarkVerificationRecord:
        """Evaluate a physical pointer observation against a planned landmark and registration transform.

        Args:
            observation: Physical pointer LandmarkObservation.
            landmark: Planned LandmarkDefinition.
            transform: Active verified RigidRegistrationTransform3D from M13.
            dwell_history: Optional sequence of recent observations for dwell stability validation.
            now_utc: ISO-8601 UTC evaluation time. Defaults to current UTC.

        Returns:
            LandmarkVerificationRecord with residual error, state classification, and verification outcome.
        """
        eval_time_str = now_utc if now_utc is not None else datetime.now(timezone.utc).isoformat()
        eval_dt = datetime.fromisoformat(eval_time_str.replace("Z", "+00:00"))

        # 1. Validate epoch binding
        if observation.epoch_id != transform.epoch_id:
            raise DriftEpochMismatchError(
                f"Observation epoch ({observation.epoch_id}) does not match registration transform epoch ({transform.epoch_id})"
            )

        # 2. Validate landmark ID matching
        if observation.landmark_id != landmark.landmark_id:
            raise DriftValidationError(
                f"Observation landmark_id {observation.landmark_id!r} does not match definition {landmark.landmark_id!r}"
            )

        # 3. Validate coordinate frame
        if observation.coordinate_frame != DEFAULT_PATIENT_TRACKER_FRAME:
            raise DriftValidationError(
                f"Observation coordinate_frame '{observation.coordinate_frame}' does not match expected '{DEFAULT_PATIENT_TRACKER_FRAME}'"
            )

        # 4. Validate timestamp freshness and future skew
        obs_dt = datetime.fromisoformat(observation.timestamp_utc.replace("Z", "+00:00"))
        age_ms = (eval_dt - obs_dt).total_seconds() * 1000.0

        if age_ms > MAX_OBSERVATION_AGE_MS:
            raise DriftValidationError(
                f"Observation timestamp is stale: age {age_ms:.1f} ms exceeds limit {MAX_OBSERVATION_AGE_MS} ms"
            )

        if age_ms < -MAX_FUTURE_TIMESTAMP_SKEW_MS:
            raise DriftValidationError(
                f"Observation timestamp is future-dated: skew {-age_ms:.1f} ms exceeds limit {MAX_FUTURE_TIMESTAMP_SKEW_MS} ms"
            )

        # 5. Compute expected point in tracker frame using M13 public transform_point
        p_expected = transform_point(transform, landmark.planned_point_mm)

        # 6. Compute Euclidean residual error
        residual = compute_euclidean_residual(p_expected, observation.observed_point_mm)

        # 7. Compute effective tolerance
        effective_tol = compute_effective_tolerance(landmark.max_tolerance_mm, MAX_ALLOWED_DRIFT_MM)

        # 8. Confidence and uncertainty gating
        meets_confidence = observation.confidence >= landmark.min_confidence
        meets_uncertainty = observation.uncertainty_mm <= landmark.max_uncertainty_mm

        # 9. Dwell stability evaluation
        meets_stability = False
        if dwell_history is not None:
            all_samples = list(dwell_history) + [observation]
            if len(all_samples) >= MIN_STABLE_SAMPLES:
                first_dt = datetime.fromisoformat(all_samples[0].timestamp_utc.replace("Z", "+00:00"))
                dwell_duration_ms = (obs_dt - first_dt).total_seconds() * 1000.0
                if dwell_duration_ms >= MIN_DWELL_DURATION_MS:
                    points = [obs.observed_point_mm for obs in all_samples]
                    jitter = compute_dwell_stability(points)
                    if jitter <= MAX_TIP_JITTER_MM:
                        meets_stability = True

        # 10. State classification
        if not meets_confidence or not meets_uncertainty or not meets_stability:
            # Gating failure -> does not produce STABLE; classified based on residual or warning
            passed = False
            if residual > effective_tol:
                state = DriftState.DRIFT_EXCEEDED
            else:
                state = DriftState.WARNING
        else:
            if residual <= WARNING_DRIFT_THRESHOLD_MM:
                state = DriftState.STABLE
                passed = True
            elif residual <= effective_tol:
                state = DriftState.WARNING
                passed = False
            else:
                state = DriftState.DRIFT_EXCEEDED
                passed = False

        rec_id = f"drift_rec_{uuid.uuid4().hex[:12]}"
        return LandmarkVerificationRecord(
            record_id=rec_id,
            session_id=observation.session_id,
            landmark_id=observation.landmark_id,
            epoch_id=observation.epoch_id,
            expected_point_mm=p_expected,
            observed_point_mm=observation.observed_point_mm,
            residual_error_mm=residual,
            effective_tolerance_mm=effective_tol,
            state=state,
            passed=passed,
            evaluated_at_utc=eval_time_str,
        )

    @classmethod
    def generate_interlock(
        cls,
        record: LandmarkVerificationRecord,
        session_id: str,
        epoch_id: int,
    ) -> Optional[SafetyInterlock]:
        """Generate an M10 SafetyInterlock if the verification failed or breached tolerance."""
        if record.passed and record.state == DriftState.STABLE:
            return None

        if record.state == DriftState.DRIFT_EXCEEDED:
            condition = "REGISTRATION_DRIFT_EXCEEDED"
            reason = (
                f"Landmark {record.landmark_id!r} residual error ({record.residual_error_mm:.2f} mm) "
                f"exceeds effective tolerance ({record.effective_tolerance_mm:.2f} mm)"
            )
        elif record.state == DriftState.INTERLOCKED:
            condition = "REGISTRATION_DRIFT_INTERLOCKED"
            reason = f"Drift integrity failure on landmark {record.landmark_id!r}"
        else:
            condition = "LANDMARK_VERIFICATION_WARNING"
            reason = (
                f"Landmark {record.landmark_id!r} residual error ({record.residual_error_mm:.2f} mm) "
                f"in warning band (tolerance: {record.effective_tolerance_mm:.2f} mm)"
            )

        return SafetyInterlock(
            interlock_id=f"drift_lock_{uuid.uuid4().hex[:8]}",
            severity=InterlockSeverity.BLOCKING,
            condition_name=condition,
            status=False,  # Tripped / Unsafe
            reason=reason,
            source_service="drift_service",
            epoch_id=epoch_id,
            session_id=session_id,
        )
