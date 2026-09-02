# -*- coding: utf-8 -*-
"""Hardening, Determinism, Reentrancy & AST Security Tests for M18 Safety Gate."""

from __future__ import annotations

import ast
import os
from unittest.mock import MagicMock
import pytest

from holomed.safety_gate.evaluator import SafetyGateEvaluator
from holomed.safety_gate.exceptions import SafetyGateLifecycleError
from holomed.safety_gate.service import SafetyGateService
from tests.unit.safety_gate.conftest import make_gate_request


def _make_started_gate_service(
    mock_dispatcher,
    mock_workflow_service,
    mock_registration_service,
    mock_navigation_service,
    mock_proximity_service,
    mock_drift_service,
    mock_recovery_service,
    mock_persistence_service,
    secret_filter,
    logger,
    runtime_context,
) -> SafetyGateService:
    svc = SafetyGateService(
        dispatcher=mock_dispatcher,
        workflow_service=mock_workflow_service,
        registration_service=mock_registration_service,
        navigation_service=mock_navigation_service,
        proximity_service=mock_proximity_service,
        drift_service=mock_drift_service,
        recovery_service=mock_recovery_service,
        persistence_service=mock_persistence_service,
        secret_filter=secret_filter,
        logger=logger,
    )
    svc.initialize(runtime_context)
    svc.start()
    return svc


class TestSafetyGateHardening:
    """Tests for reentrancy guards and determinism."""

    def test_reentrancy_protection(
        self,
        mock_dispatcher,
        mock_workflow_service,
        mock_registration_service,
        mock_navigation_service,
        mock_proximity_service,
        mock_drift_service,
        mock_recovery_service,
        mock_persistence_service,
        secret_filter,
        logger,
        runtime_context,
    ) -> None:
        svc = _make_started_gate_service(
            mock_dispatcher, mock_workflow_service, mock_registration_service,
            mock_navigation_service, mock_proximity_service, mock_drift_service,
            mock_recovery_service, mock_persistence_service, secret_filter,
            logger, runtime_context,
        )

        svc._in_transaction = True
        with pytest.raises(SafetyGateLifecycleError, match="Reentrant call"):
            svc.evaluate(make_gate_request())
        svc._in_transaction = False

    def test_deterministic_evaluation(
        self,
        mock_workflow_service,
        mock_registration_service,
        mock_navigation_service,
        mock_proximity_service,
        mock_drift_service,
        mock_recovery_service,
    ) -> None:
        req = make_gate_request(now_utc="2025-01-01T12:00:00+00:00")

        rec1 = SafetyGateEvaluator.evaluate(
            request=req,
            epoch_id=1,
            workflow_service=mock_workflow_service,
            registration_service=mock_registration_service,
            navigation_service=mock_navigation_service,
            proximity_service=mock_proximity_service,
            drift_service=mock_drift_service,
            recovery_service=mock_recovery_service,
        )
        rec2 = SafetyGateEvaluator.evaluate(
            request=req,
            epoch_id=1,
            workflow_service=mock_workflow_service,
            registration_service=mock_registration_service,
            navigation_service=mock_navigation_service,
            proximity_service=mock_proximity_service,
            drift_service=mock_drift_service,
            recovery_service=mock_recovery_service,
        )

        assert rec1.decision == rec2.decision
        assert rec1.severity == rec2.severity
        assert rec1.reason_code == rec2.reason_code
        assert rec1.evaluated_at_utc == rec2.evaluated_at_utc


class TestSafetyGateSecurityAST:
    """AST audit confirming pure stdlib usage and zero private upstream access."""

    def test_no_prohibited_imports_or_dangerous_calls(self) -> None:
        prohibited = {
            "numpy", "scipy", "socket", "asyncio", "threading", "multiprocessing",
            "subprocess", "requests", "pydicom", "trimesh", "openxr",
        }
        dangerous_calls = {"eval", "exec", "compile", "__import__"}

        package_dir = os.path.join("python", "holomed", "safety_gate")
        for root, _, files in os.walk(package_dir):
            for f in files:
                if f.endswith(".py"):
                    filepath = os.path.join(root, f)
                    with open(filepath, "r", encoding="utf-8") as src:
                        tree = ast.parse(src.read(), filename=filepath)

                    for node in ast.walk(tree):
                        if isinstance(node, ast.Import):
                            for alias in node.names:
                                base_pkg = alias.name.split(".")[0]
                                assert base_pkg not in prohibited, f"Prohibited import {alias.name} in {filepath}:{node.lineno}"
                        elif isinstance(node, ast.ImportFrom):
                            if node.module:
                                base_pkg = node.module.split(".")[0]
                                assert base_pkg not in prohibited, f"Prohibited from-import {node.module} in {filepath}:{node.lineno}"
                        elif isinstance(node, ast.Call):
                            if isinstance(node.func, ast.Name):
                                assert node.func.id not in dangerous_calls, f"Dangerous call {node.func.id} in {filepath}:{node.lineno}"

    def test_no_private_upstream_access(self) -> None:
        """Ensure no access to private attributes on external objects."""
        package_dir = os.path.join("python", "holomed", "safety_gate")
        for root, _, files in os.walk(package_dir):
            for f in files:
                if f.endswith(".py"):
                    filepath = os.path.join(root, f)
                    with open(filepath, "r", encoding="utf-8") as src:
                        tree = ast.parse(src.read(), filename=filepath)

                    for node in ast.walk(tree):
                        if isinstance(node, ast.Attribute):
                            if node.attr.startswith("_") and not node.attr.startswith("__"):
                                if not (isinstance(node.value, ast.Name) and node.value.id in ("self", "cls")):
                                    pytest.fail(f"Private attribute access {node.attr} in {filepath}:{node.lineno}")
