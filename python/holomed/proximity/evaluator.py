# -*- coding: utf-8 -*-
"""Proximity Evaluator Engine for M15 Safety Exclusion Zone Monitoring.

Pure, deterministic evaluation of instrument capsule geometry against
registered safety exclusion zones with dynamic effective radius,
rate-of-approach calculation, and worst-severity aggregation.
"""

from __future__ import annotations

from datetime import datetime, timezone
import math
from typing import Dict, List, Optional, Tuple
import uuid

from holomed.planning.models import SafetyExclusionZone
from holomed.proximity.constants import (
    DEFAULT_PATIENT_TRACKER_FRAME,
    MAX_RATE_DELTA_TIME_S,
)
from holomed.proximity.exceptions import (
    ProximityInterlockError,
    ProximityValidationError,
)
from holomed.proximity.geometry import (
    capsule_to_sphere_clearance,
    compute_effective_radius,
    point_to_sphere_clearance,
)
from holomed.proximity.models import (
    ProximityEvaluationRecord,
    ProximitySeverity,
    ProximityState,
    ToolClearanceGeometry,
    ZoneBreachRecord,
    proximity_severity_to_interlock,
)
from holomed.workflow.models import InterlockSeverity, SafetyInterlock


# Severity ordering for worst-case aggregation
_SEVERITY_RANK: Dict[ProximitySeverity, int] = {
    ProximitySeverity.WARNING: 0,
    ProximitySeverity.BLOCKING: 1,
    ProximitySeverity.CRITICAL: 2,
}

_STATE_RANK: Dict[ProximityState, int] = {
    ProximityState.CLEAR: 0,
    ProximityState.WARNING: 1,
    ProximityState.BREACHED: 2,
    ProximityState.INTERLOCKED: 3,
}

# Map M12/M10 InterlockSeverity to M15 ProximitySeverity
_INTERLOCK_TO_PROXIMITY_SEVERITY: Dict[InterlockSeverity, ProximitySeverity] = {
    InterlockSeverity.INFO: ProximitySeverity.WARNING,
    InterlockSeverity.WARNING: ProximitySeverity.WARNING,
    InterlockSeverity.BLOCKING: ProximitySeverity.BLOCKING,
    InterlockSeverity.CRITICAL: ProximitySeverity.CRITICAL,
}


class ProximityEvaluator:
    """Pure deterministic proximity evaluation engine.

    Evaluates a ToolClearanceGeometry (capsule model) against a set of
    SafetyExclusionZone spheres, computing clearance, effective radius,
    approach rate, state classification, and worst-severity aggregation.
    """

    @classmethod
    def evaluate(
        cls,
        tool_geom: ToolClearanceGeometry,
        zones: Tuple[SafetyExclusionZone, ...],
        registration_error_mm: float,
        static_margin_mm: float,
        previous_clearances: Optional[Dict[str, Tuple[float, str]]] = None,
        now_utc: Optional[str] = None,
    ) -> ProximityEvaluationRecord:
        """Evaluate proximity of the tool capsule against all exclusion zones.

        Args:
            tool_geom: Validated ToolClearanceGeometry (capsule model).
            zones: Tuple of SafetyExclusionZone from M12 planning.
            registration_error_mm: Non-negative conservative error bound from M13.
            static_margin_mm: Non-negative static safety margin.
            previous_clearances: Dict[zone_id -> (clearance_mm, timestamp_utc)] for rate calculation.
            now_utc: ISO-8601 UTC evaluation time. Defaults to current UTC.

        Returns:
            ProximityEvaluationRecord with per-zone results and worst-case aggregation.
        """
        eval_time_str = now_utc if now_utc is not None else datetime.now(timezone.utc).isoformat()

        # Validate registration_error_mm and static_margin_mm
        if not isinstance(registration_error_mm, (int, float)) or not math.isfinite(registration_error_mm) or registration_error_mm < 0.0:
            raise ProximityValidationError(
                f"registration_error_mm must be non-negative finite float, got {registration_error_mm!r}"
            )
        if not isinstance(static_margin_mm, (int, float)) or not math.isfinite(static_margin_mm) or static_margin_mm < 0.0:
            raise ProximityValidationError(
                f"static_margin_mm must be non-negative finite float, got {static_margin_mm!r}"
            )

        # Validate coordinate frame
        if tool_geom.coordinate_frame != DEFAULT_PATIENT_TRACKER_FRAME:
            raise ProximityValidationError(
                f"ToolClearanceGeometry coordinate_frame '{tool_geom.coordinate_frame}' "
                f"does not match expected '{DEFAULT_PATIENT_TRACKER_FRAME}'"
            )

        zone_results: List[ZoneBreachRecord] = []
        worst_state = ProximityState.CLEAR
        worst_severity: Optional[ProximitySeverity] = None

        for zone in zones:
            # 1. Compute effective radius
            r_effective = compute_effective_radius(
                zone_bounding_radius_mm=zone.bounding_radius_mm,
                static_margin_mm=static_margin_mm,
                registration_error_mm=registration_error_mm,
                tracking_uncertainty_mm=tool_geom.tracking_uncertainty_mm,
            )

            # 2. Compute raw clearance (capsule-to-sphere)
            raw_clearance = capsule_to_sphere_clearance(
                tip_position=tool_geom.tip_position_mm,
                shaft_direction=tool_geom.shaft_direction,
                shaft_length=tool_geom.shaft_length_mm,
                shaft_radius=tool_geom.shaft_radius_mm,
                sphere_center=zone.center_point_mm,
                sphere_radius=zone.bounding_radius_mm,
            )

            # 3. Compute effective clearance using full effective radius (minus bounding radius since raw already includes it)
            effective_clearance = capsule_to_sphere_clearance(
                tip_position=tool_geom.tip_position_mm,
                shaft_direction=tool_geom.shaft_direction,
                shaft_length=tool_geom.shaft_length_mm,
                shaft_radius=tool_geom.shaft_radius_mm,
                sphere_center=zone.center_point_mm,
                sphere_radius=r_effective,
            )

            # 4. Compute approach rate
            approach_rate = 0.0
            if previous_clearances is not None and zone.zone_id in previous_clearances:
                prev_clearance, prev_ts = previous_clearances[zone.zone_id]
                try:
                    eval_dt = datetime.fromisoformat(eval_time_str.replace("Z", "+00:00"))
                    prev_dt = datetime.fromisoformat(prev_ts.replace("Z", "+00:00"))
                    delta_time_s = (eval_dt - prev_dt).total_seconds()

                    if delta_time_s <= 0.0:
                        # Reject non-positive delta time -> rate stays 0.0
                        approach_rate = 0.0
                    elif delta_time_s > MAX_RATE_DELTA_TIME_S:
                        # Large time gap -> reset rate to 0.0 (not a breach)
                        approach_rate = 0.0
                    else:
                        # approach_rate = delta_clearance / delta_time
                        # Negative clearance change = approaching (positive rate)
                        delta_clearance = prev_clearance - effective_clearance
                        approach_rate = delta_clearance / delta_time_s
                except Exception:
                    # Timestamp parse failure -> rate reset
                    approach_rate = 0.0

            # 5. Classify state based on effective clearance
            zone_severity = _INTERLOCK_TO_PROXIMITY_SEVERITY.get(
                zone.severity_if_breached, ProximitySeverity.BLOCKING
            )

            if effective_clearance <= 0.0:
                # Breached: inside effective radius
                zone_state = ProximityState.BREACHED
            elif effective_clearance <= zone.min_clearance_mm:
                # Within warning band
                zone_state = ProximityState.WARNING
            else:
                # Clear
                zone_state = ProximityState.CLEAR

            zone_result = ZoneBreachRecord(
                zone_id=zone.zone_id,
                anatomical_structure=zone.anatomical_structure,
                raw_clearance_mm=raw_clearance,
                effective_radius_mm=r_effective,
                effective_clearance_mm=effective_clearance,
                severity=zone_severity,
                state=zone_state,
                approach_rate_mm_s=approach_rate,
                instrument_id=tool_geom.instrument_id,
                evaluated_at_utc=eval_time_str,
            )
            zone_results.append(zone_result)

            # 6. Worst-case aggregation
            if _STATE_RANK[zone_state] > _STATE_RANK[worst_state]:
                worst_state = zone_state

            if zone_state != ProximityState.CLEAR:
                if worst_severity is None or _SEVERITY_RANK[zone_severity] > _SEVERITY_RANK[worst_severity]:
                    worst_severity = zone_severity

        # 7. Safety determination
        is_safe = worst_state == ProximityState.CLEAR

        rec_id = f"prox_{uuid.uuid4().hex[:12]}"
        return ProximityEvaluationRecord(
            record_id=rec_id,
            session_id=tool_geom.session_id,
            instrument_id=tool_geom.instrument_id,
            epoch_id=tool_geom.epoch_id,
            zone_results=tuple(zone_results),
            worst_state=worst_state,
            worst_severity=worst_severity,
            is_safe=is_safe,
            evaluated_at_utc=eval_time_str,
        )

    @classmethod
    def generate_interlock(
        cls,
        evaluation: ProximityEvaluationRecord,
        session_id: str,
        epoch_id: int,
    ) -> Optional[SafetyInterlock]:
        """Generate an M10 SafetyInterlock from the evaluation result if unsafe.

        Returns None if the evaluation is CLEAR (safe).
        """
        if evaluation.is_safe:
            return None

        # Gather breached/warning zone details
        breach_details: List[str] = []
        for zr in evaluation.zone_results:
            if zr.state != ProximityState.CLEAR:
                breach_details.append(
                    f"{zr.zone_id}({zr.anatomical_structure}): "
                    f"clearance={zr.effective_clearance_mm:.2f}mm "
                    f"state={zr.state.value}"
                )

        reason = "; ".join(breach_details) if breach_details else "Proximity violation detected"

        # Map worst severity to interlock severity
        if evaluation.worst_severity is not None:
            interlock_severity = proximity_severity_to_interlock(evaluation.worst_severity)
        else:
            interlock_severity = InterlockSeverity.BLOCKING

        # Determine condition name
        if evaluation.worst_state == ProximityState.INTERLOCKED:
            condition = "PROXIMITY_INTERLOCKED"
        elif evaluation.worst_state == ProximityState.BREACHED:
            condition = "EXCLUSION_ZONE_BREACHED"
        else:
            condition = "PROXIMITY_WARNING"

        return SafetyInterlock(
            interlock_id=f"prox_lock_{uuid.uuid4().hex[:8]}",
            severity=interlock_severity,
            condition_name=condition,
            status=False,  # Tripped / Unsafe
            reason=reason,
            source_service="proximity_service",
            epoch_id=epoch_id,
            session_id=session_id,
        )
