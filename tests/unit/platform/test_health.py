# -*- coding: utf-8 -*-
"""Unit tests for HealthAggregator and Health Precedence Rules (D254)."""

from __future__ import annotations

import pytest

from holomed.platform.health import HealthAggregator
from holomed.runtime.logging import SecretFilter
from holomed.runtime.models import HealthStatus, OwnedResourceSet, ServiceHealth
from holomed.runtime.service import IService, ServiceState


class StubHealthService(IService):
    def __init__(self, name: str, health_status: HealthStatus) -> None:
        self._name = name
        self._status = health_status
        self._resources = OwnedResourceSet(name, 1)
        self.state = ServiceState.STARTED

    @property
    def name(self) -> str:
        return self._name

    @property
    def dependencies(self) -> tuple[str, ...]:
        return ()

    @property
    def resources(self) -> OwnedResourceSet:
        return self._resources

    def initialize(self, context: Any) -> None:
        pass

    def start(self) -> None:
        pass

    def stop(self) -> None:
        pass

    def health(self) -> ServiceHealth:
        return ServiceHealth(self._name, self._status, "msg", "2026-09-01T20:00:00Z")


def test_d254_health_precedence_combinations(secret_filter: SecretFilter) -> None:
    """Verify D254 strict precedence FAILED > UNHEALTHY > DEGRADED > HEALTHY."""
    ha = HealthAggregator(secret_filter=secret_filter)

    # 1. All HEALTHY -> HEALTHY
    srvs = {"s1": StubHealthService("s1", HealthStatus.HEALTHY), "s2": StubHealthService("s2", HealthStatus.HEALTHY)}
    states = {"s1": ServiceState.STARTED, "s2": ServiceState.STARTED}
    rep = ha.aggregate_health(srvs, states, epoch_id=1)
    assert rep.status == HealthStatus.HEALTHY

    # 2. One DEGRADED -> DEGRADED
    srvs["s2"] = StubHealthService("s2", HealthStatus.DEGRADED)
    rep = ha.aggregate_health(srvs, states, epoch_id=1)
    assert rep.status == HealthStatus.DEGRADED

    # 3. One UNHEALTHY + One DEGRADED -> UNHEALTHY
    srvs["s1"] = StubHealthService("s1", HealthStatus.UNHEALTHY)
    rep = ha.aggregate_health(srvs, states, epoch_id=1)
    assert rep.status == HealthStatus.UNHEALTHY

    # 4. One FAILED -> FAILED
    srvs["s2"] = StubHealthService("s2", HealthStatus.FAILED)
    rep = ha.aggregate_health(srvs, states, epoch_id=1)
    assert rep.status == HealthStatus.FAILED
