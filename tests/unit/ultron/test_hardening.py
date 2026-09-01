# -*- coding: utf-8 -*-
"""Hardening and Security Invariant Tests for M04 Ultron."""

from __future__ import annotations

import ast
import glob
import os
import pytest

from holomed.devices.exceptions import DeviceError, DeviceLifecycleError
from holomed.protocol.builders import create_query
from holomed.runtime.context import RuntimeContext
from holomed.runtime.logging import SecretFilter
from holomed.runtime.service import ServiceRegistration, compile_topology
from holomed.ultron.exceptions import (
    UltronError,
    UltronLifecycleError,
    UltronShutdownError,
)
from holomed.ultron.models import (
    ModalityObservation,
)
from holomed.ultron.service import UltronService

PROHIBITED_MODULES = frozenset({
    "torch", "cv2", "scipy", "sklearn", "pandas", "requests",
    "fastapi", "pydantic", "aiohttp", "mediapipe", "numpy",
    "tensorflow", "onnxruntime", "socket", "asyncio",
    "threading", "multiprocessing", "subprocess", "urllib.request", "http.client",
})


def test_ast_prohibited_import_scan() -> None:
    """Audit all M04 source and test files to guarantee zero prohibited imports."""
    base_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
    ultron_src = os.path.join(base_dir, "python", "holomed", "ultron")
    ultron_tests = os.path.join(base_dir, "tests", "unit", "ultron")

    py_files = glob.glob(os.path.join(ultron_src, "**", "*.py"), recursive=True) + \
               glob.glob(os.path.join(ultron_tests, "**", "*.py"), recursive=True)

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

    assert not violations, "Prohibited imports found in M04:\n" + "\n".join(violations)


def test_kahns_topological_dependency_ordering() -> None:
    """Verify Ultron cleanly compiles in Kahn's topological sort above M01, M02, M03."""
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
    }
    order = compile_topology(regs)

    assert order.index("device.mgr") < order.index("vision.service")
    assert order.index("device.mgr") < order.index("audio.service")
    assert order.index("vision.service") < order.index("gesture.service")
    assert order.index("gesture.service") < order.index("ultron.service")
    assert order.index("audio.service") < order.index("ultron.service")


def test_exception_hierarchy_mro() -> None:
    """Verify C3 MRO resolution and inheritance."""
    assert issubclass(UltronError, DeviceError)
    assert issubclass(UltronLifecycleError, (UltronError, DeviceLifecycleError))
    assert issubclass(UltronShutdownError, UltronError)


def test_secret_filter_redaction_in_audit_and_errors(
    runtime_context: RuntimeContext,
    secret_filter: SecretFilter,
) -> None:
    """Verify SecretFilter redacts secrets in public audit findings."""
    service = UltronService(secret_filter=secret_filter)
    service.initialize(runtime_context)
    service.start()

    secret_token = "SECRET_TOKEN_XYZ_12345"
    service.resources.mark_release_failed("ultron.context", f"Failed with {secret_token}")

    q = create_query("ultron.audit", source="test", target="ultron_service", payload={})
    resp = service.handle_audit_query(q)

    assert resp.payload["is_consistent"] is False
    findings_str = " ".join(resp.payload["findings"])
    assert secret_token not in findings_str
    assert "<redacted>" in findings_str


def test_service_reentrancy_guard(
    runtime_context: RuntimeContext,
    sample_vision_observation: ModalityObservation,
) -> None:
    """Verify reentrancy guard rejects nested mutating calls."""
    service = UltronService()
    service.initialize(runtime_context)
    service.start()

    class EvilObservation:
        @property
        def epoch_id(self) -> int:
            service.ingest_observation(sample_vision_observation)
            return 1
        def __getattr__(self, name: str) -> None:
            return None

    with pytest.raises(UltronLifecycleError):
        service.ingest_observation(EvilObservation())  # type: ignore
