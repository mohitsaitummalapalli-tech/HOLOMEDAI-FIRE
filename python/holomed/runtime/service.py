"""Service abstractions, registration descriptors, and deterministic topological compiler."""

import abc
from dataclasses import dataclass
import enum
import heapq
import re
from typing import Callable, Optional

from holomed.runtime.context import RuntimeContext
from holomed.runtime.exceptions import (
    ServiceDependencyCycleError,
    ServiceDependencyError,
    ServiceDependencyMissingError,
    ServiceRegistrationError,
)
from holomed.runtime.models import OwnedResourceSet, ServiceHealth

MAX_SERVICE_NAME_LENGTH = 64
_SERVICE_NAME_PATTERN = re.compile(r"^[a-z0-9]+(\.[a-z0-9]+)*$")


class ServiceState(enum.Enum):
    """Lifecycle states for individual services managed by RuntimeEngine."""

    UNINITIALIZED = "UNINITIALIZED"
    INITIALIZED = "INITIALIZED"
    STARTED = "STARTED"
    STOPPED = "STOPPED"
    FAILED = "FAILED"


class IService(abc.ABC):
    """Abstract lifecycle interface for all HoloMed runtime services."""

    @property
    @abc.abstractmethod
    def name(self) -> str:
        """Unique dot-separated service identifier."""
        pass

    @property
    @abc.abstractmethod
    def dependencies(self) -> tuple[str, ...]:
        """Declared service dependency names."""
        pass

    @property
    @abc.abstractmethod
    def resources(self) -> OwnedResourceSet:
        """Resource accounting tracker owned by this service."""
        pass

    @abc.abstractmethod
    def initialize(self, context: RuntimeContext) -> None:
        """Acquire owned resources and prepare service for operation."""
        pass

    @abc.abstractmethod
    def start(self) -> None:
        """Activate already-acquired resources. MUST NOT acquire new resources."""
        pass

    @abc.abstractmethod
    def stop(self) -> None:
        """Release all acquired and failed unreleased resources."""
        pass

    @abc.abstractmethod
    def health(self) -> ServiceHealth:
        """Produce a synchronous in-process health snapshot."""
        pass


ServiceFactory = Callable[[], IService]


@dataclass(frozen=True)
class ServiceRegistration:
    """Genuinely immutable service registration descriptor."""

    name: str
    factory: ServiceFactory
    dependencies: tuple[str, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.name, str):
            raise ServiceRegistrationError(f"Service name must be str, got {type(self.name).__name__}")
        if not (1 <= len(self.name) <= MAX_SERVICE_NAME_LENGTH):
            raise ServiceRegistrationError(
                f"Service name length must be 1..{MAX_SERVICE_NAME_LENGTH}, got {len(self.name)}"
            )
        if not _SERVICE_NAME_PATTERN.match(self.name):
            raise ServiceRegistrationError(f"Invalid service name format: '{self.name}'")
        if not callable(self.factory):
            raise ServiceRegistrationError(f"Factory for '{self.name}' must be callable")
        if not isinstance(self.dependencies, tuple):
            raise ServiceDependencyError(
                f"Dependencies for '{self.name}' must be a tuple, got {type(self.dependencies).__name__}"
            )

        seen_deps: set[str] = set()
        for dep in self.dependencies:
            if not isinstance(dep, str):
                raise ServiceDependencyError(f"Dependency identifier must be str, got {type(dep).__name__}")
            if not (1 <= len(dep) <= MAX_SERVICE_NAME_LENGTH) or not _SERVICE_NAME_PATTERN.match(dep):
                raise ServiceDependencyError(f"Invalid dependency name format: '{dep}'")
            if dep in seen_deps:
                raise ServiceDependencyError(f"Duplicate dependency '{dep}' declared for '{self.name}'")
            seen_deps.add(dep)
            if dep == self.name:
                raise ServiceDependencyError(f"Service '{self.name}' cannot depend on itself")


def find_cycle(registry: dict[str, ServiceRegistration]) -> Optional[str]:
    """Deterministically detect dependency cycles using DFS with canonical rotation."""
    sorted_names = sorted(registry.keys())
    visited: set[str] = set()
    rec_stack: list[str] = []

    def dfs(node: str) -> Optional[list[str]]:
        visited.add(node)
        rec_stack.append(node)
        for dep in sorted(registry[node].dependencies):
            if dep not in registry:
                continue
            if dep in rec_stack:
                cycle_idx = rec_stack.index(dep)
                return rec_stack[cycle_idx:] + [dep]
            if dep not in visited:
                res = dfs(dep)
                if res:
                    return res
        rec_stack.pop()
        return None

    for name in sorted_names:
        if name not in visited:
            cycle = dfs(name)
            if cycle:
                # Canonicalize cycle: rotate to lexicographically smallest node
                cycle_nodes = cycle[:-1]
                min_idx = cycle_nodes.index(min(cycle_nodes))
                canonical_nodes = cycle_nodes[min_idx:] + cycle_nodes[:min_idx]
                canonical_cycle = canonical_nodes + [canonical_nodes[0]]
                return " -> ".join(canonical_cycle)
    return None


def compile_topology(registry: dict[str, ServiceRegistration]) -> tuple[str, ...]:
    """Compile deterministic topological startup order with alphabetical tie-breaking."""
    # 1. Missing dependency validation
    for name, reg in sorted(registry.items()):
        for dep in reg.dependencies:
            if dep not in registry:
                raise ServiceDependencyMissingError(f"Service '{name}' depends on unregistered service '{dep}'")

    # 2. Cycle detection
    cycle_path = find_cycle(registry)
    if cycle_path:
        raise ServiceDependencyCycleError(f"Cyclic dependency detected: {cycle_path}")

    # 3. Kahn's algorithm with min-heap for deterministic alphabetical tie-breaking
    in_degree: dict[str, int] = {name: len(reg.dependencies) for name, reg in registry.items()}
    dependents: dict[str, list[str]] = {name: [] for name in registry}
    for name, reg in registry.items():
        for dep in reg.dependencies:
            dependents[dep].append(name)

    # Initial ready queue with in_degree == 0
    ready_queue: list[str] = [name for name, deg in in_degree.items() if deg == 0]
    heapq.heapify(ready_queue)

    topology: list[str] = []
    while ready_queue:
        current = heapq.heappop(ready_queue)
        topology.append(current)
        for dep_on_current in sorted(dependents[current]):
            in_degree[dep_on_current] -= 1
            if in_degree[dep_on_current] == 0:
                heapq.heappush(ready_queue, dep_on_current)

    if len(topology) != len(registry):
        raise ServiceDependencyCycleError("Dependency graph contains an unresolved cycle")

    return tuple(topology)
