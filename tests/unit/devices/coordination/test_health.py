"""Tests for M00.8 device health aggregator and precedence logic."""

import pytest

from holomed.configuration.models import SecretString
from holomed.devices.coordination.health import DeviceHealthAggregator
from holomed.devices.coordination.models import DeviceObservation
from holomed.devices.models import DeviceState, DeviceType
from holomed.runtime.logging import SecretFilter
from holomed.runtime.models import HealthStatus
from holomed.runtime.service import ServiceState


def make_obs(dev_id: str, status: HealthStatus, msg: str = "ok") -> DeviceObservation:
    return DeviceObservation(
        device_id=dev_id,
        physical_id=f"p_{dev_id}",
        device_type=DeviceType.RGB_CAMERA,
        state=DeviceState.ACTIVE,
        health_status=status,
        health_message=msg,
        observed_at_utc="2026-09-01T00:00:00Z",
        epoch_id=1,
    )


def test_health_precedence():
    """Verify health status precedence FAILED > UNHEALTHY > DEGRADED > HEALTHY."""
    agg = DeviceHealthAggregator()

    # All HEALTHY
    obs_healthy = [make_obs("d1", HealthStatus.HEALTHY), make_obs("d2", HealthStatus.HEALTHY)]
    assert agg.aggregate(obs_healthy, ServiceState.STARTED).status == HealthStatus.HEALTHY

    # One DEGRADED
    obs_degraded = obs_healthy + [make_obs("d3", HealthStatus.DEGRADED)]
    assert agg.aggregate(obs_degraded, ServiceState.STARTED).status == HealthStatus.DEGRADED

    # One UNHEALTHY beats DEGRADED
    obs_unhealthy = obs_degraded + [make_obs("d4", HealthStatus.UNHEALTHY)]
    assert agg.aggregate(obs_unhealthy, ServiceState.STARTED).status == HealthStatus.UNHEALTHY

    # One FAILED beats UNHEALTHY
    obs_failed = obs_unhealthy + [make_obs("d5", HealthStatus.FAILED)]
    assert agg.aggregate(obs_failed, ServiceState.STARTED).status == HealthStatus.FAILED


def test_health_empty_and_lifecycle_states():
    """Verify empty observations and coordinator lifecycle state evaluations."""
    agg = DeviceHealthAggregator()

    # Empty observations -> HEALTHY
    assert agg.aggregate([], ServiceState.STARTED).status == HealthStatus.HEALTHY

    # UNINITIALIZED -> FAILED
    assert agg.aggregate([], ServiceState.UNINITIALIZED).status == HealthStatus.FAILED

    # FAILED -> FAILED
    assert agg.aggregate([], ServiceState.FAILED).status == HealthStatus.FAILED

    # STOPPED -> HEALTHY
    assert agg.aggregate([], ServiceState.STOPPED).status == HealthStatus.HEALTHY


def test_health_secret_redaction():
    """Verify ATK-35: Secret strings in health messages are atomically redacted."""
    secret = SecretString("super_secret_token_abc")
    s_filter = SecretFilter([secret])
    agg = DeviceHealthAggregator(secret_filter=s_filter)

    obs = [make_obs("d1", HealthStatus.DEGRADED, f"Connection failed with token: {secret.get_secret_value()}")]
    report = agg.aggregate(obs, ServiceState.STARTED)

    assert "super_secret_token_abc" not in report.message
    assert "<redacted>" in report.message
