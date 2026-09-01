# -*- coding: utf-8 -*-
"""Hostile AST Hardening, Prohibited Primitives, and Zero-Actuation Audit for M12."""

from __future__ import annotations

import ast
from pathlib import Path

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
    "numpy",
    "scipy",
    "pydicom",
    "trimesh",
    "shapely",
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

ACTUATION_KEYWORDS = frozenset({
    "robot",
    "cut",
    "actuate",
    "motor",
    "cauterize",
    "ablate",
    "tissue_modification",
})


def test_ast_prohibited_imports_audit() -> None:
    """Verify zero prohibited external imports in python/holomed/planning/."""
    planning_dir = Path(__file__).resolve().parent.parent.parent.parent / "python" / "holomed" / "planning"
    py_files = list(planning_dir.glob("*.py"))
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
    """Verify zero dangerous primitives (eval, exec, etc.) in python/holomed/planning/."""
    planning_dir = Path(__file__).resolve().parent.parent.parent.parent / "python" / "holomed" / "planning"
    for path in planning_dir.glob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                if isinstance(node.func, ast.Name):
                    assert node.func.id not in DANGEROUS_CALLS, f"Dangerous call {node.func.id} in {path.name}"


def test_zero_private_upstream_attribute_access() -> None:
    """Verify zero private attribute access on upstream modules."""
    planning_dir = Path(__file__).resolve().parent.parent.parent.parent / "python" / "holomed" / "planning"
    for path in planning_dir.glob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Attribute) and node.attr.startswith("_") and not node.attr.startswith("__"):
                if isinstance(node.value, ast.Name) and node.value.id not in ("self", "cls"):
                    assert False, f"Private attribute access {node.value.id}.{node.attr} in {path.name}"
