"""First Live Slice Unity Bridge IPC data contracts."""

from dataclasses import dataclass
from typing import Any, Mapping

from holomed.devices.models import PhysicalCommand, deep_freeze_parameter

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
    This class can only be instantiated validly by wrapping a core PhysicalCommand.
    """
    correlation_id: str
    command_timestamp_ns: int
    _physical_command: PhysicalCommand

    def __post_init__(self) -> None:
        if not self.correlation_id:
            raise ValueError("correlation_id must not be empty")
        if not isinstance(self._physical_command, PhysicalCommand):
            raise TypeError("Must provide a valid admitted PhysicalCommand to construct an AuthorizedUnityCommand")

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
