"""HoloMed AI - Device Orchestration Models and Serialization."""

from __future__ import annotations

from dataclasses import dataclass
import json
import math
from types import MappingProxyType
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple, Union
import unicodedata

from holomed.devices.orchestration.exceptions import (
    DeviceOrchestrationSerializationError,
    DeviceOrchestrationValidationError,
)

# Numeric & Boundary Constants
MAX_RECORDED_ORCHESTRATION_EVENTS: int = 1000
MAX_ORCHESTRATION_PAYLOAD_BYTES: int = 65536
MAX_AUDIT_FINDINGS: int = 256
MAX_SAFE_INTEGER: int = 9223372036854775807


@dataclass(frozen=True)
class BridgeStatistics:
    """Immutable metrics of the cross-plane event bridge."""

    events_bridged: int
    telemetry_updates_emitted: int
    identity_mismatches_rejected: int
    stale_events_rejected: int
    bridge_errors: int

    def to_dict(self) -> Dict[str, int]:
        return {
            "events_bridged": self.events_bridged,
            "telemetry_updates_emitted": self.telemetry_updates_emitted,
            "identity_mismatches_rejected": self.identity_mismatches_rejected,
            "stale_events_rejected": self.stale_events_rejected,
            "bridge_errors": self.bridge_errors,
        }


@dataclass(frozen=True)
class AuditFinding:
    """Individual cross-plane consistency diagnostic finding."""

    plane: str
    code: str
    message: str
    severity: str  # "WARNING", "ERROR", "CRITICAL"

    def to_dict(self) -> Dict[str, str]:
        return {
            "plane": self.plane,
            "code": self.code,
            "message": self.message,
            "severity": self.severity,
        }


@dataclass(frozen=True)
class AuditReport:
    """Holistic subsystem consistency verdict and itemized findings."""

    is_consistent: bool
    epoch_id: int
    findings: Tuple[AuditFinding, ...]
    timestamp_utc: str

    def to_dict(self) -> Dict[str, Any]:
        return {
            "is_consistent": self.is_consistent,
            "epoch_id": self.epoch_id,
            "findings": [f.to_dict() for f in self.findings],
            "timestamp_utc": self.timestamp_utc,
        }


@dataclass(frozen=True)
class OrchestratorStatus:
    """Consolidated runtime status of the device orchestration subsystem."""

    service_name: str
    state: str
    epoch_id: int
    child_services: Mapping[str, str]
    bridge_statistics: BridgeStatistics

    def to_dict(self) -> Dict[str, Any]:
        return {
            "service_name": self.service_name,
            "state": self.state,
            "epoch_id": self.epoch_id,
            "child_services": dict(self.child_services),
            "bridge_statistics": self.bridge_statistics.to_dict(),
        }


def convert_for_json_serialization(obj: Any) -> Any:
    """Recursively convert nested MappingProxyType, sets, tuples, and floats for JSON."""
    if isinstance(obj, MappingProxyType):
        return {str(k): convert_for_json_serialization(v) for k, v in obj.items()}
    if isinstance(obj, dict):
        return {str(k): convert_for_json_serialization(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [convert_for_json_serialization(item) for item in obj]
    if isinstance(obj, (set, frozenset)):
        return sorted([convert_for_json_serialization(item) for item in obj], key=str)
    if isinstance(obj, str):
        return unicodedata.normalize("NFC", obj)
    if isinstance(obj, float):
        if math.isnan(obj) or math.isinf(obj):
            raise DeviceOrchestrationValidationError(f"Non-finite float {obj} cannot be serialized to JSON")
        if obj == 0.0:
            return 0.0
        return obj
    if hasattr(obj, "to_dict") and callable(getattr(obj, "to_dict")):
        return convert_for_json_serialization(obj.to_dict())
    return obj


def serialize_orchestration_payload(payload: Any) -> str:
    """Deterministic JSON serialization enforcing wire limits and NFC normalization."""
    clean_obj = convert_for_json_serialization(payload)
    try:
        serialized = json.dumps(
            clean_obj,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
        )
    except (TypeError, ValueError) as e:
        raise DeviceOrchestrationSerializationError(f"Payload JSON serialization failed: {e}") from e

    encoded = serialized.encode("utf-8")
    if len(encoded) > MAX_ORCHESTRATION_PAYLOAD_BYTES:
        raise DeviceOrchestrationSerializationError(
            f"Serialized orchestration payload length ({len(encoded)} bytes) exceeds MAX_ORCHESTRATION_PAYLOAD_BYTES ({MAX_ORCHESTRATION_PAYLOAD_BYTES})"
        )

    return serialized
