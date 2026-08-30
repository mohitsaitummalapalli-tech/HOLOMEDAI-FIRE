"""Runtime execution subsystem exceptions and error data models."""

from dataclasses import dataclass
from typing import Optional

from holomed.common.exceptions import HoloMedError


class HoloMedRuntimeError(HoloMedError):
    """Root runtime execution exception with support for causal chain tracking."""

    def __init__(
        self,
        message: str,
        original_exception: Optional[Exception] = None,
        teardown_error: Optional["ServiceShutdownError"] = None,
    ) -> None:
        super().__init__(message)
        self.original_exception = original_exception
        self.teardown_error = teardown_error


class InvalidStateTransitionError(HoloMedRuntimeError):
    """Raised when a forbidden engine or service state transition is attempted."""

    pass


class ResourceCleanupRequiredError(HoloMedRuntimeError):
    """Raised when an operation is rejected because unreleased outstanding resources remain."""

    pass


class ServiceLifecycleError(HoloMedRuntimeError):
    """Base exception for service lifecycle failures."""

    pass


class ServiceInitializationError(ServiceLifecycleError):
    """Raised when a service fails during initialize()."""

    pass


class ServiceStartupError(ServiceLifecycleError):
    """Raised when a service fails during start() or illegally acquires new resources."""

    pass


class ServiceDependencyError(HoloMedRuntimeError):
    """Base exception for service dependency and registration topology errors."""

    pass


class ServiceRegistrationError(ServiceDependencyError):
    """Raised when service registration parameters fail validation."""

    pass


class ServiceDuplicateRegistrationError(ServiceDependencyError):
    """Raised when duplicate service name is registered."""

    pass


class ServiceDependencyMissingError(ServiceDependencyError):
    """Raised when a service declares a dependency that is not registered."""

    pass


class ServiceDependencyCycleError(ServiceDependencyError):
    """Raised when a cyclic dependency graph is detected."""

    pass


class HealthCheckError(HoloMedRuntimeError):
    """Raised when health check evaluation encounters an unhandled failure."""

    pass


@dataclass(frozen=True)
class ShutdownFailureRecord:
    """Diagnostic record of an individual service shutdown failure."""

    service_name: str
    original_exception: Exception
    execution_index: int
    unreleased_resources: tuple[str, ...]


class ServiceShutdownError(HoloMedRuntimeError):
    """Aggregated shutdown error containing all individual service stop failures."""

    def __init__(self, message: str, failures: tuple[ShutdownFailureRecord, ...]) -> None:
        super().__init__(message)
        self.failures = failures
