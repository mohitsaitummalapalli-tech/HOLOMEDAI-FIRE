"""HoloMed AI - Device capability authorization and parameter validation."""

from __future__ import annotations

import json
from types import MappingProxyType
from typing import Any, Mapping

from holomed.devices.control.exceptions import (
    CapabilityUnauthorizedError,
    DeviceCommandResultValidationError,
    DeviceCommandValidationError,
    DeviceNotActiveError,
    DeviceQueryResultValidationError,
    DeviceStateError,
)
from holomed.devices.control.models import (
    DeviceCommandDefinition,
    DeviceQueryDefinition,
    MAX_COMMAND_RESULT_BYTES,
    MAX_QUERY_RESULT_BYTES,
)
from holomed.devices.interfaces import IDevice
from holomed.devices.models import (
    DeviceState,
    deep_freeze_parameter,
)


class CommandVerifier:
    """Validates device state, capability authorization, parameter envelopes, and results."""

    @staticmethod
    def verify_command_authorization(device: IDevice, command_def: DeviceCommandDefinition) -> None:
        """Enforce lifecycle state and capability presence for a command."""
        # 1. State authorization
        if command_def.allow_ready:
            if device.state not in (DeviceState.READY, DeviceState.ACTIVE):
                raise DeviceNotActiveError(
                    f"Device '{device.device_id}' in state {device.state.name} cannot execute command '{command_def.command_name}' (requires READY or ACTIVE)"
                )
        else:
            if device.state != DeviceState.ACTIVE:
                raise DeviceNotActiveError(
                    f"Device '{device.device_id}' in state {device.state.name} cannot execute command '{command_def.command_name}' (requires ACTIVE)"
                )

        # 2. Capability presence authorization
        if command_def.required_capability_id is not None:
            device_cap_ids = set(c.capability_id for c in device.capabilities)
            if command_def.required_capability_id not in device_cap_ids:
                raise CapabilityUnauthorizedError(
                    f"Device '{device.device_id}' lacks required capability '{command_def.required_capability_id}' for command '{command_def.command_name}'"
                )

    @staticmethod
    def verify_query_authorization(device: IDevice, query_def: DeviceQueryDefinition) -> None:
        """Enforce lifecycle state for a query."""
        if device.state not in query_def.allowed_states:
            raise DeviceStateError(
                f"Device '{device.device_id}' in state {device.state.name} not permitted for query '{query_def.query_name}' (allowed: {[s.name for s in query_def.allowed_states]})"
            )

    @staticmethod
    def validate_and_canonicalize_parameters(raw_params: Any) -> Mapping[str, Any]:
        """Validate parameter dictionary using M00.5 deep freeze rules."""
        if raw_params is None:
            return MappingProxyType({})
        if not isinstance(raw_params, (dict, MappingProxyType)):
            raise DeviceCommandValidationError(f"Parameters must be a dictionary or MappingProxyType, got {type(raw_params).__name__}")

        stats = {"nodes": 0, "units": 0}
        frozen = deep_freeze_parameter(dict(raw_params), depth=0, seen_ids=set(), stats=stats)
        if not isinstance(frozen, MappingProxyType):
            raise DeviceCommandValidationError("Parameters must resolve to an immutable MappingProxyType")
        return frozen

    @staticmethod
    def validate_and_canonicalize_command_result(raw_result: Any) -> Mapping[str, Any]:
        """Validate command result payload structure and byte limits."""
        if raw_result is None:
            return MappingProxyType({})
        if not isinstance(raw_result, (dict, MappingProxyType)):
            raise DeviceCommandResultValidationError(f"Command result must be a dictionary, got {type(raw_result).__name__}")

        stats = {"nodes": 0, "units": 0}
        try:
            frozen = deep_freeze_parameter(dict(raw_result), depth=0, seen_ids=set(), stats=stats)
        except Exception as e:
            raise DeviceCommandResultValidationError(f"Command result failed canonicalization: {e}") from e

        serialized = json.dumps(frozen, default=lambda o: dict(o) if isinstance(o, MappingProxyType) else o, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
        byte_len = len(serialized.encode("utf-8"))
        if byte_len > MAX_COMMAND_RESULT_BYTES:
            raise DeviceCommandResultValidationError(
                f"Command result size ({byte_len} bytes) exceeds limit ({MAX_COMMAND_RESULT_BYTES} bytes)"
            )
        return frozen

    @staticmethod
    def validate_and_canonicalize_query_result(raw_result: Any) -> Mapping[str, Any]:
        """Validate query result payload structure and byte limits."""
        if raw_result is None:
            return MappingProxyType({})
        if not isinstance(raw_result, (dict, MappingProxyType)):
            raise DeviceQueryResultValidationError(f"Query result must be a dictionary, got {type(raw_result).__name__}")

        stats = {"nodes": 0, "units": 0}
        try:
            frozen = deep_freeze_parameter(dict(raw_result), depth=0, seen_ids=set(), stats=stats)
        except Exception as e:
            raise DeviceQueryResultValidationError(f"Query result failed canonicalization: {e}") from e

        serialized = json.dumps(frozen, default=lambda o: dict(o) if isinstance(o, MappingProxyType) else o, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
        byte_len = len(serialized.encode("utf-8"))
        if byte_len > MAX_QUERY_RESULT_BYTES:
            raise DeviceQueryResultValidationError(
                f"Query result size ({byte_len} bytes) exceeds limit ({MAX_QUERY_RESULT_BYTES} bytes)"
            )
        return frozen
