# -*- coding: utf-8 -*-
"""Real-Time Trajectory Deviation Evaluator for M14 Surgical Navigation."""

from __future__ import annotations

from datetime import datetime, timezone
import math
from typing import Optional
import uuid

from holomed.navigation.constants import (
    DEFAULT_PATIENT_TRACKER_FRAME,
    MAX_ALLOWED_POSE_AGE_MS,
    MAX_FUTURE_TIMESTAMP_SKEW_MS,
)
from holomed.navigation.exceptions import (
    NavigationFrameMismatchError,
    NavigationStalenessError,
)
from holomed.navigation.geometry import (
    compute_angular_deviation,
    compute_lateral_and_axial_deviation,
    compute_trajectory_geometry,
    quaternion_to_shaft_direction,
)
from holomed.navigation.models import (
    NavigationState,
    PositionClass,
    TrackedInstrumentPose,
    TrajectoryDeviationRecord,
)
from holomed.planning.models import TrajectoryPlan
from holomed.workflow.models import InterlockSeverity, SafetyInterlock


class TrajectoryDeviationEvaluator:
    """Pure, deterministic evaluation engine calculating 3D deviation metrics against registered trajectories."""

    @classmethod
    def evaluate(
        cls,
        pose: TrackedInstrumentPose,
        trajectory: TrajectoryPlan,
        now_utc: Optional[str] = None,
    ) -> TrajectoryDeviationRecord:
        """Evaluate a tracked instrument pose against a registered trajectory."""
        # 1. Coordinate frame verification
        if pose.coordinate_frame != DEFAULT_PATIENT_TRACKER_FRAME:
            raise NavigationFrameMismatchError(
                f"Pose coordinate frame '{pose.coordinate_frame}' does not match registered trajectory frame '{DEFAULT_PATIENT_TRACKER_FRAME}'"
            )

        # 2. Parse timestamps and evaluate freshness
        eval_time_str = now_utc if now_utc is not None else datetime.now(timezone.utc).isoformat()
        try:
            eval_dt = datetime.fromisoformat(eval_time_str.replace("Z", "+00:00"))
            pose_dt = datetime.fromisoformat(pose.timestamp_utc.replace("Z", "+00:00"))
        except Exception as e:
            raise NavigationStalenessError(f"Failed to parse evaluation timestamps: {e}") from e

        age_ms = (eval_dt - pose_dt).total_seconds() * 1000.0

        # Check temporal boundaries
        is_stale = age_ms > MAX_ALLOWED_POSE_AGE_MS or age_ms < -MAX_FUTURE_TIMESTAMP_SKEW_MS

        # 3. Compute 3D trajectory direction and length
        u_dir, length = compute_trajectory_geometry(trajectory.entry_point_mm, trajectory.target_point_mm)

        # 4. Extract tool shaft direction vector from quaternion
        v_shaft = quaternion_to_shaft_direction(pose.orientation_quaternion)

        # 5. Compute lateral distance and axial progress
        lateral_mm, axial_mm = compute_lateral_and_axial_deviation(pose.tip_position_mm, trajectory.entry_point_mm, u_dir)

        # 6. Compute shaft angular deviation
        angular_deg = compute_angular_deviation(u_dir, v_shaft)

        # 7. Geometric position classification
        if axial_mm < 0.0:
            pos_class = PositionClass.PRE_ENTRY
        elif axial_mm <= length:
            pos_class = PositionClass.IN_TRAJECTORY
        else:
            pos_class = PositionClass.POST_TARGET

        # 8. Multi-criteria Safety and State Synthesis
        rec_id = f"dev_{uuid.uuid4().hex[:12]}"

        # Staleness failure -> STALE
        if is_stale:
            reason = (
                f"Pose age ({age_ms:.1f} ms) exceeds limit ({MAX_ALLOWED_POSE_AGE_MS} ms)"
                if age_ms > MAX_ALLOWED_POSE_AGE_MS
                else f"Future timestamp clock skew ({age_ms:.1f} ms) exceeds limit (-{MAX_FUTURE_TIMESTAMP_SKEW_MS} ms)"
            )
            interlock = SafetyInterlock(
                interlock_id=f"nav_lock_{uuid.uuid4().hex[:8]}",
                severity=InterlockSeverity.BLOCKING,
                condition_name="STALE_POSE_DATA",
                status=False,
                reason=reason,
                source_service="navigation_service",
                epoch_id=pose.epoch_id,
                session_id=pose.session_id,
            )
            return TrajectoryDeviationRecord(
                record_id=rec_id,
                session_id=pose.session_id,
                trajectory_id=trajectory.trajectory_id,
                instrument_id=pose.instrument_id,
                lateral_deviation_mm=lateral_mm,
                angular_deviation_deg=angular_deg,
                axial_distance_mm=axial_mm,
                position_class=pos_class,
                pose_age_ms=age_ms,
                state=NavigationState.STALE,
                is_aligned=False,
                is_safe=False,
                safety_interlock=interlock,
                evaluated_at_utc=eval_time_str,
            )

        # Confidence / Uncertainty failure -> INTERLOCKED
        if pose.confidence < trajectory.min_confidence:
            interlock = SafetyInterlock(
                interlock_id=f"nav_lock_{uuid.uuid4().hex[:8]}",
                severity=InterlockSeverity.BLOCKING,
                condition_name="INSUFFICIENT_CONFIDENCE",
                status=False,
                reason=f"Confidence {pose.confidence:.2f} below threshold {trajectory.min_confidence:.2f}",
                source_service="navigation_service",
                epoch_id=pose.epoch_id,
                session_id=pose.session_id,
            )
            return TrajectoryDeviationRecord(
                record_id=rec_id,
                session_id=pose.session_id,
                trajectory_id=trajectory.trajectory_id,
                instrument_id=pose.instrument_id,
                lateral_deviation_mm=lateral_mm,
                angular_deviation_deg=angular_deg,
                axial_distance_mm=axial_mm,
                position_class=pos_class,
                pose_age_ms=age_ms,
                state=NavigationState.INTERLOCKED,
                is_aligned=False,
                is_safe=False,
                safety_interlock=interlock,
                evaluated_at_utc=eval_time_str,
            )

        if pose.uncertainty > trajectory.max_uncertainty:
            interlock = SafetyInterlock(
                interlock_id=f"nav_lock_{uuid.uuid4().hex[:8]}",
                severity=InterlockSeverity.BLOCKING,
                condition_name="EXCESSIVE_UNCERTAINTY",
                status=False,
                reason=f"Uncertainty {pose.uncertainty:.2f} exceeds threshold {trajectory.max_uncertainty:.2f}",
                source_service="navigation_service",
                epoch_id=pose.epoch_id,
                session_id=pose.session_id,
            )
            return TrajectoryDeviationRecord(
                record_id=rec_id,
                session_id=pose.session_id,
                trajectory_id=trajectory.trajectory_id,
                instrument_id=pose.instrument_id,
                lateral_deviation_mm=lateral_mm,
                angular_deviation_deg=angular_deg,
                axial_distance_mm=axial_mm,
                position_class=pos_class,
                pose_age_ms=age_ms,
                state=NavigationState.INTERLOCKED,
                is_aligned=False,
                is_safe=False,
                safety_interlock=interlock,
                evaluated_at_utc=eval_time_str,
            )

        # Tolerance check (Lateral & Angular)
        lateral_exceeded = lateral_mm > trajectory.max_lateral_deviation_mm
        angular_exceeded = angular_deg > trajectory.max_angular_deviation_deg
        post_target_overextended = pos_class == PositionClass.POST_TARGET

        if lateral_exceeded or angular_exceeded or post_target_overextended:
            reasons = []
            if lateral_exceeded:
                reasons.append(f"Lateral deviation {lateral_mm:.2f} mm > {trajectory.max_lateral_deviation_mm:.2f} mm")
            if angular_exceeded:
                reasons.append(f"Angular deviation {angular_deg:.1f} deg > {trajectory.max_angular_deviation_deg:.1f} deg")
            if post_target_overextended:
                reasons.append(f"Instrument tip passed target point (axial {axial_mm:.1f} mm > length {length:.1f} mm)")

            interlock = SafetyInterlock(
                interlock_id=f"nav_lock_{uuid.uuid4().hex[:8]}",
                severity=InterlockSeverity.BLOCKING,
                condition_name="TRAJECTORY_DEVIATION_EXCEEDED",
                status=False,
                reason="; ".join(reasons),
                source_service="navigation_service",
                epoch_id=pose.epoch_id,
                session_id=pose.session_id,
            )
            return TrajectoryDeviationRecord(
                record_id=rec_id,
                session_id=pose.session_id,
                trajectory_id=trajectory.trajectory_id,
                instrument_id=pose.instrument_id,
                lateral_deviation_mm=lateral_mm,
                angular_deviation_deg=angular_deg,
                axial_distance_mm=axial_mm,
                position_class=pos_class,
                pose_age_ms=age_ms,
                state=NavigationState.DEVIATED,
                is_aligned=False,
                is_safe=False,
                safety_interlock=interlock,
                evaluated_at_utc=eval_time_str,
            )

        # Tolerances and quality checks passed!
        if pos_class == PositionClass.PRE_ENTRY:
            # Pre-entry approach: safe approach, but not in trajectory yet
            interlock = SafetyInterlock(
                interlock_id=f"nav_info_{uuid.uuid4().hex[:8]}",
                severity=InterlockSeverity.INFO,
                condition_name="PRE_ENTRY_APPROACH",
                status=True,
                reason=f"Instrument approaching entry point (axial distance {axial_mm:.1f} mm)",
                source_service="navigation_service",
                epoch_id=pose.epoch_id,
                session_id=pose.session_id,
            )
            return TrajectoryDeviationRecord(
                record_id=rec_id,
                session_id=pose.session_id,
                trajectory_id=trajectory.trajectory_id,
                instrument_id=pose.instrument_id,
                lateral_deviation_mm=lateral_mm,
                angular_deviation_deg=angular_deg,
                axial_distance_mm=axial_mm,
                position_class=pos_class,
                pose_age_ms=age_ms,
                state=NavigationState.PRE_ENTRY,
                is_aligned=False,  # PRE_ENTRY is not active alignment
                is_safe=True,      # Safe approach
                safety_interlock=interlock,
                evaluated_at_utc=eval_time_str,
            )

        # IN_TRAJECTORY and perfectly aligned!
        interlock = SafetyInterlock(
            interlock_id=f"nav_ok_{uuid.uuid4().hex[:8]}",
            severity=InterlockSeverity.INFO,
            condition_name="TRAJECTORY_ALIGNED",
            status=True,
            reason=f"Instrument aligned within tolerances (lateral {lateral_mm:.2f} mm, angular {angular_deg:.1f} deg)",
            source_service="navigation_service",
            epoch_id=pose.epoch_id,
            session_id=pose.session_id,
        )
        return TrajectoryDeviationRecord(
            record_id=rec_id,
            session_id=pose.session_id,
            trajectory_id=trajectory.trajectory_id,
            instrument_id=pose.instrument_id,
            lateral_deviation_mm=lateral_mm,
            angular_deviation_deg=angular_deg,
            axial_distance_mm=axial_mm,
            position_class=pos_class,
            pose_age_ms=age_ms,
            state=NavigationState.ALIGNED,
            is_aligned=True,
            is_safe=True,
            safety_interlock=interlock,
            evaluated_at_utc=eval_time_str,
        )
