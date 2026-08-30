"""Authoritative 7-state runtime engine lifecycle state machine and orchestrator."""

from datetime import datetime, timezone
from types import MappingProxyType
from typing import Any, Callable, Mapping, Optional

from holomed.configuration.loader import load_config
from holomed.configuration.models import AppConfig
from holomed.runtime.context import RuntimeContext
from holomed.runtime.exceptions import (
    HoloMedRuntimeError,
    InvalidStateTransitionError,
    ResourceCleanupRequiredError,
    ServiceDuplicateRegistrationError,
    ServiceInitializationError,
    ServiceLifecycleError,
    ServiceRegistrationError,
    ServiceShutdownError,
    ServiceStartupError,
    ShutdownFailureRecord,
)
from holomed.runtime.health import HealthEvaluator
from holomed.runtime.logging import SecretFilter, StructuredLogger
from holomed.runtime.models import (
    ConfigurationEpoch,
    EpochDiagnosticRecord,
    ResourceHandle,
    RuntimeHealth,
    RuntimeState,
)
from holomed.runtime.service import (
    IService,
    ServiceRegistration,
    ServiceState,
    compile_topology,
)


class RuntimeEngine:
    """Sole authoritative orchestrator of service lifecycle transitions and runtime state."""

    def __init__(self, logger: Optional[StructuredLogger] = None) -> None:
        self._state: RuntimeState = RuntimeState.NEW
        self._registry: dict[str, ServiceRegistration] = {}
        self._services: dict[str, IService] = {}
        self._service_states: dict[str, ServiceState] = {}
        self._active_topology: tuple[str, ...] = ()
        self._active_epoch: Optional[ConfigurationEpoch] = None

        self._next_epoch_id: int = 1
        self._previous_epoch_instances: tuple[IService, ...] = ()
        self._last_retired_diagnostic: Optional[EpochDiagnosticRecord] = None
        self._last_error: Optional[Exception] = None

        self._secret_filter = SecretFilter()
        self._logger = logger or StructuredLogger("runtime.engine", secret_filter=self._secret_filter)

    @property
    def state(self) -> RuntimeState:
        """Current engine lifecycle state."""
        return self._state

    @property
    def services(self) -> Mapping[str, IService]:
        """Immutable view of active service instances."""
        return MappingProxyType(dict(self._services))

    @property
    def service_states(self) -> Mapping[str, ServiceState]:
        """Immutable view of active service states."""
        return MappingProxyType(dict(self._service_states))

    @property
    def active_epoch(self) -> Optional[ConfigurationEpoch]:
        """Current active immutable configuration epoch."""
        return self._active_epoch

    @property
    def last_retired_diagnostic(self) -> Optional[EpochDiagnosticRecord]:
        """Diagnostic record from the most recently retired epoch."""
        return self._last_retired_diagnostic

    @property
    def last_error(self) -> Optional[Exception]:
        """Last fatal exception encountered by the engine."""
        return self._last_error

    @property
    def outstanding_resources(self) -> frozenset[ResourceHandle]:
        """Union of all outstanding owned resources across active services."""
        handles: set[ResourceHandle] = set()
        for service in self._services.values():
            handles.update(service.resources.outstanding_handles)
        return frozenset(handles)

    @property
    def is_resource_clean(self) -> bool:
        """True if and only if zero outstanding tracked resources exist."""
        return len(self.outstanding_resources) == 0

    def register_service(self, registration: ServiceRegistration) -> None:
        """Register a service descriptor for future configuration epochs."""
        if not isinstance(registration, ServiceRegistration):
            raise ServiceRegistrationError(f"registration must be ServiceRegistration, got {type(registration).__name__}")
        if self._state not in (RuntimeState.NEW, RuntimeState.STOPPED, RuntimeState.FAILED):
            raise InvalidStateTransitionError(
                f"Cannot register service '{registration.name}' in engine state: {self._state.name}"
            )
        if registration.name in self._registry:
            raise ServiceDuplicateRegistrationError(
                f"Service '{registration.name}' is already registered in registry"
            )
        self._registry[registration.name] = registration

    def initialize(self, config: Optional[AppConfig] = None) -> None:
        """Construct candidate configuration epoch and initialize registered services."""
        # 1. State machine transition check
        if self._state == RuntimeState.NEW:
            pass
        elif self._state == RuntimeState.STOPPED:
            pass
        elif self._state == RuntimeState.FAILED:
            if not self.is_resource_clean:
                unreleased = [h.resource_id for h in self.outstanding_resources]
                raise ResourceCleanupRequiredError(
                    f"Cannot reinitialize failed engine with unreleased resources: {unreleased}"
                )
        else:
            raise InvalidStateTransitionError(f"Cannot initialize engine from state: {self._state.name}")

        # 2. Phase 1: Candidate Construction and Validation (Zero Mutation on Failure)
        # NOTE: No engine state mutation is permitted until Phase 2 atomic activation.
        candidate_epoch_id = self._next_epoch_id
        candidate_config = load_config() if config is None else config
        if not isinstance(candidate_config, AppConfig):
            raise ServiceLifecycleError(f"config must be AppConfig, got {type(candidate_config).__name__}")

        candidate_context = RuntimeContext(
            app_config=candidate_config,
            epoch_id=candidate_epoch_id,
        )

        candidate_topology = compile_topology(self._registry)

        candidate_services: dict[str, IService] = {}
        candidate_service_states: dict[str, ServiceState] = {}

        for name in candidate_topology:
            reg = self._registry[name]
            try:
                service = reg.factory()
            except Exception as fe:
                raise ServiceLifecycleError(f"Factory for service '{name}' raised exception: {fe}") from fe

            if not isinstance(service, IService):
                raise ServiceLifecycleError(
                    f"Factory for '{name}' returned invalid instance: {type(service).__name__}"
                )
            if service.name != name:
                raise ServiceLifecycleError(
                    f"Service instance name '{service.name}' does not match registration '{name}'"
                )
            if service.dependencies != reg.dependencies:
                raise ServiceLifecycleError(
                    f"Service instance dependencies {service.dependencies} do not match registration {reg.dependencies}"
                )

            # Cumulative freshness validation across all historical epochs
            for prev_s in self._previous_epoch_instances:
                if service is prev_s:
                    raise ServiceLifecycleError(
                        f"Factory returned reused service instance '{name}' from a previous historical epoch"
                    )

            if service in candidate_services.values():
                raise ServiceLifecycleError(f"Factory returned duplicate instance for service '{name}'")

            candidate_services[name] = service
            candidate_service_states[name] = ServiceState.UNINITIALIZED

        # 3. Phase 2: Atomic Activation
        # All Phase 1 validation has passed; now mutate engine state atomically.
        self._state = RuntimeState.INITIALIZING
        self._active_epoch = ConfigurationEpoch(
            epoch_id=candidate_epoch_id,
            config=candidate_config,
            context=candidate_context,
            topology=candidate_topology,
            registrations=dict(self._registry),
        )
        self._next_epoch_id += 1
        self._services = candidate_services
        self._service_states = candidate_service_states
        self._active_topology = candidate_topology
        # Secret filter registration deferred to Phase 2 to maintain Phase 1 zero-mutation guarantee.
        if candidate_config.gemini_api_key:
            self._secret_filter.set_secrets((candidate_config.gemini_api_key,))

        # 4. Phase 3: Service Initialization in Topological Order
        initialized_services: list[str] = []
        for name in self._active_topology:
            service = self._services[name]
            try:
                service.initialize(candidate_context)
                self._service_states[name] = ServiceState.INITIALIZED
                initialized_services.append(name)
            except Exception as e:
                self._last_error = e
                self._service_states[name] = ServiceState.FAILED
                # Reverse rollback initialized services
                for init_name in reversed(initialized_services):
                    init_service = self._services[init_name]
                    try:
                        init_service.stop()
                        if init_service.resources.is_empty:
                            self._service_states[init_name] = ServiceState.STOPPED
                        else:
                            self._service_states[init_name] = ServiceState.FAILED
                    except Exception:
                        self._service_states[init_name] = ServiceState.FAILED

                self._state = RuntimeState.FAILED
                raise ServiceInitializationError(f"Service initialization failed on '{name}': {e}") from e

        self._state = RuntimeState.READY

    def start(self) -> None:
        """Start all initialized services in deterministic topological order."""
        if self._state == RuntimeState.RUNNING:
            return  # State-preserving operation
        if self._state != RuntimeState.READY:
            raise InvalidStateTransitionError(f"Cannot start engine from state: {self._state.name}")

        started_services: list[str] = []
        for name in self._active_topology:
            service = self._services[name]
            resources_before = frozenset(service.resources.outstanding_handles)
            try:
                service.start()
                resources_after = frozenset(service.resources.outstanding_handles)
                if resources_after != resources_before:
                    self._service_states[name] = ServiceState.FAILED
                    raise ServiceStartupError(
                        f"Service '{name}' acquired new owned resources during start(), which is strictly forbidden"
                    )
                self._service_states[name] = ServiceState.STARTED
                started_services.append(name)
            except Exception as e:
                self._last_error = e
                self._service_states[name] = ServiceState.FAILED
                # Reverse rollback started services
                for started_name in reversed(started_services):
                    started_service = self._services[started_name]
                    try:
                        started_service.stop()
                        if started_service.resources.is_empty:
                            self._service_states[started_name] = ServiceState.STOPPED
                        else:
                            self._service_states[started_name] = ServiceState.FAILED
                    except Exception:
                        self._service_states[started_name] = ServiceState.FAILED

                self._state = RuntimeState.FAILED
                if isinstance(e, ServiceStartupError):
                    raise e
                raise ServiceStartupError(f"Service startup failed on '{name}': {e}") from e

        self._state = RuntimeState.RUNNING

    def stop(self) -> None:
        """Stop engine or retire cleanly stopped/failed epoch."""
        if self._state == RuntimeState.NEW:
            self._state = RuntimeState.STOPPED
            return
        if self._state == RuntimeState.STOPPED:
            return  # State-preserving operation
        if self._state in (RuntimeState.READY, RuntimeState.RUNNING):
            self._state = RuntimeState.STOPPING
            try:
                self._teardown_services()
                self._retire_epoch(final_engine_state=RuntimeState.STOPPED)
                self._state = RuntimeState.STOPPED
            except ServiceShutdownError as se:
                self._state = RuntimeState.FAILED
                self._last_error = se
                raise se
            return
        if self._state == RuntimeState.FAILED:
            if not self.is_resource_clean:
                unreleased = [h.resource_id for h in self.outstanding_resources]
                raise ResourceCleanupRequiredError(
                    f"Cannot stop/retire failed engine with unreleased resources: {unreleased}"
                )
            self._retire_epoch(final_engine_state=RuntimeState.STOPPED)
            self._state = RuntimeState.STOPPED
            return

        raise InvalidStateTransitionError(f"Cannot stop engine from state: {self._state.name}")

    def retry_cleanup(self) -> None:
        """Execute cleanup attempts for failed epoch targeting dirty services."""
        if self._state != RuntimeState.FAILED:
            raise InvalidStateTransitionError(f"retry_cleanup requires FAILED state, current: {self._state.name}")

        try:
            self._retry_failed_cleanup()
        except ServiceShutdownError as se:
            unreleased = [h.resource_id for h in self.outstanding_resources]
            err = ResourceCleanupRequiredError(f"Cleanup retry incomplete; unreleased resources remain: {unreleased}")
            err.__cause__ = se
            raise err from se

        if not self.is_resource_clean:
            unreleased = [h.resource_id for h in self.outstanding_resources]
            raise ResourceCleanupRequiredError(f"Cleanup retry incomplete; unreleased resources remain: {unreleased}")

    def _teardown_services(self) -> None:
        """Execute reverse-topological shutdown and aggregate all failures."""
        failures: list[ShutdownFailureRecord] = []
        attempt_index = 0

        for name in reversed(self._active_topology):
            service = self._services.get(name)
            if service is None:
                continue

            try:
                service.stop()
                if service.resources.is_empty:
                    self._service_states[name] = ServiceState.STOPPED
                else:
                    self._service_states[name] = ServiceState.FAILED
                    unreleased = tuple(h.resource_id for h in service.resources.outstanding_handles)
                    failures.append(
                        ShutdownFailureRecord(
                            service_name=name,
                            original_exception=ResourceCleanupRequiredError(
                                f"Unreleased resources remain in '{name}'"
                            ),
                            execution_index=attempt_index,
                            unreleased_resources=unreleased,
                        )
                    )
            except Exception as e:
                self._service_states[name] = ServiceState.FAILED
                unreleased = tuple(h.resource_id for h in service.resources.outstanding_handles)
                failures.append(
                    ShutdownFailureRecord(
                        service_name=name,
                        original_exception=e,
                        execution_index=attempt_index,
                        unreleased_resources=unreleased,
                    )
                )
            attempt_index += 1

        if failures:
            raise ServiceShutdownError(
                f"Teardown failed on {len(failures)} services",
                failures=tuple(failures),
            )

    def _retry_failed_cleanup(self) -> None:
        """Execute cleanup attempts targeting ONLY dirty services with outstanding resources."""
        failures: list[ShutdownFailureRecord] = []
        attempt_index = 0

        for name in reversed(self._active_topology):
            service = self._services.get(name)
            if service is None or service.resources.is_empty:
                continue  # Skips clean services

            try:
                service.stop()
                if service.resources.is_empty:
                    self._service_states[name] = ServiceState.STOPPED
                else:
                    self._service_states[name] = ServiceState.FAILED
                    unreleased = tuple(h.resource_id for h in service.resources.outstanding_handles)
                    failures.append(
                        ShutdownFailureRecord(
                            service_name=name,
                            original_exception=ResourceCleanupRequiredError(
                                f"Unreleased resources remain in '{name}'"
                            ),
                            execution_index=attempt_index,
                            unreleased_resources=unreleased,
                        )
                    )
            except Exception as e:
                self._service_states[name] = ServiceState.FAILED
                unreleased = tuple(h.resource_id for h in service.resources.outstanding_handles)
                failures.append(
                    ShutdownFailureRecord(
                        service_name=name,
                        original_exception=e,
                        execution_index=attempt_index,
                        unreleased_resources=unreleased,
                    )
                )
            attempt_index += 1

        if failures:
            raise ServiceShutdownError(
                f"Cleanup retry failed on {len(failures)} services",
                failures=tuple(failures),
            )

    def _retire_epoch(self, final_engine_state: RuntimeState) -> None:
        """Retire cleanly stopped/failed epoch and append services to cumulative lifetime retention."""
        if not self.is_resource_clean:
            raise ResourceCleanupRequiredError("Cannot retire epoch while outstanding resources remain")

        if self._active_epoch is not None:
            self._last_retired_diagnostic = EpochDiagnosticRecord(
                epoch_id=self._active_epoch.epoch_id,
                final_engine_state=final_engine_state,
                service_final_states=MappingProxyType(dict(self._service_states)),
                retired_timestamp_utc=datetime.now(timezone.utc).isoformat(),
                error_summary=str(self._last_error) if self._last_error else None,
            )
            # Cumulative lifetime retention across ALL historical epochs
            self._previous_epoch_instances = self._previous_epoch_instances + tuple(self._services.values())

        self._active_epoch = None
        self._services.clear()
        self._service_states.clear()

    def run_guarded(self, target: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
        """Execute operational callable under strict runtime error boundary."""
        if self._state != RuntimeState.RUNNING:
            raise InvalidStateTransitionError(
                f"run_guarded requires RUNNING state, current: {self._state.name}"
            )
        if not callable(target):
            raise TypeError(f"target must be callable, got {type(target).__name__}")

        try:
            return target(*args, **kwargs)
        except Exception as e:
            self._last_error = e
            self._logger.error(
                "Fatal error during guarded execution",
                extra={
                    "event": "fatal_runtime_error",
                    "runtime_state": self._state.name,
                    "error_type": type(e).__name__,
                },
                exc_info=True,
            )

            teardown_error: Optional[ServiceShutdownError] = None
            try:
                self._teardown_services()
            except ServiceShutdownError as se:
                teardown_error = se
            except Exception as te:
                teardown_error = ServiceShutdownError(
                    "Teardown failed with unexpected error",
                    failures=(
                        ShutdownFailureRecord(
                            service_name="engine",
                            original_exception=te,
                            execution_index=0,
                            unreleased_resources=(),
                        ),
                    ),
                )

            self._state = RuntimeState.FAILED

            if teardown_error is not None:
                teardown_error.__cause__ = e
                wrapper = HoloMedRuntimeError(
                    f"Fatal execution crash in guarded workload and teardown failure: {e}",
                    original_exception=e,
                    teardown_error=teardown_error,
                )
                wrapper.__cause__ = teardown_error
                raise wrapper from teardown_error

            if isinstance(e, HoloMedRuntimeError):
                raise e

            wrapper = HoloMedRuntimeError(
                f"Fatal error during guarded execution: {e}",
                original_exception=e,
            )
            wrapper.__cause__ = e
            raise wrapper from e

    def health(self) -> RuntimeHealth:
        """Produce a synchronous aggregate health snapshot."""
        return HealthEvaluator.evaluate(self._services, self._state)
