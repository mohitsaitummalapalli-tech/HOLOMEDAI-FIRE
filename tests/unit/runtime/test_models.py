"""Unit tests for runtime state, health, resource, and epoch data models."""

from dataclasses import FrozenInstanceError
from datetime import datetime, timezone
import pytest

from holomed.configuration.models import AppConfig, EnvironmentProfile, LogLevel
from holomed.runtime.context import RuntimeContext
from holomed.runtime.exceptions import ServiceLifecycleError
from holomed.runtime.models import (
    ConfigurationEpoch,
    EpochDiagnosticRecord,
    HealthStatus,
    OwnedResourceSet,
    ResourceHandle,
    ResourceStatus,
    RuntimeHealth,
    RuntimeState,
    ServiceHealth,
)


def test_runtime_state_enum() -> None:
    """Assert RuntimeState enum contains exact 7 lifecycle states."""
    expected = {"NEW", "INITIALIZING", "READY", "RUNNING", "STOPPING", "STOPPED", "FAILED"}
    assert {s.name for s in RuntimeState} == expected
    assert {s.value for s in RuntimeState} == expected


def test_health_status_enum() -> None:
    """Assert HealthStatus enum contains exact 4 severity levels."""
    expected = {"HEALTHY", "DEGRADED", "UNHEALTHY", "FAILED"}
    assert {h.name for h in HealthStatus} == expected


def test_service_health_and_runtime_health_models() -> None:
    """Assert ServiceHealth and RuntimeHealth models are immutable and serialize correctly."""
    now = datetime.now(timezone.utc).isoformat()
    sh1 = ServiceHealth(name="svc.a", status=HealthStatus.HEALTHY, message="OK", timestamp_utc=now)
    sh2 = ServiceHealth(name="svc.b", status=HealthStatus.DEGRADED, message="High latency", timestamp_utc=now)

    rh = RuntimeHealth(
        engine_state=RuntimeState.RUNNING,
        aggregate_status=HealthStatus.DEGRADED,
        services={"svc.b": sh2, "svc.a": sh1},
        timestamp_utc=now,
    )

    # Immutability
    with pytest.raises((FrozenInstanceError, AttributeError)):
        rh.engine_state = RuntimeState.STOPPED  # type: ignore

    # Sorting
    assert list(rh.services.keys()) == ["svc.a", "svc.b"]

    d = rh.to_dict()
    assert d["engine_state"] == "RUNNING"
    assert d["aggregate_status"] == "DEGRADED"
    assert d["services"]["svc.a"]["status"] == "HEALTHY"
    assert d["services"]["svc.b"]["status"] == "DEGRADED"


def test_owned_resource_set_lifecycle_and_accounting() -> None:
    """Assert OwnedResourceSet accurately tracks resource acquisition, failure, and release."""
    ors = OwnedResourceSet("test.service", epoch_id=1)
    assert ors.is_empty is True
    assert ors.outstanding_handles == frozenset()

    # Acquire resource 1
    h1 = ors.acquire("res-1")
    assert isinstance(h1, ResourceHandle)
    assert h1.resource_id == "res-1"
    assert h1.service_name == "test.service"
    assert h1.epoch_id == 1
    assert ors.is_empty is False
    assert ors.outstanding_handles == frozenset([h1])
    assert ors.records["res-1"].status == ResourceStatus.ACQUIRED

    # Acquire resource 2
    h2 = ors.acquire("res-2")
    assert ors.outstanding_handles == frozenset([h1, h2])

    # Mark res-1 release failed
    ors.mark_release_failed("res-1", error="Socket timeout")
    assert ors.records["res-1"].status == ResourceStatus.UNRELEASED_FAILURE
    assert ors.records["res-1"].release_error == "Socket timeout"
    # Unreleased failure is still an outstanding handle!
    assert ors.outstanding_handles == frozenset([h1, h2])
    assert ors.is_empty is False

    # Release res-2
    ors.release("res-2")
    assert ors.records["res-2"].status == ResourceStatus.RELEASED
    assert ors.outstanding_handles == frozenset([h1])
    assert ors.is_empty is False

    # Now confirm release of res-1
    ors.release("res-1")
    assert ors.records["res-1"].status == ResourceStatus.RELEASED
    assert ors.outstanding_handles == frozenset()
    assert ors.is_empty is True

    # Idempotent release
    ors.release("res-1")
    assert ors.records["res-1"].status == ResourceStatus.RELEASED


def test_owned_resource_set_duplicate_acquisition_raises() -> None:
    """Assert acquiring duplicate active resource ID raises ServiceLifecycleError."""
    ors = OwnedResourceSet("test.service", epoch_id=1)
    ors.acquire("res-duplicate")
    with pytest.raises(ServiceLifecycleError, match="Duplicate active resource"):
        ors.acquire("res-duplicate")


def test_owned_resource_set_unknown_resource_raises() -> None:
    """Assert releasing or failing unknown resource raises ServiceLifecycleError."""
    ors = OwnedResourceSet("test.service", epoch_id=1)
    with pytest.raises(ServiceLifecycleError, match="unknown resource ID"):
        ors.release("non-existent")
    with pytest.raises(ServiceLifecycleError, match="unknown resource ID"):
        ors.mark_release_failed("non-existent", "error")


def test_configuration_epoch_and_diagnostic_record_immutability() -> None:
    """Assert ConfigurationEpoch and EpochDiagnosticRecord are strictly immutable."""
    config = AppConfig(
        app_name="App",
        environment=EnvironmentProfile.DEVELOPMENT,
        host="127.0.0.1",
        port=8000,
        log_level=LogLevel.INFO,
    )
    context = RuntimeContext(app_config=config, epoch_id=1)
    epoch = ConfigurationEpoch(
        epoch_id=1,
        config=config,
        context=context,
        topology=("svc.a", "svc.b"),
        registrations={},
    )
    with pytest.raises((FrozenInstanceError, AttributeError)):
        epoch.epoch_id = 2  # type: ignore

    now = datetime.now(timezone.utc).isoformat()
    diag = EpochDiagnosticRecord(
        epoch_id=1,
        final_engine_state=RuntimeState.STOPPED,
        service_final_states={},
        retired_timestamp_utc=now,
    )
    with pytest.raises((FrozenInstanceError, AttributeError)):
        diag.final_engine_state = RuntimeState.FAILED  # type: ignore
