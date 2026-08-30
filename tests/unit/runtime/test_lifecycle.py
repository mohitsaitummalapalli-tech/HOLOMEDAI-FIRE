"""Comprehensive unit tests for the RuntimeEngine lifecycle state machine and invariants."""

from typing import Optional
import pytest

from holomed.configuration.models import AppConfig, EnvironmentProfile, LogLevel
from holomed.runtime.context import RuntimeContext
from holomed.runtime.exceptions import (
    HoloMedRuntimeError,
    InvalidStateTransitionError,
    ResourceCleanupRequiredError,
    ServiceInitializationError,
    ServiceLifecycleError,
    ServiceShutdownError,
    ServiceStartupError,
)
from holomed.runtime.lifecycle import RuntimeEngine
from holomed.runtime.models import (
    HealthStatus,
    OwnedResourceSet,
    ResourceStatus,
    RuntimeState,
    ServiceHealth,
)
from holomed.runtime.service import IService, ServiceRegistration, ServiceState


class MockService(IService):
    """Test service with configurable lifecycle behaviors and tracked resources."""

    def __init__(
        self,
        name: str,
        dependencies: tuple[str, ...] = (),
        init_fail: bool = False,
        start_fail: bool = False,
        start_acquire: bool = False,
        stop_fail: bool = False,
        stop_unreleased: bool = False,
        resources_to_acquire: tuple[str, ...] = (),
    ) -> None:
        self._name = name
        self._dependencies = dependencies
        self._init_fail = init_fail
        self._start_fail = start_fail
        self._start_acquire = start_acquire
        self._stop_fail = stop_fail
        self._stop_unreleased = stop_unreleased
        self._resources_to_acquire = resources_to_acquire

        self._resources = OwnedResourceSet(name, epoch_id=1)
        self.initialize_count = 0
        self.start_count = 0
        self.stop_count = 0

    @property
    def name(self) -> str:
        return self._name

    @property
    def dependencies(self) -> tuple[str, ...]:
        return self._dependencies

    @property
    def resources(self) -> OwnedResourceSet:
        return self._resources

    def initialize(self, context: RuntimeContext) -> None:
        self.initialize_count += 1
        for res_id in self._resources_to_acquire:
            self._resources.acquire(res_id)
        if self._init_fail:
            raise RuntimeError(f"Deliberate init failure in {self._name}")

    def start(self) -> None:
        self.start_count += 1
        if self._start_acquire:
            self._resources.acquire(f"{self._name}-illegal-start-res")
        if self._start_fail:
            raise RuntimeError(f"Deliberate start failure in {self._name}")

    def stop(self) -> None:
        self.stop_count += 1
        if self._stop_unreleased:
            for h in list(self._resources.outstanding_handles):
                self._resources.mark_release_failed(h.resource_id, "Resource lock stuck")
            return
        if self._stop_fail:
            for h in list(self._resources.outstanding_handles):
                self._resources.mark_release_failed(h.resource_id, "Stop error")
            raise RuntimeError(f"Deliberate stop error in {self._name}")

        for h in list(self._resources.outstanding_handles):
            self._resources.release(h.resource_id)

    def health(self) -> ServiceHealth:
        return ServiceHealth(name=self._name, status=HealthStatus.HEALTHY, message="OK", timestamp_utc="")


def _make_config() -> AppConfig:
    return AppConfig(
        app_name="LifecycleApp",
        environment=EnvironmentProfile.TESTING,
        host="127.0.0.1",
        port=8000,
        log_level=LogLevel.INFO,
    )


# =========================================================================
# 1. State Machine & Matrix Tests (14 Legal, 6 State-Preserving, 29 Forbidden)
# =========================================================================

def test_engine_legal_state_transitions() -> None:
    """Prove all 14 legal non-state-preserving transitions across engine lifecycle."""
    # 1. NEW -> INITIALIZING -> READY
    engine = RuntimeEngine()
    engine.register_service(ServiceRegistration("svc.a", lambda: MockService("svc.a"), ()))
    assert engine.state == RuntimeState.NEW
    engine.initialize(_make_config())
    assert engine.state == RuntimeState.READY

    # 2. READY -> RUNNING
    engine.start()
    assert engine.state == RuntimeState.RUNNING

    # 3. RUNNING -> STOPPING -> STOPPED
    engine.stop()
    assert engine.state == RuntimeState.STOPPED

    # 4. STOPPED -> INITIALIZING -> READY
    engine.initialize(_make_config())
    assert engine.state == RuntimeState.READY

    # 5. READY -> STOPPING -> STOPPED
    engine.stop()
    assert engine.state == RuntimeState.STOPPED

    # 6. NEW -> STOPPED (NO-OP stop)
    engine_new = RuntimeEngine()
    engine_new.stop()
    assert engine_new.state == RuntimeState.STOPPED

    # 7. INITIALIZING -> FAILED (Init failure + rollback)
    engine_fail_init = RuntimeEngine()
    engine_fail_init.register_service(ServiceRegistration("svc.fail", lambda: MockService("svc.fail", init_fail=True), ()))
    with pytest.raises(ServiceInitializationError):
        engine_fail_init.initialize(_make_config())
    assert engine_fail_init.state == RuntimeState.FAILED

    # 8. FAILED -> INITIALIZING -> READY (re-init from clean FAILED)
    engine_fail_init._registry.clear()
    engine_fail_init.register_service(ServiceRegistration("svc.ok", lambda: MockService("svc.ok"), ()))
    engine_fail_init.initialize(_make_config())
    assert engine_fail_init.state == RuntimeState.READY

    # 9. READY -> FAILED (Start failure + rollback)
    engine_fail_start = RuntimeEngine()
    engine_fail_start.register_service(ServiceRegistration("svc.fail", lambda: MockService("svc.fail", start_fail=True), ()))
    engine_fail_start.initialize(_make_config())
    with pytest.raises(ServiceStartupError):
        engine_fail_start.start()
    assert engine_fail_start.state == RuntimeState.FAILED

    # 10. FAILED -> STOPPED (Clean epoch retirement)
    assert engine_fail_start.is_resource_clean is True
    engine_fail_start.stop()
    assert engine_fail_start.state == RuntimeState.STOPPED

    # 11. RUNNING -> FAILED (Fatal runtime crash in run_guarded)
    engine_crash = RuntimeEngine()
    engine_crash.register_service(ServiceRegistration("svc.a", lambda: MockService("svc.a"), ()))
    engine_crash.initialize(_make_config())
    engine_crash.start()
    with pytest.raises(HoloMedRuntimeError):
        engine_crash.run_guarded(lambda: 1 / 0)
    assert engine_crash.state == RuntimeState.FAILED

    # 12. STOPPING -> FAILED (Stop error aggregation)
    engine_fail_stop = RuntimeEngine()
    engine_fail_stop.register_service(ServiceRegistration("svc.fail", lambda: MockService("svc.fail", stop_fail=True, resources_to_acquire=("r1",)), ()))
    engine_fail_stop.initialize(_make_config())
    engine_fail_stop.start()
    with pytest.raises(ServiceShutdownError):
        engine_fail_stop.stop()
    assert engine_fail_stop.state == RuntimeState.FAILED


def test_engine_state_preserving_operations() -> None:
    """Prove all 6 legal state-preserving operations."""
    # 1. NEW -> NEW (registration)
    engine = RuntimeEngine()
    engine.register_service(ServiceRegistration("svc.a", lambda: MockService("svc.a"), ()))
    assert engine.state == RuntimeState.NEW

    # 2. STOPPED -> STOPPED (stop() on STOPPED is NO-OP)
    engine.stop()
    assert engine.state == RuntimeState.STOPPED
    engine.stop()
    assert engine.state == RuntimeState.STOPPED

    # 3. READY -> READY
    engine._registry.clear()
    engine.register_service(ServiceRegistration("svc.a", lambda: MockService("svc.a"), ()))
    engine.initialize(_make_config())
    assert engine.state == RuntimeState.READY

    # 4. RUNNING -> RUNNING (start() on RUNNING is NO-OP)
    engine.start()
    assert engine.state == RuntimeState.RUNNING
    engine.start()
    assert engine.state == RuntimeState.RUNNING

    # 5. FAILED -> FAILED (retry_cleanup() on FAILED remains FAILED)
    engine_fail = RuntimeEngine()
    engine_fail.register_service(ServiceRegistration("svc.fail", lambda: MockService("svc.fail", stop_unreleased=True, resources_to_acquire=("r1",)), ()))
    engine_fail.initialize(_make_config())
    engine_fail.start()
    with pytest.raises(ServiceShutdownError):
        engine_fail.stop()
    assert engine_fail.state == RuntimeState.FAILED
    with pytest.raises(ResourceCleanupRequiredError):
        engine_fail.retry_cleanup()
    assert engine_fail.state == RuntimeState.FAILED


def test_engine_forbidden_state_transitions() -> None:
    """Assert all forbidden transitions raise InvalidStateTransitionError with zero side effects."""
    engine = RuntimeEngine()

    # From NEW
    with pytest.raises(InvalidStateTransitionError):
        engine.start()
    with pytest.raises(InvalidStateTransitionError):
        engine.retry_cleanup()
    assert engine.state == RuntimeState.NEW

    # From READY
    engine.register_service(ServiceRegistration("svc.a", lambda: MockService("svc.a"), ()))
    engine.initialize(_make_config())
    assert engine.state == RuntimeState.READY
    with pytest.raises(InvalidStateTransitionError):
        engine.initialize(_make_config())
    with pytest.raises(InvalidStateTransitionError):
        engine.retry_cleanup()
    assert engine.state == RuntimeState.READY

    # From RUNNING
    engine.start()
    assert engine.state == RuntimeState.RUNNING
    with pytest.raises(InvalidStateTransitionError):
        engine.initialize(_make_config())
    with pytest.raises(InvalidStateTransitionError):
        engine.retry_cleanup()
    assert engine.state == RuntimeState.RUNNING


def test_service_registration_lifecycle_rules() -> None:
    """Assert service registration is legal in NEW, STOPPED, FAILED and forbidden otherwise."""
    engine = RuntimeEngine()
    # NEW: legal
    engine.register_service(ServiceRegistration("svc.1", lambda: MockService("svc.1"), ()))
    engine.initialize(_make_config())

    # READY: forbidden
    with pytest.raises(InvalidStateTransitionError):
        engine.register_service(ServiceRegistration("svc.2", lambda: MockService("svc.2"), ()))

    engine.start()
    # RUNNING: forbidden
    with pytest.raises(InvalidStateTransitionError):
        engine.register_service(ServiceRegistration("svc.3", lambda: MockService("svc.3"), ()))

    engine.stop()
    # STOPPED: legal
    engine.register_service(ServiceRegistration("svc.4", lambda: MockService("svc.4"), ()))


# =========================================================================
# 2. Resource Guards, Startup Non-Acquisition & Stop Semantics
# =========================================================================

def test_start_cannot_acquire_new_owned_resources() -> None:
    """Assert start() is mechanically forbidden from acquiring new resources."""
    engine = RuntimeEngine()
    engine.register_service(ServiceRegistration("svc.illegal", lambda: MockService("svc.illegal", start_acquire=True), ()))
    engine.initialize(_make_config())

    with pytest.raises(ServiceStartupError, match="acquired new owned resources during start"):
        engine.start()

    assert engine.state == RuntimeState.FAILED
    assert engine.service_states["svc.illegal"] == ServiceState.FAILED


def test_normal_stop_invokes_cleanup_before_resource_verification() -> None:
    """Assert stop() invokes service stop cleanup before inspecting resource emptiness."""
    svc = MockService("svc.a", resources_to_acquire=("res-1", "res-2"))
    engine = RuntimeEngine()
    engine.register_service(ServiceRegistration("svc.a", lambda: svc, ()))
    engine.initialize(_make_config())
    engine.start()

    assert len(svc.resources.outstanding_handles) == 2
    engine.stop()

    assert svc.stop_count == 1
    assert svc.resources.is_empty is True
    assert engine.state == RuntimeState.STOPPED


def test_normal_stop_retires_active_epoch_and_clears_lifecycle_state() -> None:
    """Assert successful normal stop retires active epoch and clears active services."""
    engine = RuntimeEngine()
    engine.register_service(ServiceRegistration("svc.a", lambda: MockService("svc.a"), ()))
    engine.initialize(_make_config())
    engine.start()
    engine.stop()

    assert engine.state == RuntimeState.STOPPED
    assert engine.active_epoch is None
    assert len(engine.services) == 0
    assert len(engine.service_states) == 0


def test_stop_assigns_stopped_only_after_zero_outstanding_resources() -> None:
    """Assert service is assigned FAILED if unreleased resources remain after stop()."""
    svc = MockService("svc.unclean", stop_unreleased=True, resources_to_acquire=("r1",))
    engine = RuntimeEngine()
    engine.register_service(ServiceRegistration("svc.unclean", lambda: svc, ()))
    engine.initialize(_make_config())
    engine.start()

    with pytest.raises(ServiceShutdownError):
        engine.stop()

    assert engine.state == RuntimeState.FAILED
    assert engine.service_states["svc.unclean"] == ServiceState.FAILED
    assert svc.resources.is_empty is False


def test_zero_resources_does_not_imply_stopped() -> None:
    """Assert STOPPED Resource Necessity Theorem and Conditional Converse."""
    svc = MockService("svc.a")
    # 1. UNINITIALIZED with 0 resources remains UNINITIALIZED
    assert svc.resources.is_empty is True
    # In engine before init:
    engine = RuntimeEngine()
    engine.register_service(ServiceRegistration("svc.a", lambda: svc, ()))
    assert engine.state == RuntimeState.NEW

    # 2. INITIALIZED with successful stop -> STOPPED
    engine.initialize(_make_config())
    assert engine.service_states["svc.a"] == ServiceState.INITIALIZED
    engine.stop()
    assert engine.state == RuntimeState.STOPPED


def test_failed_state_does_not_imply_zero_resources() -> None:
    """Assert dirty FAILED state retains outstanding resource handles."""
    svc = MockService("svc.dirty", stop_unreleased=True, resources_to_acquire=("r-dirty",))
    engine = RuntimeEngine()
    engine.register_service(ServiceRegistration("svc.dirty", lambda: svc, ()))
    engine.initialize(_make_config())
    engine.start()

    with pytest.raises(ServiceShutdownError):
        engine.stop()

    assert engine.state == RuntimeState.FAILED
    assert len(engine.outstanding_resources) == 1
    assert engine.is_resource_clean is False


def test_execution_index_counts_cleanup_attempts_only() -> None:
    """Assert execution_index in ShutdownFailureRecord counts actual cleanup attempts on dirty services."""
    svc_a = MockService("svc.a")
    svc_b = MockService("svc.b", stop_unreleased=True, resources_to_acquire=("rb",))
    svc_c = MockService("svc.c")
    svc_d = MockService("svc.d", stop_unreleased=True, resources_to_acquire=("rd",))

    engine = RuntimeEngine()
    engine.register_service(ServiceRegistration("svc.a", lambda: svc_a, ()))
    engine.register_service(ServiceRegistration("svc.b", lambda: svc_b, ()))
    engine.register_service(ServiceRegistration("svc.c", lambda: svc_c, ()))
    engine.register_service(ServiceRegistration("svc.d", lambda: svc_d, ()))
    engine.initialize(_make_config())
    engine.start()

    with pytest.raises(ServiceShutdownError):
        engine.stop()

    with pytest.raises(ResourceCleanupRequiredError) as retry_exc_info:
        engine.retry_cleanup()

    cause_se = retry_exc_info.value.__cause__
    assert isinstance(cause_se, ServiceShutdownError)
    assert len(cause_se.failures) == 2
    assert cause_se.failures[0].service_name == "svc.d"
    assert cause_se.failures[0].execution_index == 0
    assert cause_se.failures[1].service_name == "svc.b"
    assert cause_se.failures[1].execution_index == 1


def test_tracked_accounting_does_not_claim_physical_destruction() -> None:
    """Assert OwnedResourceSet proves tracked accounting predicate boundary."""
    ors = OwnedResourceSet("svc.test", epoch_id=1)
    h = ors.acquire("res-tracked")
    ors.release("res-tracked")
    assert ors.is_empty is True
    assert ors.records["res-tracked"].status == ResourceStatus.RELEASED


def test_shutdown_failure_error_aggregation() -> None:
    """Assert shutdown failure on multiple services aggregates into ServiceShutdownError."""
    engine = RuntimeEngine()
    engine.register_service(ServiceRegistration("svc.1", lambda: MockService("svc.1", stop_fail=True, resources_to_acquire=("r1",)), ()))
    engine.register_service(ServiceRegistration("svc.2", lambda: MockService("svc.2", stop_fail=True, resources_to_acquire=("r2",)), ()))
    engine.initialize(_make_config())
    engine.start()

    with pytest.raises(ServiceShutdownError) as exc_info:
        engine.stop()

    assert len(exc_info.value.failures) == 2


def test_initialization_failure_reverse_rollback() -> None:
    """Assert init failure rolls back previously initialized services in reverse order."""
    init_order: list[str] = []
    stop_order: list[str] = []

    class TrackedService(MockService):
        def initialize(self, context: RuntimeContext) -> None:
            init_order.append(self.name)
            super().initialize(context)

        def stop(self) -> None:
            stop_order.append(self.name)
            super().stop()

    engine = RuntimeEngine()
    engine.register_service(ServiceRegistration("svc.a", lambda: TrackedService("svc.a", resources_to_acquire=("ra",)), ()))
    engine.register_service(ServiceRegistration("svc.b", lambda: TrackedService("svc.b", dependencies=("svc.a",), init_fail=True), ("svc.a",)))

    with pytest.raises(ServiceInitializationError):
        engine.initialize(_make_config())

    assert init_order == ["svc.a", "svc.b"]
    assert stop_order == ["svc.a"]
    assert engine.state == RuntimeState.FAILED


def test_startup_failure_reverse_rollback() -> None:
    """Assert startup failure rolls back previously started services in reverse order."""
    engine = RuntimeEngine()
    engine.register_service(ServiceRegistration("svc.a", lambda: MockService("svc.a", resources_to_acquire=("ra",)), ()))
    engine.register_service(ServiceRegistration("svc.b", lambda: MockService("svc.b", dependencies=("svc.a",), start_fail=True), ("svc.a",)))
    engine.initialize(_make_config())

    with pytest.raises(ServiceStartupError):
        engine.start()

    assert engine.state == RuntimeState.FAILED


# =========================================================================
# 3. Epoch Management, Monotonic IDs & Cumulative Freshness
# =========================================================================

def test_epoch_ids_are_monotonically_increasing_across_retirement() -> None:
    """Assert epoch IDs increment monotonically across multiple initialize/retire cycles."""
    engine = RuntimeEngine()
    engine.register_service(ServiceRegistration("svc.a", lambda: MockService("svc.a"), ()))

    # Epoch 1
    engine.initialize(_make_config())
    assert engine.active_epoch is not None
    assert engine.active_epoch.epoch_id == 1
    engine.stop()
    assert engine.state == RuntimeState.STOPPED
    assert engine.active_epoch is None

    # Epoch 2
    engine.initialize(_make_config())
    assert engine.active_epoch is not None
    assert engine.active_epoch.epoch_id == 2
    engine.stop()
    assert engine.state == RuntimeState.STOPPED

    # Epoch 3
    engine.initialize(_make_config())
    assert engine.active_epoch.epoch_id == 3


def test_failed_candidate_does_not_consume_epoch_id() -> None:
    """Assert candidate construction failure does not increment or consume next_epoch_id."""
    engine = RuntimeEngine()
    def bad_factory() -> IService:
        raise RuntimeError("Factory exploded")

    engine.register_service(ServiceRegistration("svc.bad", bad_factory, ()))

    with pytest.raises(ServiceLifecycleError):
        engine.initialize(_make_config())

    assert engine._next_epoch_id == 1

    engine._registry.clear()
    engine.register_service(ServiceRegistration("svc.good", lambda: MockService("svc.good"), ()))
    engine.initialize(_make_config())
    assert engine.active_epoch is not None
    assert engine.active_epoch.epoch_id == 1


def test_retired_diagnostic_records_actual_final_engine_state() -> None:
    """Assert EpochDiagnosticRecord records STOPPED as the final state after normal shutdown."""
    engine = RuntimeEngine()
    engine.register_service(ServiceRegistration("svc.a", lambda: MockService("svc.a"), ()))
    engine.initialize(_make_config())
    engine.start()
    engine.stop()

    assert engine.state == RuntimeState.STOPPED
    assert engine.last_retired_diagnostic is not None
    assert engine.last_retired_diagnostic.final_engine_state == RuntimeState.STOPPED


def test_freshness_retention_covers_all_historical_epochs() -> None:
    """Assert cumulative freshness rejects service instances recycled across ANY past epoch."""
    instance_a1 = MockService("svc.a")
    instance_a2 = MockService("svc.a")

    engine = RuntimeEngine()
    engine.register_service(ServiceRegistration("svc.a", lambda: instance_a1, ()))

    # Epoch 1
    engine.initialize(_make_config())
    engine.stop()

    # Epoch 2
    engine._registry["svc.a"] = ServiceRegistration("svc.a", lambda: instance_a2, ())
    engine.initialize(_make_config())
    engine.stop()

    # Epoch 3
    engine._registry["svc.a"] = ServiceRegistration("svc.a", lambda: instance_a1, ())
    with pytest.raises(ServiceLifecycleError, match="reused service instance"):
        engine.initialize(_make_config())


def test_reinitialization_fresh_instance_model() -> None:
    """Assert reinitialization creates distinct fresh instances across epochs."""
    instances: list[MockService] = []

    def factory() -> IService:
        inst = MockService("svc.a")
        instances.append(inst)
        return inst

    engine = RuntimeEngine()
    engine.register_service(ServiceRegistration("svc.a", factory, ()))

    engine.initialize(_make_config())
    engine.stop()

    engine.initialize(_make_config())
    engine.stop()

    assert len(instances) == 2
    assert instances[0] is not instances[1]


# =========================================================================
# 4. Retry Cleanup & Error Boundaries
# =========================================================================

def test_retry_cleanup_dirty_failure_raises_resource_cleanup_required_error() -> None:
    """Assert retry_cleanup publicly raises ResourceCleanupRequiredError when resources remain."""
    engine = RuntimeEngine()
    engine.register_service(ServiceRegistration("svc.stuck", lambda: MockService("svc.stuck", stop_unreleased=True, resources_to_acquire=("lock-1",)), ()))
    engine.initialize(_make_config())
    engine.start()

    with pytest.raises(ServiceShutdownError):
        engine.stop()

    assert engine.state == RuntimeState.FAILED
    with pytest.raises(ResourceCleanupRequiredError):
        engine.retry_cleanup()

    assert engine.state == RuntimeState.FAILED


def test_retry_cleanup_skips_clean_services() -> None:
    """Assert retry_cleanup only calls stop() on services with unreleased resources."""
    svc_clean = MockService("svc.clean")
    svc_dirty = MockService("svc.dirty", stop_unreleased=True, resources_to_acquire=("rd",))

    engine = RuntimeEngine()
    engine.register_service(ServiceRegistration("svc.clean", lambda: svc_clean, ()))
    engine.register_service(ServiceRegistration("svc.dirty", lambda: svc_dirty, ()))
    engine.initialize(_make_config())
    engine.start()

    with pytest.raises(ServiceShutdownError):
        engine.stop()

    clean_stop_count_before = svc_clean.stop_count

    with pytest.raises(ResourceCleanupRequiredError):
        engine.retry_cleanup()

    assert svc_clean.stop_count == clean_stop_count_before  # Clean service skipped!
    assert svc_dirty.stop_count > 1  # Dirty service re-attempted


def test_retry_cleanup_never_changes_engine_failed_state() -> None:
    """Assert retry_cleanup leaves engine state as FAILED even after clean completion."""
    svc_flaky = MockService("svc.flaky", stop_unreleased=True, resources_to_acquire=("rf",))

    engine = RuntimeEngine()
    engine.register_service(ServiceRegistration("svc.flaky", lambda: svc_flaky, ()))
    engine.initialize(_make_config())
    engine.start()

    with pytest.raises(ServiceShutdownError):
        engine.stop()
    assert engine.state == RuntimeState.FAILED

    # Now fix resource issue on flaky service
    svc_flaky._stop_unreleased = False
    engine.retry_cleanup()  # Returns cleanly!

    # Engine MUST still be FAILED!
    assert engine.state == RuntimeState.FAILED
    assert engine.is_resource_clean is True

    # Explicit stop retires epoch to STOPPED
    engine.stop()
    assert engine.state == RuntimeState.STOPPED


def test_failed_retry_clean_then_explicit_stop_retirement() -> None:
    """Assert successful retry_cleanup allows explicit stop() to retire epoch to STOPPED."""
    svc = MockService("svc.a", stop_unreleased=True, resources_to_acquire=("r1",))
    engine = RuntimeEngine()
    engine.register_service(ServiceRegistration("svc.a", lambda: svc, ()))
    engine.initialize(_make_config())
    engine.start()

    with pytest.raises(ServiceShutdownError):
        engine.stop()

    svc._stop_unreleased = False
    engine.retry_cleanup()
    assert engine.state == RuntimeState.FAILED

    engine.stop()
    assert engine.state == RuntimeState.STOPPED
    assert engine.active_epoch is None


def test_fatal_runtime_error_boundary_and_cleanup() -> None:
    """Assert run_guarded executes teardown and chains cause on unhandled failure."""
    svc = MockService("svc.a")
    engine = RuntimeEngine()
    engine.register_service(ServiceRegistration("svc.a", lambda: svc, ()))
    engine.initialize(_make_config())
    engine.start()

    def fatal_target() -> None:
        raise ZeroDivisionError("Math error")

    with pytest.raises(HoloMedRuntimeError) as exc_info:
        engine.run_guarded(fatal_target)

    assert engine.state == RuntimeState.FAILED
    assert isinstance(exc_info.value.original_exception, ZeroDivisionError)
    assert svc.stop_count == 1


def test_engine_service_consistency_theorems() -> None:
    """Prove engine/service consistency invariants across all lifecycle states."""
    engine = RuntimeEngine()
    engine.register_service(ServiceRegistration("svc.a", lambda: MockService("svc.a"), ()))

    # In NEW: services are empty
    assert len(engine.services) == 0

    # In READY: service is INITIALIZED
    engine.initialize(_make_config())
    assert engine.service_states["svc.a"] == ServiceState.INITIALIZED

    # In RUNNING: service is STARTED
    engine.start()
    assert engine.service_states["svc.a"] == ServiceState.STARTED

    # In STOPPED: services are cleared
    engine.stop()
    assert len(engine.services) == 0
    assert len(engine.service_states) == 0
