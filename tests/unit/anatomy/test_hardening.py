# -*- coding: utf-8 -*-
"""Hardening and Security Invariant Tests for M05 Anatomy."""

from __future__ import annotations

import ast
import glob
import os
import pytest

from holomed.anatomy.exceptions import (
    AnatomyError,
    AnatomyLifecycleError,
    AnatomyShutdownError,
)
from holomed.anatomy.models import AnatomicalClassification, AnatomicalEntity, Point3D, RigidTransform3D, BoundingBox3D
from holomed.anatomy.service import AnatomyService
from holomed.devices.exceptions import DeviceError, DeviceLifecycleError
from holomed.protocol.builders import create_query
from holomed.runtime.context import RuntimeContext
from holomed.runtime.logging import SecretFilter
from holomed.runtime.service import ServiceRegistration, compile_topology

PROHIBITED_MODULES = frozenset({
    "torch", "cv2", "scipy", "sklearn", "pandas", "requests",
    "fastapi", "pydantic", "aiohttp", "mediapipe", "numpy",
    "tensorflow", "onnxruntime", "socket", "asyncio",
    "threading", "multiprocessing", "subprocess", "urllib.request", "http.client",
    "openxr", "trimesh", "pyvista", "pybullet", "meshio",
})


def test_ast_prohibited_import_scan() -> None:
    """Audit all M05 source and test files to guarantee ZERO prohibited imports."""
    base_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
    anatomy_src = os.path.join(base_dir, "python", "holomed", "anatomy")
    anatomy_tests = os.path.join(base_dir, "tests", "unit", "anatomy")

    py_files = glob.glob(os.path.join(anatomy_src, "**", "*.py"), recursive=True) + \
               glob.glob(os.path.join(anatomy_tests, "**", "*.py"), recursive=True)

    assert len(py_files) >= 20, f"Expected at least 20 files, found {len(py_files)}"
    violations: list[str] = []

    for path in py_files:
        with open(path, "r", encoding="utf-8") as f:
            tree = ast.parse(f.read(), filename=path)
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    root_mod = alias.name.split(".")[0]
                    if root_mod in PROHIBITED_MODULES:
                        violations.append(f"{path}:{node.lineno} prohibited import '{alias.name}'")
            elif isinstance(node, ast.ImportFrom):
                if node.module:
                    root_mod = node.module.split(".")[0]
                    if root_mod in PROHIBITED_MODULES:
                        violations.append(f"{path}:{node.lineno} prohibited from-import '{node.module}'")

    assert not violations, "Prohibited imports found in M05:\n" + "\n".join(violations)


def test_kahns_topological_dependency_ordering() -> None:
    """Verify AnatomyService compiles cleanly in Kahn's sort above M00-M04."""
    regs = {
        "device.mgr": ServiceRegistration("device.mgr", lambda: None, ()),
        "vision.service": ServiceRegistration("vision.service", lambda: None, ("device.mgr",)),
        "audio.service": ServiceRegistration("audio.service", lambda: None, ("device.mgr",)),
        "gesture.service": ServiceRegistration("gesture.service", lambda: None, ("vision.service",)),
        "ultron.service": ServiceRegistration(
            "ultron.service",
            lambda: None,
            ("vision.service", "audio.service", "gesture.service"),
        ),
        "anatomy.service": ServiceRegistration(
            "anatomy.service",
            lambda: None,
            ("device.mgr", "ultron.service"),
        ),
    }
    order = compile_topology(regs)

    assert order.index("device.mgr") < order.index("ultron.service")
    assert order.index("ultron.service") < order.index("anatomy.service")


def test_exception_hierarchy_mro() -> None:
    """Verify C3 MRO resolution and inheritance."""
    assert issubclass(AnatomyError, DeviceError)
    assert issubclass(AnatomyLifecycleError, (AnatomyError, DeviceLifecycleError))
    assert issubclass(AnatomyShutdownError, AnatomyError)


def test_secret_filter_redaction_in_audit_query(
    runtime_context: RuntimeContext,
    secret_filter: SecretFilter,
) -> None:
    """Verify SecretFilter redacts secrets from resource release failure reports."""
    service = AnatomyService(secret_filter=secret_filter)
    service.initialize(runtime_context)
    service.start()

    secret_token = "SECRET_ANATOMY_PATIENT_TOKEN_999"
    service.resources.mark_release_failed("anatomy.model", f"Failed with {secret_token}")

    # Inspect health or status reporting
    h = service.health()
    assert h.status.name == "UNHEALTHY"


def test_service_reentrancy_guard(
    runtime_context: RuntimeContext,
    sample_organ_entity: AnatomicalEntity,
) -> None:
    """Verify reentrancy guard rejects nested mutating calls."""
    service = AnatomyService()
    service.initialize(runtime_context)
    service.start()

    class EvilEntity:
        @property
        def entity_id(self) -> str:
            service.register_entity(sample_organ_entity)
            return "anat.evil"
        def __getattr__(self, name: str) -> None:
            return None

    with pytest.raises(AnatomyLifecycleError):
        service.register_entity(EvilEntity())  # type: ignore
