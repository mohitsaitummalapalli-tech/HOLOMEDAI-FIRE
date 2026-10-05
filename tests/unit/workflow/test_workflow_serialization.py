import json
import math
import pytest
from enum import Enum
from types import MappingProxyType
from holomed.workflow.serialization import normalize_workflow_value, serialize_canonical_workflow_bytes
from holomed.workflow.exceptions import WorkflowValidationError

class DummyEnum(Enum):
    A = "A"
    B = 1

def test_normalize_workflow_value_dict():
    data = {"b": 2, "a": 1}
    assert normalize_workflow_value(data) == {"a": 1, "b": 2}
    
    mp = MappingProxyType({"z": 3, "y": 4})
    assert normalize_workflow_value(mp) == {"y": 4, "z": 3}

def test_normalize_workflow_value_list_tuple():
    assert normalize_workflow_value([3, 1, 2]) == [3, 1, 2]
    assert normalize_workflow_value((3, 1, 2)) == [3, 1, 2]

def test_normalize_workflow_value_set():
    s = {3, 1, 2}
    # sorted by JSON string: "1", "2", "3"
    assert normalize_workflow_value(s) == [1, 2, 3]
    fs = frozenset({"b", "a"})
    # sorted by JSON string: '"a"', '"b"'
    assert normalize_workflow_value(fs) == ["a", "b"]

def test_normalize_workflow_value_string():
    # 'é' as e + acute accent vs composed é
    s1 = "e\u0301"
    s2 = "\u00e9"
    assert normalize_workflow_value(s1) == s2

def test_normalize_workflow_value_float():
    assert normalize_workflow_value(0.0) == 0.0
    assert normalize_workflow_value(-0.0) == 0.0
    assert normalize_workflow_value(1.2345678) == 1.234568
    
    with pytest.raises(WorkflowValidationError, match="Non-finite float encountered"):
        normalize_workflow_value(float("inf"))
    
    with pytest.raises(WorkflowValidationError, match="Non-finite float encountered"):
        normalize_workflow_value(float("nan"))

def test_normalize_workflow_value_enum():
    assert normalize_workflow_value(DummyEnum.A) == "A"
    assert normalize_workflow_value(DummyEnum.B) == 1

def test_serialize_canonical_workflow_bytes():
    data = {
        "z": "e\u0301",
        "y": {3, 1, 2},
        "x": 1.2345678,
        "w": DummyEnum.A
    }
    b = serialize_canonical_workflow_bytes(data)
    assert isinstance(b, bytes)
    
    # decode and verify
    s = b.decode("utf-8")
    assert s == '{"w":"A","x":1.234568,"y":[1,2,3],"z":"\u00e9"}'
