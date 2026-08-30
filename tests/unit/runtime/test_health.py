"""Unit tests for in-process health evaluation and aggregation."""

from datetime import datetime, timezone
import pytest

from holomed.runtime.health import HealthEvaluator
from holomed.runtime.models import HealthStatus, OwnedResourceSet, RuntimeState, ServiceHealth
from holomed.runtime.service import IService
from holomed.runtime.context import RuntimeContext


class HealthMockService(IService):
    def __init__(self, name: str, status: HealthStatus, raises: bool = False) -> None:
        self._name = name
        self._status = status
        self._raises = raises
        self._resources = OwnedResourceSet(name, epoch_id=1)

    @property
    def name(self) -> str:
        return self._name

    @property
    def dependencies(self) -> tuple[str, ...]:
        return ()

    @property
    def resources(self) -> OwnedResourceSet:
        return self._resources

    def initialize(self, context: RuntimeContext) -> None:
        pass

    def start(self) -> None:
        pass

    def stop(self) -> None:
        pass

    def health(self) -> ServiceHealth:
        if self._raises:
            raise RuntimeError("Hardware sensor failure")
        return ServiceHealth(
            name=self._name,
            status=self._status,
            message=f"Status is {self._status.name}",
            timestamp_utc=datetime.now(timezone.utc).isoformat(),
        )


def test_health_aggregation_precedence() -> None:
    """Assert aggregate health calculation follows strict precedence."""
    # All healthy
    services_healthy = {
        "svc.a": HealthMockService("svc.a", HealthStatus.HEALTHY),
        "svc.b": HealthMockService("svc.b", HealthStatus.HEALTHY),
    }
    rh = HealthEvaluator.evaluate(services_healthy, RuntimeState.RUNNING)
    assert rh.aggregate_status == HealthStatus.HEALTHY

    # Degraded
    services_degraded = {
        "svc.a": HealthMockService("svc.a", HealthStatus.HEALTHY),
        "svc.b": HealthMockService("svc.b", HealthStatus.DEGRADED),
    }
    rh = HealthEvaluator.evaluate(services_degraded, RuntimeState.RUNNING)
    assert rh.aggregate_status == HealthStatus.DEGRADED

    # Unhealthy over Degraded
    services_unhealthy = {
        "svc.a": HealthMockService("svc.a", HealthStatus.DEGRADED),
        "svc.b": HealthMockService("svc.b", HealthStatus.UNHEALTHY),
    }
    rh = HealthEvaluator.evaluate(services_unhealthy, RuntimeState.RUNNING)
    assert rh.aggregate_status == HealthStatus.UNHEALTHY

    # Failed over all
    services_failed = {
        "svc.a": HealthMockService("svc.a", HealthStatus.UNHEALTHY),
        "svc.b": HealthMockService("svc.b", HealthStatus.FAILED),
    }
    rh = HealthEvaluator.evaluate(services_failed, RuntimeState.RUNNING)
    assert rh.aggregate_status == HealthStatus.FAILED


def test_health_evaluation_exception_boundary() -> None:
    """Assert unhandled exceptions during health checks are caught and reported as FAILED."""
    services = {
        "svc.exploding": HealthMockService("svc.exploding", HealthStatus.HEALTHY, raises=True),
    }
    rh = HealthEvaluator.evaluate(services, RuntimeState.RUNNING)
    assert rh.aggregate_status == HealthStatus.FAILED
    assert rh.services["svc.exploding"].status == HealthStatus.FAILED
    assert "Hardware sensor failure" in rh.services["svc.exploding"].message


def test_engine_state_failed_forces_aggregate_failed() -> None:
    """Assert engine state FAILED produces aggregate FAILED regardless of service states."""
    services = {
        "svc.ok": HealthMockService("svc.ok", HealthStatus.HEALTHY),
    }
    rh = HealthEvaluator.evaluate(services, RuntimeState.FAILED)
    assert rh.aggregate_status == HealthStatus.FAILED
