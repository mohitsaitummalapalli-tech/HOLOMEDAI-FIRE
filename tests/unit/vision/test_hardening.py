"""Hardening, AST import validation, topological compilation, and determinism tests for M01."""

from __future__ import annotations

import ast
import json
from pathlib import Path
import pytest
from holomed.configuration.models import SecretString
from holomed.devices.exceptions import (
    DeviceCapacityError,
    DeviceLifecycleError,
    DeviceShutdownError,
    DeviceValidationError,
)
from holomed.devices.models import DeviceShutdownFailureRecord
from holomed.runtime.logging import SecretFilter
from holomed.runtime.service import ServiceRegistration, compile_topology
from holomed.vision.exceptions import (
    VisionCapacityError,
    VisionError,
    VisionLifecycleError,
    VisionShutdownError,
    VisionValidationError,
)
from holomed.vision.pipeline import VisionPipeline
from holomed.vision.store import VisionFrameStore
from tests.unit.vision.conftest import make_test_frame

PROHIBITED_MODULES = {
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


def test_topological_dependency_minimal_footprint() -> None:
    """Verify Kahn's topological sort compiles cleanly with vision_service depending solely on device_manager."""
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
        "vision.service": ServiceRegistration(
            "vision.service",
            lambda: None,
            ("device.mgr",),  # Minimal footprint (Issue 9)
        ),
    }

    order = compile_topology(regs)
    assert order[0] == "device.mgr"
    assert "vision.service" in order
    # Verify vision.service comes after device.mgr
    assert order.index("device.mgr") < order.index("vision.service")


def test_ast_prohibited_imports() -> None:
    """Scan all Python source files in holomed.vision to guarantee 0 prohibited imports."""
    vision_src_dir = Path("python/holomed/vision")
    assert vision_src_dir.is_dir(), f"Directory not found: {vision_src_dir}"

    violations = []
    for py_file in vision_src_dir.glob("*.py"):
        code = py_file.read_text(encoding="utf-8")
        tree = ast.parse(code, filename=str(py_file))

        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    for prohibited in PROHIBITED_MODULES:
                        if alias.name == prohibited or alias.name.startswith(f"{prohibited}."):
                            violations.append((py_file.name, alias.name))
            elif isinstance(node, ast.ImportFrom):
                if node.module:
                    for prohibited in PROHIBITED_MODULES:
                        if node.module == prohibited or node.module.startswith(f"{prohibited}."):
                            violations.append((py_file.name, node.module))

    assert len(violations) == 0, f"Prohibited imports detected: {violations}"


def test_exception_hierarchy_multiple_inheritance() -> None:
    """Verify multiple-inheritance exception structure conforms to M00 base handlers."""
    # VisionShutdownError
    rec = DeviceShutdownFailureRecord(
        device_id="res_1",
        error_type="RuntimeError",
        error_message="msg",
        execution_index=0,
        unreleased_resources=("res_1",),
    )
    shut_err = VisionShutdownError("Shutdown failed", [rec])
    assert isinstance(shut_err, VisionError)
    assert isinstance(shut_err, DeviceShutdownError)
    assert shut_err.failures == (rec,)

    # VisionLifecycleError
    life_err = VisionLifecycleError("Invalid state")
    assert isinstance(life_err, VisionError)
    assert isinstance(life_err, DeviceLifecycleError)

    # VisionCapacityError
    cap_err = VisionCapacityError("Buffer exhausted")
    assert isinstance(cap_err, VisionError)
    assert isinstance(cap_err, DeviceCapacityError)

    # VisionValidationError
    val_err = VisionValidationError("Bad coordinate")
    assert isinstance(val_err, VisionError)
    assert isinstance(val_err, DeviceValidationError)


def test_determinism_identical_inputs_reproducible() -> None:
    """Processing identical frame byte sequences produces bit-for-bit identical results."""
    desc, data = make_test_frame(width=120, height=80)

    # Run 1
    store1 = VisionFrameStore()
    pipe1 = VisionPipeline()
    h1 = store1.allocate_slot(desc, data)
    v1 = store1.get_readonly_view(h1)
    res1 = pipe1.process(desc, v1)
    store1.release_slot(h1)

    # Run 2
    store2 = VisionFrameStore()
    pipe2 = VisionPipeline()
    h2 = store2.allocate_slot(desc, data)
    v2 = store2.get_readonly_view(h2)
    res2 = pipe2.process(desc, v2)
    store2.release_slot(h2)

    # Convert landmarks to canonical serialization
    lm1_dump = json.dumps([
        {"id": lm.landmark_id, "u": lm.u, "v": lm.v, "depth": lm.depth_m, "conf": lm.confidence}
        for lm in res1.landmarks
    ], sort_keys=True)
    lm2_dump = json.dumps([
        {"id": lm.landmark_id, "u": lm.u, "v": lm.v, "depth": lm.depth_m, "conf": lm.confidence}
        for lm in res2.landmarks
    ], sort_keys=True)

    assert lm1_dump == lm2_dump
    assert res1.quality == res2.quality


def test_secret_redaction_in_diagnostics() -> None:
    """Diagnostics and failure strings pass through SecretFilter."""
    sf = SecretFilter()
    sf.set_secrets([SecretString("SuperSecretMedicalToken")])

    pipe = VisionPipeline(secret_filter=sf)
    desc, data = make_test_frame(width=64, height=64)

    # Mock stage 1 to raise with secret string
    def bad_validate(*args, **kwargs):
        raise RuntimeError("Validation crashed with SuperSecretMedicalToken inside")

    pipe._validate_frame = bad_validate
    view = memoryview(data)
    result = pipe.process(desc, view)

    assert result.error_message is not None
    assert "SuperSecretMedicalToken" not in result.error_message
    assert "<redacted>" in result.error_message
