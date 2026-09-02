# -*- coding: utf-8 -*-
"""Hardening, Determinism, Reentrancy & AST Security Tests for M17 Recovery."""

from __future__ import annotations

import ast
from datetime import datetime, timezone
import os
from unittest.mock import MagicMock

import pytest

from holomed.recovery.constants import MAX_ACTIVE_RECOVERY_SESSIONS
from holomed.recovery.evaluator import RecoveryEvaluator
from holomed.recovery.exceptions import RecoveryCapacityError, RecoveryLifecycleError
from holomed.recovery.models import RecoveryState
from holomed.recovery.service import RecoveryService

from tests.unit.recovery.conftest import (
    make_fiducial_cloud,
    make_recovery_authorization,
)


def _make_started_service(
    mock_dispatcher,
    mock_registration_service,
    mock_drift_service,
    mock_proximity_service,
    mock_navigation_service,
    mock_persistence_service,
    secret_filter,
    logger,
    runtime_context,
) -> RecoveryService:
    svc = RecoveryService(
        dispatcher=mock_dispatcher,
        registration_service=mock_registration_service,
        drift_service=mock_drift_service,
        proximity_service=mock_proximity_service,
        navigation_service=mock_navigation_service,
        persistence_service=mock_persistence_service,
        secret_filter=secret_filter,
        logger=logger,
    )
    svc.initialize(runtime_context)
    svc.start()
    return svc


class TestRecoveryHardening:
    """Tests for capacity limits, reentrancy guards, and determinism."""

    def test_session_capacity_limit(
        self,
        mock_dispatcher,
        mock_registration_service,
        mock_drift_service,
        mock_proximity_service,
        mock_navigation_service,
        mock_persistence_service,
        secret_filter,
        logger,
        runtime_context,
        make_recovery_capability,
    ) -> None:
        svc = _make_started_service(
            mock_dispatcher,
            mock_registration_service,
            mock_drift_service,
            mock_proximity_service,
            mock_navigation_service,
            mock_persistence_service,
            secret_filter,
            logger,
            runtime_context,
        )
        cloud = make_fiducial_cloud()
        for i in range(MAX_ACTIVE_RECOVERY_SESSIONS):
            cap = make_recovery_capability(svc, f"session-{i:03d}", seq=1)
            svc.stage_candidate(f"session-{i:03d}", "plan-01", cloud, sequence_number=1, capability=cap)

        cap_overflow = make_recovery_capability(svc, "session-overflow", seq=1)
        with pytest.raises(RecoveryCapacityError, match="Max active"):
            svc.stage_candidate("session-overflow", "plan-01", cloud, sequence_number=1, capability=cap_overflow)

    def test_reentrancy_protection(
        self,
        mock_dispatcher,
        mock_registration_service,
        mock_drift_service,
        mock_proximity_service,
        mock_navigation_service,
        mock_persistence_service,
        secret_filter,
        logger,
        runtime_context,
        make_recovery_capability,
    ) -> None:
        svc = _make_started_service(
            mock_dispatcher,
            mock_registration_service,
            mock_drift_service,
            mock_proximity_service,
            mock_navigation_service,
            mock_persistence_service,
            secret_filter,
            logger,
            runtime_context,
        )
        cloud = make_fiducial_cloud()
        cap = make_recovery_capability(svc, "session-01", seq=1)

        svc._in_transaction = True
        with pytest.raises(RecoveryLifecycleError, match="Reentrant"):
            svc.stage_candidate("session-01", "plan-01", cloud, sequence_number=1, capability=cap)

        with pytest.raises(RecoveryLifecycleError, match="Reentrant"):
            svc.verify_candidate("session-01", make_recovery_authorization(), (0, 0, 0), (0, 0, 0), sequence_number=1, capability=cap)

        with pytest.raises(RecoveryLifecycleError, match="Reentrant"):
            svc.activate_recovery("session-01", sequence_number=1, capability=cap)

        svc._in_transaction = False

    def test_deterministic_recovery_cycle(self) -> None:
        """Repeated candidate solving and verification with identical inputs yields bitwise identical results."""
        cloud = make_fiducial_cloud(offset_mm=(2.5, -1.2, 3.4), jitter_mm=0.1)
        fixed_time = "2025-01-01T12:00:00+00:00"

        candidate1 = RecoveryEvaluator.stage_and_solve_candidate("session-01", "plan-01", cloud, 1, now_utc=fixed_time)
        candidate2 = RecoveryEvaluator.stage_and_solve_candidate("session-01", "plan-01", cloud, 1, now_utc=fixed_time)

        assert candidate1.candidate_transform.rotation_matrix == candidate2.candidate_transform.rotation_matrix
        assert candidate1.candidate_transform.translation_vector_mm == candidate2.candidate_transform.translation_vector_mm
        assert candidate1.quality_report.fre_rms_mm == candidate2.quality_report.fre_rms_mm

        verif1 = RecoveryEvaluator.verify_checkpoint(candidate1, (100.0, 0.0, 0.0), (102.5, -1.2, 3.4), "dr_chen", now_utc=fixed_time)
        verif2 = RecoveryEvaluator.verify_checkpoint(candidate2, (100.0, 0.0, 0.0), (102.5, -1.2, 3.4), "dr_chen", now_utc=fixed_time)

        assert verif1.measured_drift_error_mm == verif2.measured_drift_error_mm


class TestRecoverySecurityAST:
    """AST audit confirming pure stdlib usage and zero private upstream access."""

    def test_no_prohibited_imports_or_dangerous_calls(self) -> None:
        prohibited = {
            "numpy", "scipy", "socket", "asyncio", "threading", "multiprocessing",
            "subprocess", "requests", "pydicom", "trimesh", "openxr",
        }
        dangerous_calls = {"eval", "exec", "compile", "__import__"}

        package_dir = os.path.join("python", "holomed", "recovery")
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
        package_dir = os.path.join("python", "holomed", "recovery")
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
