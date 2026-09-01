# -*- coding: utf-8 -*-
"""Adversarial hardening, security, and prohibited-import audit tests for M03."""

from __future__ import annotations

import ast
import glob
import os
import pytest

from holomed.gesture.exceptions import (
    GestureCapacityError,
    GestureError,
    GestureLifecycleError,
    GestureResourceIntegrityError,
    GestureShutdownError,
    GestureValidationError,
)
from holomed.gesture.models import (
    HandObservation,
    Handedness,
    serialize_gesture_payload,
)
from holomed.gesture.service import GestureService
from holomed.runtime.context import RuntimeContext
from holomed.runtime.logging import SecretFilter
from holomed.runtime.service import ServiceRegistration, compile_topology
from holomed.vision.models import SpatialLandmark
from tests.unit.gesture.conftest import make_observation


def test_topological_dependency_minimal_footprint() -> None:
    """Verify Kahn's topological sort compiles cleanly with gesture depending on vision."""
    regs = {
        "device.mgr": ServiceRegistration("device.mgr", lambda: None, ()),
        "vision.service": ServiceRegistration(
            "vision.service",
            lambda: None,
            ("device.mgr",),
        ),
        "gesture.service": ServiceRegistration(
            "gesture.service",
            lambda: None,
            ("vision.service",),
        ),
    }

    order = compile_topology(regs)
    assert order[0] == "device.mgr"
    assert order.index("vision.service") < order.index("gesture.service")


def test_ast_prohibited_import_audit() -> None:
    """Audit all M03 source and test files to ensure ZERO prohibited imports."""
    prohibited = {
        "torch",
        "cv2",
        "scipy",
        "sklearn",
        "pandas",
        "requests",
        "fastapi",
        "pydantic",
        "aiohttp",
        "mediapipe",
        "numpy",
        "tensorflow",
        "onnxruntime",
        "socket",
        "asyncio",
        "threading",
        "multiprocessing",
        "subprocess",
    }

    m03_files = glob.glob("python/holomed/gesture/**/*.py", recursive=True) + glob.glob(
        "tests/unit/gesture/**/*.py", recursive=True
    )
    assert len(m03_files) >= 15, "Expected at least 15 M03 python files to be audited"

    violations: list[str] = []
    for fpath in m03_files:
        with open(fpath, "r", encoding="utf-8") as fh:
            tree = ast.parse(fh.read(), filename=fpath)

        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    root_pkg = alias.name.split(".")[0]
                    if root_pkg in prohibited:
                        violations.append(f"{fpath}:{node.lineno} prohibited 'import {alias.name}'")
            elif isinstance(node, ast.ImportFrom):
                if node.module:
                    root_pkg = node.module.split(".")[0]
                    if root_pkg in prohibited:
                        violations.append(f"{fpath}:{node.lineno} prohibited 'from {node.module} import ...'")

    assert not violations, f"Prohibited import audit failed:\n" + "\n".join(violations)


def test_exception_hierarchy_mro() -> None:
    """Verify C3 method resolution order on all M03 exception classes."""
    classes = [
        GestureLifecycleError,
        GestureCapacityError,
        GestureValidationError,
        GestureResourceIntegrityError,
        GestureShutdownError,
    ]
    for cls in classes:
        mro_names = [c.__name__ for c in cls.__mro__]
        assert "GestureError" in mro_names
        assert "DeviceError" in mro_names
        assert "HoloMedError" in mro_names
        assert mro_names.index("GestureError") < mro_names.index("DeviceError")


def test_deterministic_repeated_processing(
    runtime_context: RuntimeContext,
    pointing_landmarks: tuple[SpatialLandmark, ...],
) -> None:
    """Verify identical inputs yield identical outputs across repeated runs (D208 / Section 46)."""
    service1 = GestureService()
    service1.initialize(runtime_context)
    service1.start()

    service2 = GestureService()
    service2.initialize(runtime_context)
    service2.start()

    obs1 = make_observation(pointing_landmarks, sequence_number=1)
    obs2 = make_observation(pointing_landmarks, sequence_number=1)

    res1 = service1.ingest_observation(obs1)
    res2 = service2.ingest_observation(obs2)

    assert res1.quality == res2.quality
    assert len(res1.tracks) == len(res2.tracks) == 1
    assert res1.tracks[0].track_id == res2.tracks[0].track_id
    assert len(res1.gestures) == len(res2.gestures) == 1
    assert res1.gestures[0].gesture_type == res2.gestures[0].gesture_type
    assert res1.gestures[0].confidence == res2.gestures[0].confidence
    assert res1.gestures[0].interaction_direction == res2.gestures[0].interaction_direction


def test_secret_filter_redaction_in_audit_and_errors(
    runtime_context: RuntimeContext,
    secret_filter: SecretFilter,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verify SecretFilter redacts secrets in public audit findings."""
    service = GestureService(secret_filter=secret_filter)
    service.initialize(runtime_context)
    service.start()

    # Corrupt resource to trigger audit finding with secret
    secret_token = "SECRET_TOKEN_XYZ_12345"
    service.resources.mark_release_failed("gesture.store", f"Failed with {secret_token}")

    from holomed.protocol.builders import create_query
    q = create_query("gesture.pipeline.audit", source="test", target="gesture_service", payload={})
    resp = service.handle_audit_query(q)

    assert resp.payload["is_consistent"] is False
    findings_str = " ".join(resp.payload["findings"])
    assert secret_token not in findings_str
    assert "<redacted>" in findings_str
