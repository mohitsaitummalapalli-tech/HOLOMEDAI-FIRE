# -*- coding: utf-8 -*-
"""Whole-System Aggregate Health Assessment and Precedence Engine (D254)."""

from __future__ import annotations

from typing import Mapping, Optional

from holomed.runtime.logging import SecretFilter
from holomed.runtime.models import HealthStatus, ServiceHealth
from holomed.runtime.service import IService, ServiceState
from holomed.platform.models import AggregateHealthReport

# Mathematical precedence (D254): FAILED > UNHEALTHY > DEGRADED > HEALTHY
STATUS_PRECEDENCE: dict[HealthStatus, int] = {
    HealthStatus.FAILED: 4,
    HealthStatus.UNHEALTHY: 3,
    HealthStatus.DEGRADED: 2,
    HealthStatus.HEALTHY: 1,
}


class HealthAggregator:
    """Aggregates health reports across all platform services with strict precedence."""

    def __init__(self, secret_filter: Optional[SecretFilter] = None) -> None:
        self._secret_filter = secret_filter

    def aggregate_health(
        self,
        services: Mapping[str, IService],
        service_states: Mapping[str, ServiceState],
        epoch_id: int,
    ) -> AggregateHealthReport:
        """Evaluate and aggregate health across all supplied services."""
        highest_precedence = 1
        agg_status = HealthStatus.HEALTHY

        raw_states: dict[str, str] = {}
        raw_healths: dict[str, str] = {}
        diagnostics: list[str] = []
        is_clean = True

        for name, srv in services.items():
            state = service_states.get(name, srv.state if hasattr(srv, "state") else ServiceState.UNINITIALIZED)
            raw_states[name] = state.name

            try:
                h = srv.health()
                h_status = h.status
                raw_healths[name] = h_status.name
                if h.message:
                    diagnostics.append(f"{name}: {h.message}")
            except Exception as e:
                h_status = HealthStatus.FAILED
                raw_healths[name] = "FAILED"
                diagnostics.append(f"{name}: health check raised {type(e).__name__}")

            if srv.resources is not None and not srv.resources.is_empty:
                # If stopped but resources remain, unclean
                if state == ServiceState.STOPPED:
                    is_clean = False
                    diagnostics.append(f"{name}: unreleased resources remain while STOPPED")

            prec = STATUS_PRECEDENCE.get(h_status, 3)
            if prec > highest_precedence:
                highest_precedence = prec
                agg_status = h_status

        # If any service state is FAILED, aggregate must be at least FAILED
        if any(st == ServiceState.FAILED for st in service_states.values()):
            agg_status = HealthStatus.FAILED

        sanitized_diag = tuple(
            self._secret_filter.redact(d) if self._secret_filter else d
            for d in diagnostics
        )

        return AggregateHealthReport(
            status=agg_status,
            service_states=raw_states,
            service_healths=raw_healths,
            epoch_id=epoch_id,
            is_resource_clean=is_clean,
            diagnostics=sanitized_diag,
        )
