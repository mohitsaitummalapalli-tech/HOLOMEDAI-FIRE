# -*- coding: utf-8 -*-
"""Pure Stateless Evaluation Engine & Precedence Rules for M18 Safety Gate."""

from __future__ import annotations

from typing import Any, List, Optional

from holomed.safety_gate.models import (
    GateDecision,
    GateReasonCode,
    GateRequest,
    GateSeverity,
    GateStatusRecord,
    SafetyGateAction,
    SubsystemSnapshot,
)


class SafetyGateEvaluator:
    """Pure mathematical and logical decision engine aggregating multi-service safety signals."""

    @staticmethod
    def evaluate(
        request: GateRequest,
        epoch_id: int,
        workflow_service: Optional[Any] = None,
        registration_service: Optional[Any] = None,
        navigation_service: Optional[Any] = None,
        proximity_service: Optional[Any] = None,
        drift_service: Optional[Any] = None,
        recovery_service: Optional[Any] = None,
    ) -> GateStatusRecord:
        """Evaluate cross-service safety state and produce authoritative GateStatusRecord."""
        session_id = request.session_id
        action = request.action
        snapshots: List[SubsystemSnapshot] = []

        # ---------------------------------------------------------------------
        # 1. Query Subsystem States via Public Handles
        # ---------------------------------------------------------------------

        # M15 Proximity Protection
        m15_state: Optional[str] = None
        m15_epoch: Optional[int] = None
        if proximity_service is not None:
            try:
                prox_status = proximity_service.get_proximity_status(session_id)
                m15_state = prox_status.state.value
                m15_epoch = prox_status.epoch_id
                snapshots.append(
                    SubsystemSnapshot(
                        subsystem_name="proximity_service",
                        state=m15_state,
                        epoch_id=m15_epoch,
                        is_nominal=m15_state == "SAFE",
                        details={"monitored_zones": prox_status.monitored_zone_count},
                    )
                )
            except Exception:
                m15_state = "UNKNOWN"

        # M16 Landmark Drift
        m16_state: Optional[str] = None
        m16_epoch: Optional[int] = None
        if drift_service is not None:
            try:
                drift_status = drift_service.get_drift_status(session_id)
                m16_state = drift_status.state.value
                m16_epoch = drift_status.epoch_id
                snapshots.append(
                    SubsystemSnapshot(
                        subsystem_name="drift_service",
                        state=m16_state,
                        epoch_id=m16_epoch,
                        is_nominal=m16_state in ("READY", "STABLE"),
                        details={"bound_landmarks": drift_status.bound_landmark_count},
                    )
                )
            except Exception:
                m16_state = "UNKNOWN"

        # M17 Spatial Recovery
        m17_state: Optional[str] = None
        m17_rev: Optional[int] = None
        if recovery_service is not None:
            try:
                rec_status = recovery_service.get_recovery_status(session_id)
                m17_state = rec_status.state.value
                m17_rev = rec_status.registration_revision
                snapshots.append(
                    SubsystemSnapshot(
                        subsystem_name="recovery_service",
                        state=m17_state,
                        epoch_id=None,  # registration_revision is NOT an epoch
                        is_nominal=m17_state in ("IDLE", "ACTIVATED"),
                        details={"registration_revision": m17_rev},
                    )
                )
            except Exception:
                m17_state = "UNKNOWN"

        # M13 Registration
        m13_state: Optional[str] = None
        m13_epoch: Optional[int] = None
        if registration_service is not None:
            try:
                reg_record = registration_service.get_registration(session_id)
                if reg_record is not None:
                    m13_state = reg_record.state.value
                    m13_epoch = reg_record.epoch_id
                    snapshots.append(
                        SubsystemSnapshot(
                            subsystem_name="registration_service",
                            state=m13_state,
                            epoch_id=m13_epoch,
                            is_nominal=m13_state == "VERIFIED",
                            details={"locked": reg_record.locked},
                        )
                    )
                else:
                    m13_state = "UNINITIALIZED"
            except Exception:
                m13_state = "UNKNOWN"

        # M10 Workflow
        m10_state: Optional[str] = None
        if workflow_service is not None:
            try:
                wf_snapshot = workflow_service.get_workflow_state(session_id)
                if wf_snapshot is not None:
                    m10_state = wf_snapshot.current_phase.value
                    snapshots.append(
                        SubsystemSnapshot(
                            subsystem_name="workflow_service",
                            state=m10_state,
                            epoch_id=None,
                            is_nominal=m10_state not in ("RECOVERY_REQUIRED", "ABORTED"),
                            details={"is_terminal": wf_snapshot.is_terminal},
                        )
                    )
                else:
                    m10_state = "UNINITIALIZED"
            except Exception:
                m10_state = "UNKNOWN"

        # M14 Navigation
        m14_state: Optional[str] = None
        m14_epoch: Optional[int] = None
        has_bound_traj: bool = False
        if navigation_service is not None:
            try:
                nav_status = navigation_service.get_navigation_status(session_id)
                m14_state = nav_status.state.value
                m14_epoch = nav_status.epoch_id
                has_bound_traj = nav_status.has_bound_trajectory
                snapshots.append(
                    SubsystemSnapshot(
                        subsystem_name="navigation_service",
                        state=m14_state,
                        epoch_id=m14_epoch,
                        is_nominal=m14_state in ("IDLE", "TRACKING"),
                        details={"has_bound_trajectory": has_bound_traj},
                    )
                )
            except Exception:
                m14_state = "UNKNOWN"

        # ---------------------------------------------------------------------
        # 2. Strict Precedence Hierarchy Evaluation
        # ---------------------------------------------------------------------

        # Precedence 1: M15 Critical Exclusion Zone Breach
        if m15_state in ("CRITICAL_BREACH", "INTERLOCKED"):
            return GateStatusRecord(
                session_id=session_id,
                decision=GateDecision.DENIED_CRITICAL,
                severity=GateSeverity.CRITICAL,
                reason_code=GateReasonCode.CRITICAL_EXCLUSION_ZONE_BREACH,
                action=action,
                sequence_number=request.sequence_number,
                subsystem_snapshots=tuple(snapshots),
                evaluated_at_utc=request.now_utc,
            )

        # Precedence 2: M16 Integrity Interlock (Telemetry/Sensor Corruption)
        if m16_state == "INTERLOCKED":
            return GateStatusRecord(
                session_id=session_id,
                decision=GateDecision.DENIED_INTERLOCKED,
                severity=GateSeverity.BLOCKING,
                reason_code=GateReasonCode.LANDMARK_INTEGRITY_INTERLOCKED,
                action=action,
                sequence_number=request.sequence_number,
                subsystem_snapshots=tuple(snapshots),
                evaluated_at_utc=request.now_utc,
            )

        # Precedence 3: M16 Drift Exceeded (Registration Tracking Breach)
        if m16_state == "DRIFT_EXCEEDED":
            if action == SafetyGateAction.RECOVERY_REORIENTATION:
                # Permits recovery reorientation specifically so new landmarks can be captured
                return GateStatusRecord(
                    session_id=session_id,
                    decision=GateDecision.PERMITTED_WITH_CAUTION,
                    severity=GateSeverity.WARNING,
                    reason_code=GateReasonCode.LANDMARK_DRIFT_EXCEEDED,
                    action=action,
                    sequence_number=request.sequence_number,
                    subsystem_snapshots=tuple(snapshots),
                    evaluated_at_utc=request.now_utc,
                )
            return GateStatusRecord(
                session_id=session_id,
                decision=GateDecision.DENIED_INTERLOCKED,
                severity=GateSeverity.BLOCKING,
                reason_code=GateReasonCode.LANDMARK_DRIFT_EXCEEDED,
                action=action,
                sequence_number=request.sequence_number,
                subsystem_snapshots=tuple(snapshots),
                evaluated_at_utc=request.now_utc,
            )

        # Precedence 4: M17 Spatial Recovery Failed or Blocked
        if m17_state in ("FAILED", "BLOCKED"):
            return GateStatusRecord(
                session_id=session_id,
                decision=GateDecision.DENIED_INTERLOCKED,
                severity=GateSeverity.BLOCKING,
                reason_code=GateReasonCode.SPATIAL_RECOVERY_FAILED,
                action=action,
                sequence_number=request.sequence_number,
                subsystem_snapshots=tuple(snapshots),
                evaluated_at_utc=request.now_utc,
            )

        # Precedence 4: Runtime Epoch Consistency Check across attached subsystems
        for snap in snapshots:
            if snap.epoch_id is not None and snap.epoch_id != epoch_id:
                return GateStatusRecord(
                    session_id=session_id,
                    decision=GateDecision.DENIED_INTERLOCKED,
                    severity=GateSeverity.BLOCKING,
                    reason_code=GateReasonCode.RUNTIME_EPOCH_MISMATCH,
                    action=action,
                    sequence_number=request.sequence_number,
                    subsystem_snapshots=tuple(snapshots),
                    evaluated_at_utc=request.now_utc,
                )

        # Precedence 5: M13 Registration Unverified
        if m13_state != "VERIFIED":
            if action == SafetyGateAction.RECOVERY_REORIENTATION:
                return GateStatusRecord(
                    session_id=session_id,
                    decision=GateDecision.PERMITTED_CLEAR,
                    severity=GateSeverity.NONE,
                    reason_code=GateReasonCode.NONE,
                    action=action,
                    sequence_number=request.sequence_number,
                    subsystem_snapshots=tuple(snapshots),
                    evaluated_at_utc=request.now_utc,
                )
            if action == SafetyGateAction.TRAJECTORY_ALIGNMENT:
                return GateStatusRecord(
                    session_id=session_id,
                    decision=GateDecision.PERMITTED_WITH_CAUTION,
                    severity=GateSeverity.WARNING,
                    reason_code=GateReasonCode.REGISTRATION_UNVERIFIED,
                    action=action,
                    sequence_number=request.sequence_number,
                    subsystem_snapshots=tuple(snapshots),
                    evaluated_at_utc=request.now_utc,
                )
            return GateStatusRecord(
                session_id=session_id,
                decision=GateDecision.DENIED_INTERLOCKED,
                severity=GateSeverity.BLOCKING,
                reason_code=GateReasonCode.REGISTRATION_UNVERIFIED,
                action=action,
                sequence_number=request.sequence_number,
                subsystem_snapshots=tuple(snapshots),
                evaluated_at_utc=request.now_utc,
            )

        # Precedence 6: M10 Workflow Phase Blocked
        if m10_state in ("RECOVERY_REQUIRED", "ABORTED"):
            return GateStatusRecord(
                session_id=session_id,
                decision=GateDecision.DENIED_INTERLOCKED,
                severity=GateSeverity.BLOCKING,
                reason_code=GateReasonCode.WORKFLOW_PHASE_BLOCKED,
                action=action,
                sequence_number=request.sequence_number,
                subsystem_snapshots=tuple(snapshots),
                evaluated_at_utc=request.now_utc,
            )

        # Precedence 7: M14 Navigation Interlocked or Trajectory Missing
        if action == SafetyGateAction.TOOL_NAVIGATION:
            if m14_state == "INTERLOCKED" or not has_bound_traj:
                return GateStatusRecord(
                    session_id=session_id,
                    decision=GateDecision.DENIED_INTERLOCKED,
                    severity=GateSeverity.BLOCKING,
                    reason_code=GateReasonCode.NAVIGATION_UNAVAILABLE,
                    action=action,
                    sequence_number=request.sequence_number,
                    subsystem_snapshots=tuple(snapshots),
                    evaluated_at_utc=request.now_utc,
                )

        # Precedence 8: Non-Critical Warnings
        if m15_state == "WARNING":
            return GateStatusRecord(
                session_id=session_id,
                decision=GateDecision.PERMITTED_WITH_CAUTION,
                severity=GateSeverity.WARNING,
                reason_code=GateReasonCode.APPROACHING_MARGIN,
                action=action,
                sequence_number=request.sequence_number,
                subsystem_snapshots=tuple(snapshots),
                evaluated_at_utc=request.now_utc,
            )

        if m16_state == "WARNING":
            return GateStatusRecord(
                session_id=session_id,
                decision=GateDecision.PERMITTED_WITH_CAUTION,
                severity=GateSeverity.WARNING,
                reason_code=GateReasonCode.LANDMARK_DRIFT_WARNING,
                action=action,
                sequence_number=request.sequence_number,
                subsystem_snapshots=tuple(snapshots),
                evaluated_at_utc=request.now_utc,
            )

        if m14_state == "DEVIATION_EXCEEDED" and action == SafetyGateAction.TOOL_NAVIGATION:
            return GateStatusRecord(
                session_id=session_id,
                decision=GateDecision.PERMITTED_WITH_CAUTION,
                severity=GateSeverity.WARNING,
                reason_code=GateReasonCode.TRACKING_DEVIATION_WARNING,
                action=action,
                sequence_number=request.sequence_number,
                subsystem_snapshots=tuple(snapshots),
                evaluated_at_utc=request.now_utc,
            )

        # Precedence 9: All Nominal
        return GateStatusRecord(
            session_id=session_id,
            decision=GateDecision.PERMITTED_CLEAR,
            severity=GateSeverity.NONE,
            reason_code=GateReasonCode.NONE,
            action=action,
            sequence_number=request.sequence_number,
            subsystem_snapshots=tuple(snapshots),
            evaluated_at_utc=request.now_utc,
        )
