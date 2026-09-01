# -*- coding: utf-8 -*-
"""Hostile AST Hardening, Determinism, and Zero-Actuation Audit for M14."""

from __future__ import annotations

import ast
from pathlib import Path
import re

from holomed.navigation.constants import DEFAULT_PATIENT_TRACKER_FRAME
from holomed.navigation.evaluator import TrajectoryDeviationEvaluator
from holomed.navigation.models import TrackedInstrumentPose
from holomed.planning.models import TrajectoryPlan

PROHIBITED_MODULES = frozenset({
    "requests",
    "urllib",
    "http",
    "socket",
    "asyncio",
    "aiohttp",
    "fastapi",
    "flask",
    "subprocess",
    "multiprocessing",
    "threading",
    "numpy",
    "scipy",
    "pydicom",
    "trimesh",
    "openxr",
})

DANGEROUS_CALLS = frozenset({
    "eval",
    "exec",
    "compile",
    "__import__",
    "system",
    "popen",
    "spawn",
    "fork",
})


def test_ast_prohibited_imports_audit() -> None:
    """Verify zero prohibited external imports in python/holomed/navigation/."""
    nav_dir = Path(__file__).resolve().parent.parent.parent.parent / "python" / "holomed" / "navigation"
    py_files = list(nav_dir.glob("*.py"))
    assert len(py_files) >= 5

    for path in py_files:
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    root = alias.name.split(".")[0]
                    assert root not in PROHIBITED_MODULES, f"Prohibited import {root} in {path.name}"
            elif isinstance(node, ast.ImportFrom):
                if node.module:
                    root = node.module.split(".")[0]
                    assert root not in PROHIBITED_MODULES, f"Prohibited import {root} in {path.name}"


def test_ast_dangerous_primitives_audit() -> None:
    """Verify zero dangerous primitives (eval, exec, etc.) in python/holomed/navigation/."""
    nav_dir = Path(__file__).resolve().parent.parent.parent.parent / "python" / "holomed" / "navigation"
    for path in nav_dir.glob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                if isinstance(node.func, ast.Name):
                    assert node.func.id not in DANGEROUS_CALLS, f"Dangerous call {node.func.id} in {path.name}"


def test_zero_private_upstream_attribute_access() -> None:
    """Verify zero private attribute access on upstream modules."""
    nav_dir = Path(__file__).resolve().parent.parent.parent.parent / "python" / "holomed" / "navigation"
    for path in nav_dir.glob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Attribute) and node.attr.startswith("_") and not node.attr.startswith("__"):
                if isinstance(node.value, ast.Name) and node.value.id not in ("self", "cls"):
                    assert False, f"Private attribute access {node.value.id}.{node.attr} in {path.name}"


def test_zero_surgical_actuation_in_navigation() -> None:
    """Verify zero surgical actuation words/commands in navigation source code."""
    banned_words = ["robot", "actuate", "motor", "cauter", "ablat", "laser", "energy", "resection", "end-effector"]
    pattern = re.compile(r"\b(" + "|".join(banned_words) + r")\b", re.IGNORECASE)

    nav_dir = Path(__file__).resolve().parent.parent.parent.parent / "python" / "holomed" / "navigation"
    for path in nav_dir.glob("*.py"):
        text = path.read_text(encoding="utf-8")
        matches = pattern.findall(text)
        assert len(matches) == 0, f"Found actuation terms {matches} in {path.name}"


def test_navigation_determinism_across_multiple_runs(sample_plan_trajectory: TrajectoryPlan) -> None:
    """Verify evaluation of identical inputs produces identical results bit-for-bit across multiple runs."""
    pose = TrackedInstrumentPose(
        instrument_id="probe_01",
        session_id="s1",
        epoch_id=1,
        sequence_number=1,
        tip_position_mm=(1.23, 2.34, 45.67),
        orientation_quaternion=(1.0, 0.0, 0.0, 0.0),
        coordinate_frame=DEFAULT_PATIENT_TRACKER_FRAME,
        confidence=0.92,
        uncertainty=0.08,
        timestamp_utc="2026-09-01T12:00:00.000Z",
    )
    results = []
    for _ in range(10):
        dev = TrajectoryDeviationEvaluator.evaluate(pose, sample_plan_trajectory, now_utc="2026-09-01T12:00:00.050Z")
        results.append((dev.lateral_deviation_mm, dev.angular_deviation_deg, dev.axial_distance_mm, dev.state, dev.is_aligned))

    first = results[0]
    for idx, res in enumerate(results[1:], start=1):
        assert res == first, f"Run {idx} diverged from run 0: {res} vs {first}"
