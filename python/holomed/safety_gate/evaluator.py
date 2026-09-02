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
from holomed.tools.models import ToolSafetyClassification


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
        session_mismatch: bool = False

        # ---------------------------------------------------------------------
        # 1. Query Subsystem States via Public Handles
        # ---------------------------------------------------------------------

        # M15 Proximity Protection
        m15_state: Optional[str] = None
        m15_epoch: Optional[int] = None
        m15_rev: Optional[int] = None
        m15_ts: Optional[str] = None
        if proximity_service is not None:
            try:
                prox_status = proximity_service.get_proximity_status(session_id)
                if prox_status is not None:
                    sid = getattr(prox_status, "session_id", None)
                    if isinstance(sid, str) and sid != session_id:
                        session_mismatch = True
                    m15_state = prox_status.state.value if hasattr(prox_status.state, "value") else str(prox_status.state)
                    m15_epoch = prox_status.epoch_id if isinstance(getattr(prox_status, "epoch_id", None), int) else None
                    raw_rev = getattr(prox_status, "registration_revision", None) or getattr(prox_status, "recovery_revision", None)
                    if isinstance(raw_rev, int):
                        m15_rev = raw_rev
                    raw_ts = (
                        getattr(prox_status.last_evaluation, "evaluated_at_utc", None)
                        if getattr(prox_status, "last_evaluation", None)
                        else getattr(prox_status, "updated_at_utc", None)
                    )
                    if isinstance(raw_ts, str):
                        m15_ts = raw_ts
                    snapshots.append(
                        SubsystemSnapshot(
                            subsystem_name="proximity_service",
                            state=m15_state,
                            epoch_id=m15_epoch,
                            is_nominal=m15_state == "SAFE",
                            details={"monitored_zones": getattr(prox_status, "monitored_zone_count", 0)},
                        )
                    )
            except Exception:
                m15_state = "UNKNOWN"

        # M16 Landmark Drift
        m16_state: Optional[str] = None
        m16_epoch: Optional[int] = None
        m16_rev: Optional[int] = None
        m16_ts: Optional[str] = None
        if drift_service is not None:
            try:
                drift_status = drift_service.get_drift_status(session_id)
                if drift_status is not None:
                    sid = getattr(drift_status, "session_id", None)
                    if isinstance(sid, str) and sid != session_id:
                        session_mismatch = True
                    m16_state = drift_status.state.value if hasattr(drift_status.state, "value") else str(drift_status.state)
                    m16_epoch = drift_status.epoch_id if isinstance(getattr(drift_status, "epoch_id", None), int) else None
                    raw_rev = getattr(drift_status, "registration_revision", None) or getattr(drift_status, "recovery_revision", None)
                    if isinstance(raw_rev, int):
                        m16_rev = raw_rev
                    raw_ts = (
                        getattr(drift_status.last_verification, "evaluated_at_utc", None)
                        if getattr(drift_status, "last_verification", None)
                        else getattr(drift_status, "updated_at_utc", None)
                    )
                    if isinstance(raw_ts, str):
                        m16_ts = raw_ts
                    snapshots.append(
                        SubsystemSnapshot(
                            subsystem_name="drift_service",
                            state=m16_state,
                            epoch_id=m16_epoch,
                            is_nominal=m16_state in ("READY", "STABLE"),
                            details={"bound_landmarks": getattr(drift_status, "bound_landmark_count", 0)},
                        )
                    )
            except Exception:
                m16_state = "UNKNOWN"

        # M17 Spatial Recovery
        m17_state: Optional[str] = None
        m17_rev: Optional[int] = None
        m17_ts: Optional[str] = None
        if recovery_service is not None:
            try:
                rec_status = recovery_service.get_recovery_status(session_id)
                if rec_status is not None:
                    sid = getattr(rec_status, "session_id", None)
                    if isinstance(sid, str) and sid != session_id:
                        session_mismatch = True
                    m17_state = rec_status.state.value if hasattr(rec_status.state, "value") else str(rec_status.state)
                    raw_rev = getattr(rec_status, "registration_revision", None)
                    if isinstance(raw_rev, int):
                        m17_rev = raw_rev
                    raw_ts = getattr(rec_status, "updated_at_utc", None)
                    if isinstance(raw_ts, str):
                        m17_ts = raw_ts
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
        m13_rev: Optional[int] = None
        m13_ts: Optional[str] = None
        if registration_service is not None:
            try:
                reg_record = registration_service.get_registration(session_id)
                if reg_record is not None:
                    sid = getattr(reg_record, "session_id", None)
                    if isinstance(sid, str) and sid != session_id:
                        session_mismatch = True
                    m13_state = reg_record.state.value if hasattr(reg_record.state, "value") else str(reg_record.state)
                    m13_epoch = reg_record.epoch_id if isinstance(getattr(reg_record, "epoch_id", None), int) else None
                    raw_rev = getattr(reg_record, "revision", None) or getattr(reg_record, "registration_revision", None)
                    if isinstance(raw_rev, int):
                        m13_rev = raw_rev
                    raw_ts = getattr(reg_record, "verified_at_utc", None) or (
                        getattr(reg_record.transform, "created_at_utc", None) if getattr(reg_record, "transform", None) else None
                    )
                    if isinstance(raw_ts, str):
                        m13_ts = raw_ts
                    snapshots.append(
                        SubsystemSnapshot(
                            subsystem_name="registration_service",
                            state=m13_state,
                            epoch_id=m13_epoch,
                            is_nominal=m13_state == "VERIFIED",
                            details={"locked": getattr(reg_record, "locked", False)},
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
                    sid = getattr(wf_snapshot, "session_id", None)
                    if isinstance(sid, str) and sid != session_id:
                        session_mismatch = True
                    m10_state = (
                        wf_snapshot.current_phase.value
                        if hasattr(wf_snapshot.current_phase, "value")
                        else str(wf_snapshot.current_phase)
                    )
                    snapshots.append(
                        SubsystemSnapshot(
                            subsystem_name="workflow_service",
                            state=m10_state,
                            epoch_id=None,
                            is_nominal=m10_state not in ("RECOVERY_REQUIRED", "ABORTED"),
                            details={"is_terminal": getattr(wf_snapshot, "is_terminal", False)},
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
                if nav_status is not None:
                    sid = getattr(nav_status, "session_id", None)
                    if isinstance(sid, str) and sid != session_id:
                        session_mismatch = True
                    m14_state = nav_status.state.value if hasattr(nav_status.state, "value") else str(nav_status.state)
                    m14_epoch = nav_status.epoch_id if isinstance(getattr(nav_status, "epoch_id", None), int) else None
                    has_bound_traj = bool(getattr(nav_status, "has_bound_trajectory", False))
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

        # Precedence 0: Session Mismatch Check
        if session_mismatch:
            return GateStatusRecord(
                session_id=session_id,
                decision=GateDecision.DENIED_INTERLOCKED,
                severity=GateSeverity.BLOCKING,
                reason_code=GateReasonCode.SESSION_MISMATCH,
                action=action,
                sequence_number=request.sequence_number,
                subsystem_snapshots=tuple(snapshots),
                evaluated_at_utc=request.now_utc,
            )

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

        # ---------------------------------------------------------------------
        # WORKFLOW_RESUMPTION Dedicated Precedence Path
        # ---------------------------------------------------------------------
        if action == SafetyGateAction.WORKFLOW_RESUMPTION:
            # 1. M15 Proximity Check
            if m15_state not in ("SAFE", "CLEAR"):
                return GateStatusRecord(
                    session_id=session_id,
                    decision=GateDecision.DENIED_CRITICAL if m15_state == "CRITICAL_BREACH" else GateDecision.DENIED_INTERLOCKED,
                    severity=GateSeverity.CRITICAL if m15_state == "CRITICAL_BREACH" else GateSeverity.BLOCKING,
                    reason_code=GateReasonCode.CRITICAL_EXCLUSION_ZONE_BREACH,
                    action=action,
                    sequence_number=request.sequence_number,
                    subsystem_snapshots=tuple(snapshots),
                    evaluated_at_utc=request.now_utc,
                )
            if isinstance(m15_rev, int) and isinstance(m17_rev, int) and m15_rev != m17_rev:
                return GateStatusRecord(
                    session_id=session_id,
                    decision=GateDecision.DENIED_INTERLOCKED,
                    severity=GateSeverity.BLOCKING,
                    reason_code=GateReasonCode.CRITICAL_EXCLUSION_ZONE_BREACH,
                    action=action,
                    sequence_number=request.sequence_number,
                    subsystem_snapshots=tuple(snapshots),
                    evaluated_at_utc=request.now_utc,
                )
            if isinstance(m15_ts, str) and isinstance(m17_ts, str) and m15_ts < m17_ts:
                return GateStatusRecord(
                    session_id=session_id,
                    decision=GateDecision.DENIED_INTERLOCKED,
                    severity=GateSeverity.BLOCKING,
                    reason_code=GateReasonCode.CRITICAL_EXCLUSION_ZONE_BREACH,
                    action=action,
                    sequence_number=request.sequence_number,
                    subsystem_snapshots=tuple(snapshots),
                    evaluated_at_utc=request.now_utc,
                )

            # 2. M16 Landmark Drift Check: Must be STABLE
            if m16_state != "STABLE":
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
            if isinstance(m16_rev, int) and isinstance(m17_rev, int) and m16_rev != m17_rev:
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
            if isinstance(m16_ts, str) and isinstance(m17_ts, str) and m16_ts < m17_ts:
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

            # 3. M17 Spatial Recovery Check: Must be ACTIVATED
            if m17_state != "ACTIVATED":
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
            if isinstance(request.recovery_revision, int) and isinstance(m17_rev, int) and request.recovery_revision != m17_rev:
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

            # 4. M13 Registration Check: Must be VERIFIED
            if m13_state != "VERIFIED":
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
            if isinstance(m13_rev, int) and isinstance(m17_rev, int) and m13_rev != m17_rev:
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

            # 5. M10 Workflow Phase Check: Must currently be RECOVERY_REQUIRED
            if m10_state != "RECOVERY_REQUIRED":
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

            # 6. All checks passed for WORKFLOW_RESUMPTION -> PERMITTED_CLEAR
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
            if action == SafetyGateAction.TOOL_INVOCATION and request.safety_classification == ToolSafetyClassification.TELEMETRY_RECORDING:
                # Telemetry recording does not rely on anatomical registration
                pass
            else:
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
            if action == SafetyGateAction.TOOL_INVOCATION and request.safety_classification == ToolSafetyClassification.TELEMETRY_RECORDING:
                # Telemetry recording is specifically permitted during recovery/aborted diagnostics
                pass
            else:
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
