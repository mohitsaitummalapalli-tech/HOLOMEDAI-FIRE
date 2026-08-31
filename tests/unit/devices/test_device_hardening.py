"""Adversarial stress testing, AST import audit, and LSCU golden vectors for M00.5."""

import ast
import os
import pytest

from holomed.devices.models import deep_freeze_parameter


PROHIBITED_MODULES = {
    "socket",
    "asyncio",
    "threading",
    "multiprocessing",
    "subprocess",
    "cv2",
    "mediapipe",
    "pyaudio",
    "serial",
    "google.generativeai",
    "holomed.core.dispatcher",
}


def test_ast_prohibited_imports_audit() -> None:
    """Audit the entire python/holomed/devices directory for forbidden modules."""
    devices_dir = os.path.join(os.path.dirname(__file__), "..", "..", "..", "python", "holomed", "devices")
    devices_dir = os.path.abspath(devices_dir)

    for root, _, files in os.walk(devices_dir):
        for file in files:
            if file.endswith(".py"):
                file_path = os.path.join(root, file)
                with open(file_path, "r", encoding="utf-8") as f:
                    tree = ast.parse(f.read(), filename=file_path)

                for node in ast.walk(tree):
                    if isinstance(node, ast.Import):
                        for alias in node.names:
                            for prohibited in PROHIBITED_MODULES:
                                assert not (alias.name == prohibited or alias.name.startswith(f"{prohibited}.")), (
                                    f"Prohibited import '{alias.name}' in {file_path}"
                                )
                    elif isinstance(node, ast.ImportFrom):
                        if node.module:
                            for prohibited in PROHIBITED_MODULES:
                                assert not (node.module == prohibited or node.module.startswith(f"{prohibited}.")), (
                                    f"Prohibited from-import '{node.module}' in {file_path}"
                                )


@pytest.mark.parametrize(
    "vector_id, value, expected_units",
    [
        ("V01_none", None, 4),
        ("V02_true", True, 4),
        ("V03_false", False, 4),
        ("V04_int_zero", 0, 8),
        ("V05_int_neg", -100, 8),
        ("V06_float_pi", 3.14159, 8),
        ("V07_float_neg_zero", -0.0, 8),
        ("V08_empty_str", "", 0),
        ("V09_sensor_str", "sensor", 6),
        ("V10_nfc_str", "héllo", 6),
        ("V11_empty_tuple", (), 0),
        ("V12_int_tuple", (1, 2), 32),
        ("V13_mixed_tuple", (True, None, "a"), 33),
        ("V14_empty_frozenset", frozenset(), 0),
        ("V15_int_frozenset", frozenset({1, 2}), 32),
        ("V16_empty_dict", {}, 0),
        ("V17_single_entry_dict", {"a": 1}, 17),
        ("V18_two_entry_dict", {"rate": 60, "enabled": True}, 39),
        ("V19_float_str_dict", {"gain": 1.5, "mode": "auto"}, 36),
        ("V20_nested_dict", {"nested": {"val": (10,)}}, 41),
    ],
)
def test_lscu_golden_vectors(vector_id: str, value: object, expected_units: int) -> None:
    """Verify exact 20 deterministic LSCU golden test vectors."""
    stats = {"nodes": 0, "units": 0}
    deep_freeze_parameter(value, stats=stats)
    assert stats["units"] == expected_units, f"Vector {vector_id} cost mismatch"
