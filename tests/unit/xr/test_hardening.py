# -*- coding: utf-8 -*-
"""Hardening and Security Invariant Tests for M06 XR."""

from __future__ import annotations

import ast
import glob
import os
import pytest

from holomed.anatomy.models import BoundingBox3D, Point3D, RigidTransform3D
from holomed.devices.exceptions import DeviceError, DeviceLifecycleError
from holomed.runtime.context import RuntimeContext
from holomed.runtime.logging import SecretFilter
from holomed.runtime.service import ServiceRegistration, compile_topology
from holomed.xr.exceptions import (
    XRError,
    XRLifecycleError,
    XRShutdownError,
)
from holomed.xr.models import (
    VisibilityState,
    VisualCategory,
    VisualNode,
)
from holomed.xr.service import XRService

PROHIBITED_MODULES = frozenset({
    "openxr", "pyvista", "trimesh", "pybullet", "meshio",
    "torch", "cv2", "scipy", "sklearn", "pandas", "requests",
    "fastapi", "pydantic", "aiohttp", "mediapipe", "numpy",
    "tensorflow", "onnxruntime", "socket", "asyncio",
    "threading", "multiprocessing", "subprocess", "urllib.request", "http.client",
})


def test_ast_prohibited_import_scan() -> None:
    """Audit all M06 source and test files to guarantee ZERO prohibited imports."""
    base_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
    xr_src = os.path.join(base_dir, "python", "holomed", "xr")
    xr_tests = os.path.join(base_dir, "tests", "unit", "xr")

    py_files = glob.glob(os.path.join(xr_src, "**", "*.py"), recursive=True) + \
               glob.glob(os.path.join(xr_tests, "**", "*.py"), recursive=True)

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

    assert not violations, "Prohibited imports found in M06:\n" + "\n".join(violations)


def test_kahns_topological_dependency_ordering() -> None:
    """Verify XRService compiles cleanly in Kahn's sort above M00-M05."""
    regs = {
        "device.mgr": ServiceRegistration("device.mgr", lambda: None, ()),
        "anatomy.service": ServiceRegistration("anatomy.service", lambda: None, ("device.mgr",)),
        "xr.service": ServiceRegistration(
            "xr.service",
            lambda: None,
            ("device.mgr", "anatomy.service"),
        ),
    }
    order = compile_topology(regs)
    assert order.index("device.mgr") < order.index("anatomy.service")
    assert order.index("anatomy.service") < order.index("xr.service")


def test_exception_hierarchy_mro() -> None:
    """Verify C3 MRO resolution and inheritance."""
    assert issubclass(XRError, DeviceError)
    assert issubclass(XRLifecycleError, (XRError, DeviceLifecycleError))
    assert issubclass(XRShutdownError, XRError)


def test_service_reentrancy_guard(
    runtime_context: RuntimeContext,
    sample_visual_node: VisualNode,
) -> None:
    """Verify reentrancy guard rejects nested mutating calls."""
    service = XRService()
    service.initialize(runtime_context)
    service.start()

    class EvilNode:
        @property
        def node_id(self) -> str:
            service.add_visual_node(sample_visual_node)
            return "visual.evil"
        def __getattr__(self, name: str) -> None:
            return None

    with pytest.raises(XRLifecycleError):
        service.add_visual_node(EvilNode())  # type: ignore
