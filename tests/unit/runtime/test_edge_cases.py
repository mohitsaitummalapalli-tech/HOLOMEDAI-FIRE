"""Adversarial probes, import safety, and guarded execution boundary unit tests."""

import threading
import pytest

from holomed.configuration.models import AppConfig, EnvironmentProfile, LogLevel
from holomed.runtime.context import RuntimeContext
from holomed.runtime.exceptions import (
    HoloMedRuntimeError,
    InvalidStateTransitionError,
    ServiceDependencyError,
    ServiceLifecycleError,
    ServiceShutdownError,
)
from holomed.runtime.lifecycle import RuntimeEngine
from holomed.runtime.models import (
    HealthStatus,
    OwnedResourceSet,
    RuntimeState,
    ServiceHealth,
)
from holomed.runtime.service import IService, ServiceRegistration


class EdgeCaseService(IService):
    def __init__(self, name: str, stop_fail: bool = False) -> None:
        self._name = name
        self._stop_fail = stop_fail
        self._resources = OwnedResourceSet(name, epoch_id=1)
        self.stop_called = False

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
        self._resources.acquire(f"{self._name}-res")

    def start(self) -> None:
        pass

    def stop(self) -> None:
        self.stop_called = True
        if self._stop_fail:
            self._resources.mark_release_failed(f"{self._name}-res", "Teardown error")
            raise RuntimeError(f"Stop failure in {self._name}")
        self._resources.release(f"{self._name}-res")

    def health(self) -> ServiceHealth:
        return ServiceHealth(name=self._name, status=HealthStatus.HEALTHY, message="OK", timestamp_utc="")


def _make_config() -> AppConfig:
    return AppConfig(
        app_name="EdgeApp",
        environment=EnvironmentProfile.TESTING,
        host="127.0.0.1",
        port=8000,
        log_level=LogLevel.INFO,
    )


def test_zero_import_side_effects() -> None:
    """Assert importing holomed packages starts zero threads and causes zero background execution."""
    threads_before = threading.active_count()
    import holomed.common
    import holomed.configuration
    import holomed.runtime
    threads_after = threading.active_count()

    assert threads_before == threads_after


def test_guarded_execution_success_and_exception_boundary() -> None:
    """Assert run_guarded() executes target cleanly and tears down on failure with cause chaining."""
    svc_a = EdgeCaseService("svc.a")
    svc_b = EdgeCaseService("svc.b")

    engine = RuntimeEngine()
    engine.register_service(ServiceRegistration("svc.a", lambda: svc_a, ()))
    engine.register_service(ServiceRegistration("svc.b", lambda: svc_b, ()))
    engine.initialize(_make_config())
    engine.start()

    # Success case
    result = engine.run_guarded(lambda x, y: x + y, 10, 20)
    assert result == 30
    assert engine.state == RuntimeState.RUNNING

    # Failure case
    def faulty_workload() -> None:
        raise ValueError("Fatal computation crash")

    with pytest.raises(HoloMedRuntimeError) as exc_info:
        engine.run_guarded(faulty_workload)

    # Asserts
    assert engine.state == RuntimeState.FAILED
    assert isinstance(exc_info.value.original_exception, ValueError)
    assert exc_info.value.__cause__ is exc_info.value.original_exception
    assert svc_a.stop_called is True
    assert svc_b.stop_called is True


def test_multiple_cleanup_failures_do_not_abort_later_cleanup() -> None:
    """Assert multi-service teardown continues across all services despite individual failures."""
    svc_1 = EdgeCaseService("svc.1", stop_fail=True)
    svc_2 = EdgeCaseService("svc.2", stop_fail=False)
    svc_3 = EdgeCaseService("svc.3", stop_fail=True)

    engine = RuntimeEngine()
    engine.register_service(ServiceRegistration("svc.1", lambda: svc_1, ()))
    engine.register_service(ServiceRegistration("svc.2", lambda: svc_2, ()))
    engine.register_service(ServiceRegistration("svc.3", lambda: svc_3, ()))
    engine.initialize(_make_config())
    engine.start()

    with pytest.raises(ServiceShutdownError) as exc_info:
        engine.stop()

    assert svc_1.stop_called is True
    assert svc_2.stop_called is True
    assert svc_3.stop_called is True
    assert len(exc_info.value.failures) == 2  # svc.3 and svc.1


def test_candidate_failure_leaves_active_state_observationally_unchanged() -> None:
    """Assert candidate validation failure leaves pre-existing active engine state byte-for-byte intact."""
    svc_ok = EdgeCaseService("svc.ok")
    engine = RuntimeEngine()
    engine.register_service(ServiceRegistration("svc.ok", lambda: svc_ok, ()))
    engine.initialize(_make_config())
    engine.start()
    engine.stop()

    # Now in STOPPED state with diagnostic record
    diag_before = engine.last_retired_diagnostic
    state_before = engine.state
    epoch_id_counter_before = engine._next_epoch_id

    # Register an invalid service with circular dependency
    engine.register_service(ServiceRegistration("svc.c1", lambda: EdgeCaseService("svc.c1"), ("svc.c2",)))
    engine.register_service(ServiceRegistration("svc.c2", lambda: EdgeCaseService("svc.c2"), ("svc.c1",)))

    with pytest.raises(ServiceDependencyError):
        engine.initialize(_make_config())

    assert engine.state == state_before
    assert engine.last_retired_diagnostic == diag_before
    assert engine._next_epoch_id == epoch_id_counter_before
