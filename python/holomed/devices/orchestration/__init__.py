"""HoloMed AI - Device plane orchestration and cross-plane coordination."""

from __future__ import annotations

from holomed.devices.orchestration.auditor import DeviceSubsystemConsistencyAuditor
from holomed.devices.orchestration.bridge import (
    WHITELISTED_BRIDGE_TOPICS,
    DeviceCrossPlaneBridge,
)
from holomed.devices.orchestration.events import (
    IDeviceOrchestrationEventSink,
    NullDeviceOrchestrationEventSink,
    RecordingDeviceOrchestrationEventSink,
)
from holomed.devices.orchestration.exceptions import (
    DeviceOrchestrationBridgeError,
    DeviceOrchestrationCapacityError,
    DeviceOrchestrationError,
    DeviceOrchestrationIntegrityError,
    DeviceOrchestrationLifecycleError,
    DeviceOrchestrationSerializationError,
    DeviceOrchestrationShutdownError,
    DeviceOrchestrationValidationError,
)
from holomed.devices.orchestration.models import (
    MAX_AUDIT_FINDINGS,
    MAX_ORCHESTRATION_PAYLOAD_BYTES,
    MAX_RECORDED_ORCHESTRATION_EVENTS,
    MAX_SAFE_INTEGER,
    AuditFinding,
    AuditReport,
    BridgeStatistics,
    OrchestratorStatus,
    convert_for_json_serialization,
    serialize_orchestration_payload,
)
from holomed.devices.orchestration.orchestrator import DevicePlaneOrchestrator

__all__ = [
    # Orchestrator & Engines
    "DevicePlaneOrchestrator",
    "DeviceCrossPlaneBridge",
    "DeviceSubsystemConsistencyAuditor",
    "WHITELISTED_BRIDGE_TOPICS",
    # Models & Dataclasses
    "BridgeStatistics",
    "AuditFinding",
    "AuditReport",
    "OrchestratorStatus",
    "convert_for_json_serialization",
    "serialize_orchestration_payload",
    # Event Sinks
    "IDeviceOrchestrationEventSink",
    "NullDeviceOrchestrationEventSink",
    "RecordingDeviceOrchestrationEventSink",
    # Constants
    "MAX_RECORDED_ORCHESTRATION_EVENTS",
    "MAX_ORCHESTRATION_PAYLOAD_BYTES",
    "MAX_AUDIT_FINDINGS",
    "MAX_SAFE_INTEGER",
    # Exceptions
    "DeviceOrchestrationError",
    "DeviceOrchestrationLifecycleError",
    "DeviceOrchestrationCapacityError",
    "DeviceOrchestrationIntegrityError",
    "DeviceOrchestrationValidationError",
    "DeviceOrchestrationBridgeError",
    "DeviceOrchestrationSerializationError",
    "DeviceOrchestrationShutdownError",
]
