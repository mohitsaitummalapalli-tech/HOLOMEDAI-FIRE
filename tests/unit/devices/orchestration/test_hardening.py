"""Hardening, AST prohibited import verification, and topological tests for M00.9."""

import ast
from pathlib import Path
import pytest

from holomed.devices.exceptions import (
    DeviceCapacityError,
    DeviceError,
    DeviceLifecycleError,
    DeviceResourceIntegrityError,
    DeviceShutdownError,
    DeviceValidationError,
)
from holomed.devices.orchestration.exceptions import (
    DeviceOrchestrationBridgeError,
    DeviceOrchestrationCapacityError,
    DeviceOrchestrationError,
    DeviceOrchestrationIntegrityError,
    DeviceOrchestrationLifecycleError,
    DeviceOrchestrationSerializationError,
    DeviceOrchestrationShutdownError,
    DeviceOrchestrationValidationError,
)
from holomed.devices.orchestration.orchestrator import DevicePlaneOrchestrator
from holomed.runtime.service import ServiceRegistration, compile_topology
from tests.unit.devices.orchestration.conftest import make_test_context


def test_exception_multiple_inheritance_conformance() -> None:
    """Verify that all M00.9 exceptions satisfy both domain and base catch blocks (D173)."""
    assert issubclass(DeviceOrchestrationError, DeviceError)

    assert issubclass(DeviceOrchestrationLifecycleError, DeviceOrchestrationError)
    assert issubclass(DeviceOrchestrationLifecycleError, DeviceLifecycleError)

    assert issubclass(DeviceOrchestrationCapacityError, DeviceOrchestrationError)
    assert issubclass(DeviceOrchestrationCapacityError, DeviceCapacityError)

    assert issubclass(DeviceOrchestrationIntegrityError, DeviceOrchestrationError)
    assert issubclass(DeviceOrchestrationIntegrityError, DeviceResourceIntegrityError)

    assert issubclass(DeviceOrchestrationValidationError, DeviceOrchestrationError)
    assert issubclass(DeviceOrchestrationValidationError, DeviceValidationError)

    assert issubclass(DeviceOrchestrationBridgeError, DeviceOrchestrationError)

    assert issubclass(DeviceOrchestrationShutdownError, DeviceOrchestrationError)
    assert issubclass(DeviceOrchestrationShutdownError, DeviceShutdownError)


def test_topological_compiler_clean_acyclic_order() -> None:
    """Verify Kahn's topological sort compiles cleanly with device_orchestration at highest rank (D167)."""
    regs = {
        "device.mgr": ServiceRegistration("device.mgr", lambda: None, ()),
        "device.ctrl": ServiceRegistration("device.ctrl", lambda: None, ("device.mgr",)),
        "device.data": ServiceRegistration("device.data", lambda: None, ("device.mgr",)),
        "device.coord": ServiceRegistration("device.coord", lambda: None, ("device.mgr",)),
        "device.orch": ServiceRegistration(
            "device.orch",
            lambda: None,
            ("device.mgr", "device.ctrl", "device.data", "device.coord"),
        ),
    }

    order = compile_topology(regs)
    assert order[0] == "device.mgr"
    assert order[-1] == "device.orch"
    assert set(order[1:4]) == {"device.ctrl", "device.coord", "device.data"}


def test_ast_prohibited_imports() -> None:
    """Ensure zero prohibited imports across python/holomed/devices/orchestration."""
    prohibited = {
        "socket",
        "urllib.request",
        "http.client",
        "requests",
        "aiohttp",
        "asyncio",
        "threading",
        "multiprocessing",
        "subprocess",
        "cv2",
        "mediapipe",
        "pyaudio",
        "serial",
    }

    source_dir = Path("python/holomed/devices/orchestration")
    assert source_dir.exists()

    violations = []
    for py_file in source_dir.glob("*.py"):
        tree = ast.parse(py_file.read_text("utf-8"), filename=str(py_file))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if any(alias.name == p or alias.name.startswith(p + ".") for p in prohibited):
                        violations.append((py_file.name, alias.name))
            elif isinstance(node, ast.ImportFrom):
                if node.module:
                    if any(node.module == p or node.module.startswith(p + ".") for p in prohibited):
                        violations.append((py_file.name, node.module))

    assert len(violations) == 0, f"Prohibited imports detected: {violations}"


def test_orchestrator_resource_failure_marks_failed(device_subsystem_stack) -> None:
    """Verify that resource release failures leave service in FAILED state and raise DeviceOrchestrationShutdownError."""
    ctx = make_test_context(epoch_id=1)
    orch = DevicePlaneOrchestrator(
        device_manager=device_subsystem_stack["manager"],
        control_manager=device_subsystem_stack["control"],
        data_processor=device_subsystem_stack["data"],
        coordination=device_subsystem_stack["coordination"],
    )
    orch.initialize(ctx)
    orch.start()

    # Sabotage resource release
    def bad_release(handle_id):
        raise RuntimeError(f"Hardware bus hung for {handle_id}")

    orch.resources.release = bad_release  # type: ignore

    with pytest.raises(DeviceOrchestrationShutdownError) as exc_info:
        orch.stop()

    assert len(exc_info.value.failures) == 3
    from holomed.runtime.service import ServiceState
    assert orch._state == ServiceState.FAILED
