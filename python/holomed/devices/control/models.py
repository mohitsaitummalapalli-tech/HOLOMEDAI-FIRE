"""HoloMed AI - Device command & query control plane models and constants."""

from __future__ import annotations

import re
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, Callable, Mapping, Optional, Tuple

from holomed.devices.control.exceptions import DeviceCommandValidationError
from holomed.devices.interfaces import IDevice
from holomed.devices.models import DeviceState

# -----------------------------------------------------------------------------
# Bounds & Constants
# -----------------------------------------------------------------------------
MAX_REGISTERED_COMMANDS: int = 256
MAX_REGISTERED_QUERIES: int = 256
MAX_IDEMPOTENCY_KEYS: int = 4096

MAX_COMMAND_RESULT_BYTES: int = 65536
MAX_QUERY_RESULT_BYTES: int = 65536

MAX_RESULT_DEPTH: int = 8
MAX_RESULT_CONTAINER_WIDTH: int = 64
MAX_RESULT_TOTAL_NODES: int = 256
MAX_RESULT_TOTAL_UNITS: int = 16384

COMMAND_NAME_REGEX = re.compile(r"^[a-z0-9_]+(\.[a-z0-9_]+)*$")
QUERY_NAME_REGEX = re.compile(r"^[a-z0-9_]+(\.[a-z0-9_]+)*$")

# Handler Callables
CommandHandler = Callable[[IDevice, Mapping[str, Any]], Any]
QueryHandler = Callable[[IDevice, Mapping[str, Any]], Any]


@dataclass(frozen=True)
class DeviceCommandDefinition:
    """Authoritative declaration of a device command supported by the control plane."""

    command_name: str
    handler: CommandHandler
    required_capability_id: Optional[str] = None
    allow_ready: bool = False
    description: str = ""

    def __post_init__(self) -> None:
        if type(self.command_name) is not str or not COMMAND_NAME_REGEX.match(self.command_name) or not (1 <= len(self.command_name) <= 64):
            raise DeviceCommandValidationError(f"Invalid command_name syntax or length: '{self.command_name}'")
        if not callable(self.handler):
            raise DeviceCommandValidationError("CommandHandler must be callable")
        if self.required_capability_id is not None:
            if type(self.required_capability_id) is not str or not (1 <= len(self.required_capability_id) <= 64):
                raise DeviceCommandValidationError(f"Invalid required_capability_id: '{self.required_capability_id}'")


@dataclass(frozen=True)
class DeviceQueryDefinition:
    """Authoritative declaration of a device query supported by the control plane."""

    query_name: str
    handler: QueryHandler
    allowed_states: Tuple[DeviceState, ...] = (DeviceState.REGISTERED, DeviceState.READY, DeviceState.ACTIVE)
    description: str = ""

    def __post_init__(self) -> None:
        if type(self.query_name) is not str or not QUERY_NAME_REGEX.match(self.query_name) or not (1 <= len(self.query_name) <= 64):
            raise DeviceCommandValidationError(f"Invalid query_name syntax or length: '{self.query_name}'")
        if not callable(self.handler):
            raise DeviceCommandValidationError("QueryHandler must be callable")
        if not isinstance(self.allowed_states, tuple):
            raise DeviceCommandValidationError("allowed_states must be a tuple of DeviceState")
        for st in self.allowed_states:
            if not isinstance(st, DeviceState):
                raise DeviceCommandValidationError(f"Invalid state in allowed_states: {st}")


@dataclass(frozen=True)
class IdempotencyRecord:
    """Immutable cached entry for a processed command envelope."""

    message_id: str
    payload_hash: str
    response_payload: Mapping[str, Any]
    response_metadata: Mapping[str, Any]
