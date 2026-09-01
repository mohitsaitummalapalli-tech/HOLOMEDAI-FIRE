# -*- coding: utf-8 -*-
"""Hardening and AST Security Audit Tests for M10 Workflow Subsystem."""

from __future__ import annotations

import ast
from pathlib import Path
import pytest

PROHIBITED_IMPORTS: frozenset[str] = frozenset(
    [
        "torch",
        "tensorflow",
        "numpy",
        "scipy",
        "sklearn",
        "cv2",
        "mediapipe",
        "pandas",
        "requests",
        "fastapi",
        "pydantic",
        "aiohttp",
        "socket",
        "urllib.request",
        "http.client",
        "asyncio",
        "threading",
        "multiprocessing",
        "subprocess",
        "openxr",
        "trimesh",
        "pyvista",
        "pybullet",
        "meshio",
    ]
)

DANGEROUS_BUILTIN_CALLS: frozenset[str] = frozenset(
    [
        "eval",
        "exec",
        "compile",
        "__import__",
    ]
)

DANGEROUS_METHOD_CALLS: frozenset[str] = frozenset(
    [
        "system",
        "popen",
        "spawn",
    ]
)


def test_ast_prohibited_imports_and_calls() -> None:
    """Verify workflow source contains zero prohibited modules or dangerous execution calls."""
    target_dirs = [
        Path("python/holomed/workflow"),
        Path("tests/unit/workflow"),
    ]

    for root_dir in target_dirs:
        for py_file in root_dir.glob("*.py"):
            with open(py_file, "r", encoding="utf-8") as f:
                tree = ast.parse(f.read(), filename=str(py_file))

            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        base = alias.name.split(".")[0]
                        assert (
                            base not in PROHIBITED_IMPORTS
                        ), f"Prohibited import {alias.name} found in {py_file}"
                elif isinstance(node, ast.ImportFrom):
                    if node.module:
                        base = node.module.split(".")[0]
                        assert (
                            base not in PROHIBITED_IMPORTS
                        ), f"Prohibited import-from {node.module} in {py_file}"
                elif isinstance(node, ast.Call):
                    if isinstance(node.func, ast.Name):
                        assert (
                            node.func.id not in DANGEROUS_BUILTIN_CALLS
                        ), f"Dangerous builtin call {node.func.id}() in {py_file}"
                    elif isinstance(node.func, ast.Attribute):
                        assert (
                            node.func.attr not in DANGEROUS_METHOD_CALLS
                        ), f"Dangerous method call {node.func.attr}() in {py_file}"


def test_no_private_upstream_attribute_access() -> None:
    """Verify workflow source does not access private platform or persistence attributes."""
    source_dir = Path("python/holomed/workflow")
    prohibited_private_attrs = {
        "_cycle_history",
        "_observations",
        "_context_history",
        "_writers",
        "_journal_path",
    }

    for py_file in source_dir.glob("*.py"):
        with open(py_file, "r", encoding="utf-8") as f:
            tree = ast.parse(f.read(), filename=str(py_file))

        for node in ast.walk(tree):
            if isinstance(node, ast.Attribute):
                is_self = isinstance(node.value, ast.Name) and node.value.id == "self"
                if not is_self:
                    assert (
                        node.attr not in prohibited_private_attrs
                    ), f"Prohibited private attribute access {node.attr} on external object in {py_file}"
