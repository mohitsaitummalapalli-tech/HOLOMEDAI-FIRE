# -*- coding: utf-8 -*-
"""Hardening, Reentrancy, Determinism & AST Security Tests for M19 Execution Gateway."""

from __future__ import annotations

import ast
import os
from unittest.mock import MagicMock
import pytest

from holomed.execution.exceptions import ExecutionLifecycleError
from holomed.execution.models import ExecutionStatus
from holomed.execution.service import NavigationExecutionService
from tests.unit.execution.conftest import make_execution_request


def _make_started_service(
    mock_dispatcher,
    mock_safety_gate_service,
    mock_workflow_service,
    mock_navigation_service,
    mock_persistence_service,
    secret_filter,
    logger,
    runtime_context,
) -> NavigationExecutionService:
    svc = NavigationExecutionService(
        dispatcher=mock_dispatcher,
        safety_gate_service=mock_safety_gate_service,
        workflow_service=mock_workflow_service,
        navigation_service=mock_navigation_service,
        persistence_service=mock_persistence_service,
        secret_filter=secret_filter,
        logger=logger,
    )
    svc.initialize(runtime_context)
    svc.start()
    return svc


class TestExecutionHardening:
    """Tests for reentrancy guards and determinism."""

    def test_reentrancy_protection(
        self,
        mock_dispatcher,
        mock_safety_gate_service,
        mock_workflow_service,
        mock_navigation_service,
        mock_persistence_service,
        secret_filter,
        logger,
        runtime_context,
    ) -> None:
        svc = _make_started_service(
            mock_dispatcher, mock_safety_gate_service, mock_workflow_service,
            mock_navigation_service, mock_persistence_service, secret_filter,
            logger, runtime_context,
        )

        svc._in_transaction = True
        with pytest.raises(ExecutionLifecycleError, match="Reentrant call"):
            svc.execute_navigation(make_execution_request())
        svc._in_transaction = False

    def test_deterministic_execution_output(
        self,
        mock_dispatcher,
        mock_safety_gate_service,
        mock_workflow_service,
        mock_navigation_service,
        mock_persistence_service,
        secret_filter,
        logger,
        runtime_context,
    ) -> None:
        svc = _make_started_service(
            mock_dispatcher, mock_safety_gate_service, mock_workflow_service,
            mock_navigation_service, mock_persistence_service, secret_filter,
            logger, runtime_context,
        )

        req = make_execution_request(now_utc="2025-01-01T12:00:00+00:00")
        res1 = svc.execute_navigation(req)
        res2 = svc.execute_navigation(req)

        assert res1.execution_status == res2.execution_status
        assert res1.gate_decision == res2.gate_decision
        assert res1.gate_reason_code == res2.gate_reason_code
        assert res1.executed_at_utc == res2.executed_at_utc


class TestExecutionSecurityAST:
    """AST audit confirming pure stdlib usage, zero private upstream access, and zero dangerous calls."""

    def test_no_prohibited_imports_or_dangerous_calls(self) -> None:
        prohibited = {
            "numpy", "scipy", "socket", "asyncio", "threading", "multiprocessing",
            "subprocess", "requests", "pydicom", "trimesh", "openxr",
        }
        dangerous_calls = {"eval", "exec", "compile", "__import__"}

        package_dir = os.path.join("python", "holomed", "execution")
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
        package_dir = os.path.join("python", "holomed", "execution")
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
