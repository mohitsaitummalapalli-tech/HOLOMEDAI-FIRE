"""Hardening, security boundary, and AST import compliance tests for M00.8."""

import ast
from pathlib import Path
import pytest

from holomed.configuration.models import SecretString
from holomed.devices.coordination.coordinator import DeviceCoordinationService
from holomed.devices.coordination.exceptions import DeviceCoordinationShutdownError
from holomed.devices.interfaces import RegistryAuthorityToken
from holomed.devices.models import DeviceState, DeviceType
from holomed.devices.registry import DeviceRegistry
from holomed.runtime.logging import SecretFilter
from holomed.runtime.models import ResourceStatus
from holomed.runtime.service import ServiceRegistration, compile_topology
from tests.unit.devices.coordination.conftest import (
    DummyCoordinationDevice,
    make_test_context,
    register_device,
)

PROHIBITED_MODULES = {
    "socket",
    "asyncio",
    "threading",
    "multiprocessing",
    "subprocess",
    "cv2",
    "mediapipe",
    "pyaudio",
    "serial",
    "urllib.request",
    "http.client",
    "requests",
    "aiohttp",
}


def test_ast_prohibited_imports_audit():
    """Verify ATK-30: Zero prohibited modules imported across holomed.devices.coordination."""
    pkg_dir = Path(__file__).parents[4] / "python" / "holomed" / "devices" / "coordination"
    py_files = list(pkg_dir.glob("*.py"))
    assert len(py_files) >= 6, "Expected at least 6 source files in coordination package"

    found_prohibited = []
    for py_file in py_files:
        with open(py_file, "r", encoding="utf-8") as f:
            tree = ast.parse(f.read(), filename=str(py_file))

        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    for prohibited in PROHIBITED_MODULES:
                        if alias.name == prohibited or alias.name.startswith(f"{prohibited}."):
                            found_prohibited.append((py_file.name, alias.name))
            elif isinstance(node, ast.ImportFrom):
                if node.module:
                    for prohibited in PROHIBITED_MODULES:
                        if node.module == prohibited or node.module.startswith(f"{prohibited}."):
                            found_prohibited.append((py_file.name, node.module))

    assert found_prohibited == [], f"Prohibited imports discovered: {found_prohibited}"


def test_topological_compiler_compatibility():
    """Verify Kahn's topological sorting with dot-separated names."""
    registry = {
        "device.manager": ServiceRegistration(
            name="device.manager",
            factory=lambda: None,
            dependencies=(),
        ),
        "device.coordination": ServiceRegistration(
            name="device.coordination",
            factory=lambda: None,
            dependencies=("device.manager",),
        ),
    }
    topology = compile_topology(registry)
    assert topology == ("device.manager", "device.coordination")


def test_shutdown_release_failure_aggregation(device_registry: DeviceRegistry):
    """Verify ATK-25: Resource release failures are aggregated into DeviceCoordinationShutdownError."""
    coordinator = DeviceCoordinationService(device_registry)
    coordinator.initialize(make_test_context(epoch_id=1))
    coordinator.start()

    # Monkeypatch one resource to fail on release
    orig_release = coordinator.resources.release

    def failing_release(res_id: str):
        if res_id == "coordination.telemetry":
            raise RuntimeError("Hardware bus hung on telemetry handle release")
        orig_release(res_id)

    coordinator.resources.release = failing_release

    with pytest.raises(DeviceCoordinationShutdownError) as exc_info:
        coordinator.stop()

    assert len(exc_info.value.failures) == 1
    failure = exc_info.value.failures[0]
    assert failure.unreleased_resources == ("coordination.telemetry",)
    assert "Hardware bus hung" in failure.error_message


def test_secret_redaction_across_observations(device_registry: DeviceRegistry, registry_token: RegistryAuthorityToken):
    """Verify §26 & ATK-35: SecretFilter redacts secrets in device health within snapshots."""
    secret = SecretString("leaked_patient_mrn_999")
    s_filter = SecretFilter([secret])

    dev = DummyCoordinationDevice(
        "cam_01",
        "p1",
        health_message=f"Device error for MRN: {secret.get_secret_value()}",
    )
    register_device(device_registry, registry_token, dev)

    coordinator = DeviceCoordinationService(device_registry, secret_filter=s_filter)
    coordinator.initialize(make_test_context(1))
    coordinator.start()

    snapshot = coordinator.capture_snapshot()
    obs = snapshot.observations[0]

    assert "leaked_patient_mrn_999" not in obs.health_message
    assert "<redacted>" in obs.health_message
