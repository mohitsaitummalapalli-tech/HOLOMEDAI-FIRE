# -*- coding: utf-8 -*-
"""Whole-System Consistency and Integration Auditor for M08."""

from __future__ import annotations

from typing import Any, Mapping, Optional

from holomed.runtime.logging import SecretFilter
from holomed.runtime.models import ResourceStatus
from holomed.runtime.service import IService, ServiceState
from holomed.platform.cycle import CycleCoordinator
from holomed.platform.models import MAX_CYCLE_HISTORY
from holomed.platform.session import SessionManager

REQUIRED_SERVICES: tuple[str, ...] = (
    "device_manager",
    "vision_service",
    "audio_service",
    "gesture_service",
    "ultron_service",
    "anatomy_service",
    "xr_service",
    "tool_service",
)


class PlatformConsistencyAuditor:
    """Performs deep whole-system integration and cross-domain state verification."""

    def __init__(self, secret_filter: Optional[SecretFilter] = None) -> None:
        self._secret_filter = secret_filter

    def audit_platform(
        self,
        services: Mapping[str, IService],
        service_states: Mapping[str, ServiceState],
        session_manager: SessionManager,
        cycle_coordinator: CycleCoordinator,
        epoch_id: int,
    ) -> Mapping[str, Any]:
        """Perform comprehensive whole-system consistency audit."""
        findings: list[str] = []

        # 1. Verify all 8 required services are registered
        for req in REQUIRED_SERVICES:
            if req not in services:
                findings.append(f"Missing required service: {req}")

        # 2. Check epoch alignment across epoch-aware services
        for name in ("ultron_service", "anatomy_service", "xr_service", "tool_service"):
            srv = services.get(name)
            if srv is not None and srv.resources is not None:
                if srv.resources.epoch_id != epoch_id:
                    findings.append(f"Service {name} is on epoch {srv.resources.epoch_id} != platform epoch {epoch_id}")

        # 3. Check for resource failures across all services
        for name, srv in services.items():
            if srv.resources is not None:
                for handle_id, rec in srv.resources.records.items():
                    if rec.status == ResourceStatus.UNRELEASED_FAILURE:
                        findings.append(f"Service {name} has unreleased failure on handle {handle_id}")

        # 4. Check cycle history bounds
        if len(cycle_coordinator.cycle_history) > MAX_CYCLE_HISTORY:
            findings.append(f"Cycle history count ({len(cycle_coordinator.cycle_history)}) exceeds limit ({MAX_CYCLE_HISTORY})")

        sanitized = [
            self._secret_filter.redact(f) if self._secret_filter else f
            for f in findings
        ]

        return {
            "audit_passed": len(findings) == 0,
            "required_services_count": len(REQUIRED_SERVICES),
            "present_services_count": len(services),
            "active_sessions_count": session_manager.session_count,
            "cycle_history_count": len(cycle_coordinator.cycle_history),
            "findings_count": len(sanitized),
            "findings": sanitized,
        }
