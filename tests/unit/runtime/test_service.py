"""Unit tests for service abstractions, registration rules, and topological compiler."""

from dataclasses import FrozenInstanceError
import pytest

from holomed.runtime.context import RuntimeContext
from holomed.runtime.exceptions import (
    ServiceDependencyCycleError,
    ServiceDependencyError,
    ServiceDependencyMissingError,
    ServiceRegistrationError,
)
from holomed.runtime.models import HealthStatus, OwnedResourceSet, ServiceHealth
from holomed.runtime.service import (
    IService,
    ServiceRegistration,
    compile_topology,
    find_cycle,
)


class DummyService(IService):
    def __init__(self, name: str, dependencies: tuple[str, ...] = ()) -> None:
        self._name = name
        self._dependencies = dependencies
        self._resources = OwnedResourceSet(name, epoch_id=1)

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
        pass

    def start(self) -> None:
        pass

    def stop(self) -> None:
        pass

    def health(self) -> ServiceHealth:
        return ServiceHealth(name=self._name, status=HealthStatus.HEALTHY, message="OK", timestamp_utc="")


def test_service_registration_immutable() -> None:
    """Assert ServiceRegistration instances are strictly immutable."""
    reg = ServiceRegistration(name="svc.a", factory=lambda: DummyService("svc.a"), dependencies=())
    with pytest.raises((FrozenInstanceError, AttributeError)):
        reg.name = "svc.b"  # type: ignore


def test_service_registration_type_safety() -> None:
    """Assert ServiceRegistration constructor enforces strict parameter types and formats."""
    # Invalid name
    with pytest.raises(ServiceRegistrationError):
        ServiceRegistration(name="InvalidName", factory=lambda: DummyService("a"), dependencies=())
    with pytest.raises(ServiceRegistrationError):
        ServiceRegistration(name="", factory=lambda: DummyService("a"), dependencies=())
    with pytest.raises(ServiceRegistrationError):
        ServiceRegistration(name="a" * 65, factory=lambda: DummyService("a"), dependencies=())
    with pytest.raises(ServiceRegistrationError):
        ServiceRegistration(name=123, factory=lambda: DummyService("a"), dependencies=())  # type: ignore

    # Non-callable factory
    with pytest.raises(ServiceRegistrationError):
        ServiceRegistration(name="svc.a", factory="not-callable", dependencies=())  # type: ignore

    # Invalid dependencies
    with pytest.raises(ServiceDependencyError):
        ServiceRegistration(name="svc.a", factory=lambda: DummyService("svc.a"), dependencies=["svc.b"])  # type: ignore
    with pytest.raises(ServiceDependencyError):
        ServiceRegistration(name="svc.a", factory=lambda: DummyService("svc.a"), dependencies=("svc.a",))  # self-dep
    with pytest.raises(ServiceDependencyError):
        ServiceRegistration(name="svc.a", factory=lambda: DummyService("svc.a"), dependencies=("svc.b", "svc.b"))  # duplicate
    with pytest.raises(ServiceDependencyError):
        ServiceRegistration(name="svc.a", factory=lambda: DummyService("svc.a"), dependencies=("Invalid.Dep",))


def test_deterministic_topological_order() -> None:
    """Assert compile_topology generates deterministic alphabetical tie-breaking order."""
    registry = {
        "z.svc": ServiceRegistration("z.svc", lambda: DummyService("z.svc"), ()),
        "a.svc": ServiceRegistration("a.svc", lambda: DummyService("a.svc"), ()),
        "m.svc": ServiceRegistration("m.svc", lambda: DummyService("m.svc"), ()),
        "b.svc": ServiceRegistration("b.svc", lambda: DummyService("b.svc"), ("a.svc",)),
    }
    topology = compile_topology(registry)
    # Tie-breaking with no dependencies: a.svc, m.svc, z.svc. b.svc depends on a.svc.
    # Ready initially: a.svc, m.svc, z.svc -> pops a.svc, b.svc ready.
    # Priority queue order: a.svc, b.svc, m.svc, z.svc
    assert topology == ("a.svc", "b.svc", "m.svc", "z.svc")


def test_cycle_and_missing_dependency_rejection() -> None:
    """Assert compile_topology detects missing dependencies and cycles with canonical reporting."""
    # Missing dependency
    reg_missing = {
        "svc.a": ServiceRegistration("svc.a", lambda: DummyService("svc.a"), ("svc.unregistered",)),
    }
    with pytest.raises(ServiceDependencyMissingError, match="unregistered"):
        compile_topology(reg_missing)

    # 2-node cycle
    reg_cycle = {
        "svc.a": ServiceRegistration("svc.a", lambda: DummyService("svc.a"), ("svc.b",)),
        "svc.b": ServiceRegistration("svc.b", lambda: DummyService("svc.b"), ("svc.a",)),
    }
    with pytest.raises(ServiceDependencyCycleError, match="svc.a -> svc.b -> svc.a"):
        compile_topology(reg_cycle)
