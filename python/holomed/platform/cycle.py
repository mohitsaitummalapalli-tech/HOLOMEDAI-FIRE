# -*- coding: utf-8 -*-
"""Synchronous Deterministic End-to-End Cycle Coordinator (D251, D252)."""

from __future__ import annotations

import time
from typing import Any, Mapping, Optional
import uuid

from holomed.platform.exceptions import (
    PlatformCapacityError,
    PlatformCycleError,
    PlatformEpochMismatchError,
)
from holomed.platform.models import (
    MAX_CYCLE_HISTORY,
    CycleStatus,
    CycleSummary,
)
from holomed.platform.session import SessionManager
from holomed.runtime.service import IService


class CycleCoordinator:
    """Executes synchronized end-to-end cycles across all platform domain services."""

    def __init__(self, epoch_id: int = 0) -> None:
        self._epoch_id = epoch_id
        self._cycle_history: list[CycleSummary] = []

    @property
    def cycle_history(self) -> tuple[CycleSummary, ...]:
        return tuple(self._cycle_history)

    def execute_cycle(
        self,
        session_manager: SessionManager,
        session_id: str,
        sequence_number: int,
        services: Mapping[str, IService],
        cycle_params: Optional[Mapping[str, Any]] = None,
    ) -> CycleSummary:
        """Execute one complete synchronous cycle across M01–M07 services (D251)."""
        # 1. Validate sequence and advance session
        session_manager.validate_and_advance_sequence(session_id, self._epoch_id, sequence_number)

        cycle_id = str(uuid.uuid4())
        start_time = time.perf_counter()
        phases_completed: list[str] = []
        status = CycleStatus.COMPLETED
        fused_count = 0
        tool_count = 0
        xr_nodes = 0
        confidence = 1.0
        uncertainty = 0.0
        diagnostic_msg: Optional[str] = None

        # Phase 1: Ingress & Phase 2: Perception
        # Check if Vision / Audio services are present and started
        vision_srv = services.get("vision_service")
        audio_srv = services.get("audio_service")
        if vision_srv is None or audio_srv is None:
            status = CycleStatus.DEGRADED
            confidence = 0.5
            uncertainty = 0.5
            diagnostic_msg = "Perception services unavailable; operating in degraded mode"
        else:
            phases_completed.append("PERCEPTION")

        # Phase 3: Spatial Interaction (Gesture)
        gesture_srv = services.get("gesture_service")
        if gesture_srv is not None:
            phases_completed.append("INTERACTION")

        # Phase 4: Multimodal Reasoning (Ultron)
        ultron_srv = services.get("ultron_service")
        if ultron_srv is not None:
            if hasattr(ultron_srv, "fuse") and hasattr(ultron_srv, "reason"):
                try:
                    fused_ctx = ultron_srv.fuse()
                    actions = ultron_srv.reason()
                    fused_count = len(fused_ctx.entities) if hasattr(fused_ctx, "entities") else len(actions)
                    phases_completed.append("REASONING")
                except Exception as e:
                    status = CycleStatus.DEGRADED
                    confidence = min(confidence, 0.4)
                    uncertainty = max(uncertainty, 0.6)
                    diagnostic_msg = f"Ultron reasoning failed: {type(e).__name__}"
            elif hasattr(ultron_srv, "execute_cycle"):
                try:
                    actions = ultron_srv.execute_cycle(observation=None)
                    fused_count = len(actions)
                    phases_completed.append("REASONING")
                except Exception as e:
                    status = CycleStatus.DEGRADED
                    confidence = min(confidence, 0.4)
                    uncertainty = max(uncertainty, 0.6)
                    diagnostic_msg = f"Ultron reasoning failed: {type(e).__name__}"

        # Phase 5: Anatomy Simulation
        anatomy_srv = services.get("anatomy_service")
        if anatomy_srv is not None and hasattr(anatomy_srv, "step_simulation"):
            try:
                anatomy_srv.step_simulation(count=1)
                phases_completed.append("ANATOMY")
            except Exception as e:
                status = CycleStatus.DEGRADED
                confidence = min(confidence, 0.3)
                uncertainty = max(uncertainty, 0.7)
                diagnostic_msg = f"Anatomy simulation step failed: {type(e).__name__}"

        # Phase 6: Tools
        tool_srv = services.get("tool_service")
        if tool_srv is not None:
            if hasattr(tool_srv, "engine") and tool_srv.engine is not None:
                tool_count = len(tool_srv.engine.result_history)
            phases_completed.append("TOOLS")

        # Phase 7: XR Presentation Frame
        xr_srv = services.get("xr_service")
        if xr_srv is not None and hasattr(xr_srv, "generate_frame"):
            try:
                frame_desc = xr_srv.generate_frame()
                xr_nodes = len(frame_desc.nodes)
                phases_completed.append("PRESENTATION")
            except Exception as e:
                status = CycleStatus.DEGRADED
                confidence = min(confidence, 0.2)
                uncertainty = max(uncertainty, 0.8)
                diagnostic_msg = f"XR frame generation failed: {type(e).__name__}"

        elapsed_ms = (time.perf_counter() - start_time) * 1000.0

        summary = CycleSummary(
            cycle_id=cycle_id,
            epoch_id=self._epoch_id,
            session_id=session_id,
            sequence_number=sequence_number,
            status=status,
            execution_time_ms=elapsed_ms,
            phases_completed=tuple(phases_completed),
            fused_entity_count=fused_count,
            tool_invocations_count=tool_count,
            xr_node_count=xr_nodes,
            confidence=confidence,
            uncertainty_metric=uncertainty,
            is_simulated=True,
            diagnostic_message=diagnostic_msg,
        )

        self._record_cycle(summary)
        return summary

    def _record_cycle(self, summary: CycleSummary) -> None:
        """Store cycle summary in bounded ring buffer."""
        if len(self._cycle_history) >= MAX_CYCLE_HISTORY:
            self._cycle_history.pop(0)
        self._cycle_history.append(summary)

    def reset(self, epoch_id: int) -> None:
        """Reset cycle coordinator state for a new epoch."""
        self._epoch_id = epoch_id
        self.clear()

    def clear(self) -> None:
        """Clear cycle history buffer."""
        self._cycle_history.clear()
