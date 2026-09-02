# -*- coding: utf-8 -*-
"""Hardening Tests for M16 Registration Drift — Edge Cases, Determinism & AST Security."""

from __future__ import annotations

import ast
import math
import os
from datetime import datetime, timezone, timedelta
from unittest.mock import MagicMock

import pytest

from holomed.drift.constants import MAX_POINT_COORDINATE_MM
from holomed.drift.evaluator import DriftEvaluator
from holomed.drift.exceptions import DriftLifecycleError, DriftValidationError
from holomed.drift.geometry import (
    compute_dwell_stability,
    compute_effective_tolerance,
    compute_euclidean_residual,
)
from holomed.drift.models import DriftState
from holomed.drift.service import DriftService

from tests.unit.drift.conftest import (
    make_identity_transform,
    make_landmark_definition,
    make_landmark_observation,
)


# ===========================================================================
# Determinism Tests
# ===========================================================================

class TestDriftDeterminism:
    """Tests that repeated evaluations produce mathematically identical outputs."""

    def test_repeated_evaluation_identical_residuals(self) -> None:
        now = datetime(2025, 1, 1, 12, 0, 0, tzinfo=timezone.utc).isoformat()
        lm = make_landmark_definition(planned_point_mm=(100.0, 50.0, -25.0), max_tolerance_mm=1.5)
        obs = make_landmark_observation(observed_point_mm=(100.8, 50.6, -24.4), timestamp_utc=now)
        transform = make_identity_transform()

        records = []
        for _ in range(10):
            rec = DriftEvaluator.evaluate(obs, lm, transform, now_utc=now)
            records.append(rec)

        for r in records[1:]:
            assert r.residual_error_mm == records[0].residual_error_mm
            assert r.state == records[0].state
            assert r.passed == records[0].passed
            assert r.effective_tolerance_mm == records[0].effective_tolerance_mm

    def test_pure_geometry_deterministic(self) -> None:
        for _ in range(10):
            res = compute_euclidean_residual((10.0, 20.0, 30.0), (12.0, 22.0, 31.0))
            assert math.isclose(res, 3.0, abs_tol=1e-12)


# ===========================================================================
# Reentrancy Protection
# ===========================================================================

class TestDriftReentrancy:
    """Tests for reentrancy guards."""

    def test_reentrancy_guard_prevents_concurrent_bind(
        self, mock_dispatcher, mock_registration_service, secret_filter, logger, runtime_context, default_landmarks
    ) -> None:
        svc = DriftService(mock_dispatcher, mock_registration_service, secret_filter, logger)
        svc.initialize(runtime_context)
        svc.start()

        svc._in_transaction = True
        with pytest.raises(DriftLifecycleError, match="Reentrant"):
            svc.bind_landmarks("session-01", default_landmarks)
        svc._in_transaction = False

    def test_reentrancy_guard_prevents_concurrent_evaluate(
        self, mock_dispatcher, mock_registration_service, secret_filter, logger, runtime_context, default_landmarks
    ) -> None:
        svc = DriftService(mock_dispatcher, mock_registration_service, secret_filter, logger)
        svc.initialize(runtime_context)
        svc.start()

        svc._in_transaction = True
        obs = make_landmark_observation()
        with pytest.raises(DriftLifecycleError, match="Reentrant"):
            svc.evaluate(obs)
        svc._in_transaction = False

    def test_reentrancy_guard_prevents_stop_during_transaction(
        self, mock_dispatcher, mock_registration_service, secret_filter, logger, runtime_context
    ) -> None:
        svc = DriftService(mock_dispatcher, mock_registration_service, secret_filter, logger)
        svc.initialize(runtime_context)
        svc.start()

        svc._in_transaction = True
        with pytest.raises(DriftLifecycleError, match="Cannot stop"):
            svc.stop()
        svc._in_transaction = False


# ===========================================================================
# Event Deduplication
# ===========================================================================

class TestDriftEventDeduplication:
    """Tests that repeated identical evaluations emit zero duplicate transition events."""

    def test_duplicate_evaluations_emit_zero_events_in_steady_state(
        self, mock_dispatcher, mock_registration_service, secret_filter, logger, runtime_context, default_landmarks
    ) -> None:
        svc = DriftService(mock_dispatcher, mock_registration_service, secret_filter, logger)
        svc.initialize(runtime_context)
        svc.start()

        svc.bind_landmarks("session-01", default_landmarks)

        t0 = datetime(2025, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
        t1 = t0 + timedelta(milliseconds=80)
        t2 = t0 + timedelta(milliseconds=180)
        t3 = t0 + timedelta(milliseconds=240)

        # Sample 1: READY -> WARNING (dwell not yet met)
        obs1 = make_landmark_observation(
            session_id="session-01", sequence_number=1, observed_point_mm=(100.2, 0.0, 0.0),
            timestamp_utc=t0.isoformat(),
        )
        svc.evaluate(obs1, now_utc=t0.isoformat())

        # Sample 2: WARNING -> WARNING (dwell not yet met)
        obs2 = make_landmark_observation(
            session_id="session-01", sequence_number=2, observed_point_mm=(100.2, 0.0, 0.0),
            timestamp_utc=t1.isoformat(),
        )
        svc.evaluate(obs2, now_utc=t1.isoformat())

        mock_dispatcher.dispatch.reset_mock()

        # Sample 3: WARNING -> STABLE (N=3, duration >= 150 ms) -> emits drift.landmark.verified + drift.warning.cleared
        obs3 = make_landmark_observation(
            session_id="session-01", sequence_number=3, observed_point_mm=(100.2, 0.0, 0.0),
            timestamp_utc=t2.isoformat(),
        )
        svc.evaluate(obs3, now_utc=t2.isoformat())
        assert mock_dispatcher.dispatch.call_count == 2

        mock_dispatcher.dispatch.reset_mock()

        # Sample 4: STABLE -> STABLE (steady-state) -> emits ZERO events!
        obs4 = make_landmark_observation(
            session_id="session-01", sequence_number=4, observed_point_mm=(100.2, 0.0, 0.0),
            timestamp_utc=t3.isoformat(),
        )
        svc.evaluate(obs4, now_utc=t3.isoformat())
        assert mock_dispatcher.dispatch.call_count == 0


# ===========================================================================
# AST Security Audit
# ===========================================================================

class TestDriftSecurityAST:
    """AST audit to prove zero prohibited imports or dangerous primitives."""

    def test_no_prohibited_imports_or_dangerous_calls(self) -> None:
        prohibited = {
            "numpy", "scipy", "socket", "asyncio", "threading", "multiprocessing",
            "subprocess", "requests", "pydicom", "trimesh", "openxr",
        }
        dangerous_calls = {"eval", "exec", "compile", "__import__"}

        violations = []
        package_dir = os.path.join("python", "holomed", "drift")
        for root, _, files in os.walk(package_dir):
            for f in files:
                if f.endswith(".py"):
                    p = os.path.join(root, f)
                    with open(p, "r", encoding="utf-8") as src:
                        tree = ast.parse(src.read(), filename=p)
                    for node in ast.walk(tree):
                        if isinstance(node, ast.Import):
                            for alias in node.names:
                                if alias.name.split(".")[0] in prohibited:
                                    violations.append((p, node.lineno, f"Prohibited import: {alias.name}"))
                        elif isinstance(node, ast.ImportFrom):
                            if node.module and node.module.split(".")[0] in prohibited:
                                violations.append((p, node.lineno, f"Prohibited import: {node.module}"))
                        elif isinstance(node, ast.Call):
                            if isinstance(node.func, ast.Name) and node.func.id in dangerous_calls:
                                violations.append((p, node.lineno, f"Dangerous call: {node.func.id}"))

        assert violations == [], f"Security violations found: {violations}"
