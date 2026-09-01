"""Tests for M00.9 models, dataclasses, and serialization."""

from types import MappingProxyType
import pytest

from holomed.devices.orchestration.exceptions import (
    DeviceOrchestrationSerializationError,
    DeviceOrchestrationValidationError,
)
from holomed.devices.orchestration.models import (
    MAX_ORCHESTRATION_PAYLOAD_BYTES,
    MAX_SAFE_INTEGER,
    AuditFinding,
    AuditReport,
    BridgeStatistics,
    OrchestratorStatus,
    convert_for_json_serialization,
    serialize_orchestration_payload,
)


def test_models_frozen_and_immutability() -> None:
    stats = BridgeStatistics(
        events_bridged=10,
        telemetry_updates_emitted=9,
        identity_mismatches_rejected=1,
        stale_events_rejected=0,
        bridge_errors=0,
    )
    with pytest.raises(Exception):
        stats.events_bridged = 20  # type: ignore

    finding = AuditFinding(
        plane="control",
        code="ERR_TEST",
        message="Test message",
        severity="WARNING",
    )
    with pytest.raises(Exception):
        finding.severity = "ERROR"  # type: ignore

    report = AuditReport(
        is_consistent=True,
        epoch_id=1,
        findings=(finding,),
        timestamp_utc="2026-09-01T07:00:00Z",
    )
    assert report.is_consistent is True
    assert len(report.findings) == 1

    status = OrchestratorStatus(
        service_name="device_orchestration",
        state="STARTED",
        epoch_id=1,
        child_services=MappingProxyType({"device_manager": "STARTED"}),
        bridge_statistics=stats,
    )
    assert status.child_services["device_manager"] == "STARTED"
    d = status.to_dict()
    assert d["service_name"] == "device_orchestration"
    assert d["bridge_statistics"]["events_bridged"] == 10


def test_convert_for_json_serialization_recursively() -> None:
    nested = {
        "proxy": MappingProxyType({"inner": MappingProxyType({"val": 42})}),
        "set": frozenset({"b", "a"}),
        "tuple": (1, 2, 3),
        "neg_zero": -0.0,
        "unicode": "cafe\u0301",
    }
    converted = convert_for_json_serialization(nested)
    assert converted["proxy"] == {"inner": {"val": 42}}
    assert converted["set"] == ["a", "b"]
    assert converted["tuple"] == [1, 2, 3]
    assert converted["neg_zero"] == 0.0
    assert converted["unicode"] == "café"


def test_non_finite_float_rejection() -> None:
    with pytest.raises(DeviceOrchestrationValidationError):
        convert_for_json_serialization({"nan": float("nan")})

    with pytest.raises(DeviceOrchestrationValidationError):
        convert_for_json_serialization({"inf": float("inf")})


def test_payload_serialization_byte_bound() -> None:
    small_payload = {"key": "value", "count": 123}
    serialized = serialize_orchestration_payload(small_payload)
    assert '{"count":123,"key":"value"}' == serialized

    oversized_payload = {"huge": "X" * (MAX_ORCHESTRATION_PAYLOAD_BYTES + 10)}
    with pytest.raises(DeviceOrchestrationSerializationError):
        serialize_orchestration_payload(oversized_payload)
