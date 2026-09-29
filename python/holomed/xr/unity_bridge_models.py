"""First Live Slice Unity Bridge IPC data contracts."""

from dataclasses import dataclass
from typing import Any, Mapping

from holomed.devices.models import PhysicalCommand, deep_freeze_parameter, AdmittedCommandCapability, EndpointLease
from holomed.protocol.models import MessageEnvelope

@dataclass(frozen=True)
class CommandRequest:
    """Untrusted Ultron proposal for DeviceControlManager admission.
    
    This is NOT an authorized command. It is merely a proposal submitted
    by the AI intent layer. It lacks canonical physical identity.
    """
    correlation_id: str
    command_timestamp_ns: int
    device_id: str
    action: str
    payload: Mapping[str, Any]

    def __post_init__(self) -> None:
        if not self.correlation_id:
            raise ValueError("correlation_id must not be empty")
        if not self.device_id:
            raise ValueError("device_id must not be empty")
        if not self.action:
            raise ValueError("action must not be empty")
        
        # Deep recursive immutability leveraging the core system's deep_freeze_parameter
        frozen_payload = deep_freeze_parameter(dict(self.payload))
        object.__setattr__(self, "payload", frozen_payload)

@dataclass(frozen=True)
class AuthorizedUnityCommand:
    """Distinct, explicitly authorized command contract for Unity IPC.
    
    This command is ONLY produced AFTER DeviceControlManager authoritative admission.
    Ultron CANNOT supply canonical identity fields like device_epoch or command_nonce.
    This class can only be instantiated validly by supplying a cryptographically sealed
    AdmittedCommandCapability alongside the physical command, proving provenance.
    """
    correlation_id: str
    command_timestamp_ns: int
    _physical_command: PhysicalCommand
    _capability: 'AdmittedCommandCapability'
    _lease: 'EndpointLease'

    def __post_init__(self) -> None:
        from holomed.devices.models import AdmittedCommandCapability, PhysicalCommand, EndpointLease
        from holomed.devices.control.admission import verify_admitted_capability
        if not self.correlation_id:
            raise ValueError("correlation_id must not be empty")
        if not isinstance(self._physical_command, PhysicalCommand):
            raise TypeError("Must provide a valid admitted PhysicalCommand")
        if not isinstance(self._lease, EndpointLease):
            raise TypeError("Must provide a valid EndpointLease")
        if not isinstance(self._capability, AdmittedCommandCapability):
            raise TypeError("Must provide a cryptographically sealed AdmittedCommandCapability to construct an AuthorizedUnityCommand")
        # Validate the cryptographic seal
        verify_admitted_capability(self._capability, self._physical_command, self._lease)

    @property
    def action(self) -> str:
        return self._physical_command.operation

    @property
    def payload(self) -> Mapping[str, Any]:
        # PhysicalCommand already enforces deep immutability internally via deep_freeze_parameter
        return self._physical_command.parameters

    @property
    def device_id(self) -> str:
        return self._physical_command.endpoint_id


    @property
    def device_epoch(self) -> int:
        return self._physical_command.device_epoch

    @property
    def controller_epoch(self) -> int:
        return self._physical_command.controller_epoch

    @property
    def physical_operation_id(self) -> str:
        return self._physical_command.physical_operation_id

    @property
    def command_nonce(self) -> str:
        return self._physical_command.command_nonce

def create_authorized_unity_envelope(
    physical_command: PhysicalCommand,
    endpoint_lease: 'EndpointLease',
    command_request: CommandRequest,
    capability: 'AdmittedCommandCapability'
) -> 'MessageEnvelope':
    """Authoritatively bridges DeviceControlManager admission into a Unity IPC envelope.
    
    Proves provenance by requiring a cryptographically sealed AdmittedCommandCapability
    rather than publicly constructible PhysicalCommand/EndpointLease instances.
    """
    from holomed.core.models import MessageEnvelope
    from holomed.protocol.models import MessageType
    from datetime import datetime, timezone
    import uuid

    auth_cmd = AuthorizedUnityCommand(
        correlation_id=command_request.correlation_id,
        command_timestamp_ns=command_request.command_timestamp_ns,
        _physical_command=physical_command,
        _capability=capability,
        _lease=endpoint_lease
    )

    payload = dict(auth_cmd.payload)
    payload["device_id"] = auth_cmd.device_id
    payload["command"] = auth_cmd.action
    payload["device_epoch"] = auth_cmd.device_epoch
    payload["controller_epoch"] = auth_cmd.controller_epoch
    payload["physical_operation_id"] = auth_cmd.physical_operation_id
    payload["command_nonce"] = auth_cmd.command_nonce

    return MessageEnvelope(
        protocol_version="1.0",
        message_id=str(uuid.uuid4()),
        correlation_id=auth_cmd.correlation_id,
        causation_id=None,
        message_type=MessageType.COMMAND,
        message_name="unity.action",
        source="xr.unity_bridge",
        target="unity_ipc",
        timestamp_utc=datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        payload=payload,
        metadata={}
    )

@dataclass(frozen=True)
class TelemetryObservation:
    """Wire/IPC DTO for driver-asserted execution evidence from Unity.
    
    This is STRICTLY an IPC message. It CANNOT directly mutate persistence.
    It must be decoded and fed into the existing TelemetryTransport and
    ReconciliationDaemon using established M49.3.5 evidence paths.
    """
    correlation_id: str
    telemetry_timestamp_ns: int
    device_id: str
    device_epoch: int
    controller_epoch: int
    physical_operation_id: str
    command_nonce: str
    status: str
    evidence_type: str = "DRIVER_ASSERTED_SOFTWARE_EVIDENCE"

    def __post_init__(self) -> None:
        if not self.correlation_id:
            raise ValueError("correlation_id must not be empty")
        if not self.device_id:
            raise ValueError("device_id must not be empty")
        if not self.physical_operation_id:
            raise ValueError("physical_operation_id must not be empty")
        if not self.command_nonce:
            raise ValueError("command_nonce must not be empty")
        if self.evidence_type != "DRIVER_ASSERTED_SOFTWARE_EVIDENCE":
            raise ValueError("Invalid evidence_type. Hardware evidence is STRICTLY forbidden in this software-only slice.")
