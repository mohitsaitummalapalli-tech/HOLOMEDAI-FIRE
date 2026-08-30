"""Runtime state, health, resource, and epoch data models."""

from dataclasses import dataclass
from datetime import datetime, timezone
import enum
from types import MappingProxyType
from typing import TYPE_CHECKING, Any, Mapping, Optional

from holomed.configuration.models import AppConfig
from holomed.runtime.exceptions import ServiceLifecycleError

if TYPE_CHECKING:
    from holomed.runtime.context import RuntimeContext
    from holomed.runtime.service import ServiceRegistration, ServiceState


class RuntimeState(enum.Enum):
    """Authoritative 7-state lifecycle machine for the RuntimeEngine."""

    NEW = "NEW"
    INITIALIZING = "INITIALIZING"
    READY = "READY"
    RUNNING = "RUNNING"
    STOPPING = "STOPPING"
    STOPPED = "STOPPED"
    FAILED = "FAILED"


class HealthStatus(enum.Enum):
    """In-process health severity statuses."""

    HEALTHY = "HEALTHY"
    DEGRADED = "DEGRADED"
    UNHEALTHY = "UNHEALTHY"
    FAILED = "FAILED"


@dataclass(frozen=True)
class ServiceHealth:
    """Immutable health snapshot for an individual service."""

    name: str
    status: HealthStatus
    message: str
    timestamp_utc: str


@dataclass(frozen=True)
class RuntimeHealth:
    """Immutable aggregate health report for the entire runtime engine."""

    engine_state: RuntimeState
    aggregate_status: HealthStatus
    services: Mapping[str, ServiceHealth]
    timestamp_utc: str

    def __post_init__(self) -> None:
        if not isinstance(self.services, MappingProxyType):
            sorted_services = {k: self.services[k] for k in sorted(self.services.keys())}
            object.__setattr__(self, "services", MappingProxyType(sorted_services))

    def to_dict(self) -> dict[str, Any]:
        """Convert health snapshot to a JSON-serializable dictionary."""
        return {
            "engine_state": self.engine_state.name,
            "aggregate_status": self.aggregate_status.name,
            "services": {
                name: {
                    "name": sh.name,
                    "status": sh.status.name,
                    "message": sh.message,
                    "timestamp_utc": sh.timestamp_utc,
                }
                for name, sh in self.services.items()
            },
            "timestamp_utc": self.timestamp_utc,
        }


class ResourceStatus(enum.Enum):
    """Resource ownership states."""

    ACQUIRED = "ACQUIRED"
    RELEASED = "RELEASED"
    UNRELEASED_FAILURE = "UNRELEASED_FAILURE"


@dataclass(frozen=True)
class ResourceHandle:
    """Immutable handle identifying an owned runtime resource."""

    resource_id: str
    service_name: str
    epoch_id: int


@dataclass(frozen=True)
class ResourceOwnershipRecord:
    """Detailed audit record of resource acquisition and release status."""

    handle: ResourceHandle
    status: ResourceStatus
    acquired_timestamp_utc: str
    released_timestamp_utc: Optional[str] = None
    release_error: Optional[str] = None


class OwnedResourceSet:
    """Mechanically verifiable resource tracking contract for services and test doubles."""

    def __init__(self, service_name: str, epoch_id: int) -> None:
        self.service_name = service_name
        self.epoch_id = epoch_id
        self._records: dict[str, ResourceOwnershipRecord] = {}

    def acquire(self, resource_id: str) -> ResourceHandle:
        """Register a new acquired resource handle."""
        if not isinstance(resource_id, str) or not resource_id:
            raise ServiceLifecycleError("resource_id must be a non-empty string")
        if resource_id in self._records and self._records[resource_id].status in (
            ResourceStatus.ACQUIRED,
            ResourceStatus.UNRELEASED_FAILURE,
        ):
            raise ServiceLifecycleError(f"Duplicate active resource ID '{resource_id}' in service '{self.service_name}'")
        handle = ResourceHandle(resource_id=resource_id, service_name=self.service_name, epoch_id=self.epoch_id)
        self._records[resource_id] = ResourceOwnershipRecord(
            handle=handle,
            status=ResourceStatus.ACQUIRED,
            acquired_timestamp_utc=datetime.now(timezone.utc).isoformat(),
        )
        return handle

    def release(self, resource_id: str) -> None:
        """Confirm deterministic release of an owned resource."""
        if resource_id not in self._records:
            raise ServiceLifecycleError(
                f"Cannot release unknown resource ID '{resource_id}' in service '{self.service_name}'"
            )
        rec = self._records[resource_id]
        if rec.status == ResourceStatus.RELEASED:
            return  # Idempotent release
        self._records[resource_id] = ResourceOwnershipRecord(
            handle=rec.handle,
            status=ResourceStatus.RELEASED,
            acquired_timestamp_utc=rec.acquired_timestamp_utc,
            released_timestamp_utc=datetime.now(timezone.utc).isoformat(),
        )

    def mark_release_failed(self, resource_id: str, error: str) -> None:
        """Record a failed cleanup attempt for an owned resource."""
        if resource_id not in self._records:
            raise ServiceLifecycleError(
                f"Cannot mark release failed on unknown resource ID '{resource_id}' in service '{self.service_name}'"
            )
        rec = self._records[resource_id]
        self._records[resource_id] = ResourceOwnershipRecord(
            handle=rec.handle,
            status=ResourceStatus.UNRELEASED_FAILURE,
            acquired_timestamp_utc=rec.acquired_timestamp_utc,
            release_error=error,
        )

    @property
    def outstanding_handles(self) -> frozenset[ResourceHandle]:
        """Returns all currently owned resources (both ACQUIRED and UNRELEASED_FAILURE)."""
        return frozenset(
            rec.handle
            for rec in self._records.values()
            if rec.status in (ResourceStatus.ACQUIRED, ResourceStatus.UNRELEASED_FAILURE)
        )

    @property
    def is_empty(self) -> bool:
        """True if and only if zero acquired or unreleased resources remain."""
        return len(self.outstanding_handles) == 0

    @property
    def records(self) -> Mapping[str, ResourceOwnershipRecord]:
        """Immutable view of internal resource records."""
        return MappingProxyType(dict(self._records))


@dataclass(frozen=True)
class ConfigurationEpoch:
    """Genuinely immutable snapshot of active configuration and service registrations."""

    epoch_id: int
    config: AppConfig
    context: "RuntimeContext"
    topology: tuple[str, ...]
    registrations: Mapping[str, "ServiceRegistration"]

    def __post_init__(self) -> None:
        if not isinstance(self.registrations, MappingProxyType):
            object.__setattr__(self, "registrations", MappingProxyType(dict(self.registrations)))


@dataclass(frozen=True)
class EpochDiagnosticRecord:
    """Immutable diagnostic record of a retired epoch."""

    epoch_id: int
    final_engine_state: RuntimeState
    service_final_states: Mapping[str, Any]
    retired_timestamp_utc: str
    error_summary: Optional[str] = None
