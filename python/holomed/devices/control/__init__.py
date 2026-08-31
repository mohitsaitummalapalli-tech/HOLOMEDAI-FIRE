"""HoloMed AI - Device command and query control plane exports."""

from __future__ import annotations

from holomed.devices.control.exceptions import (
    CapabilityUnauthorizedError,
    CommandNotFoundError,
    ControlCapacityError,
    DeviceCommandResultValidationError,
    DeviceCommandValidationError,
    DeviceControlError,
    DeviceNotActiveError,
    DeviceNotFoundError,
    DeviceQueryResultValidationError,
    DeviceStateError,
    IdempotencyConflictError,
    QueryNotFoundError,
)
from holomed.devices.control.idempotency import (
    IdempotencyTracker,
    compute_payload_hash,
)
from holomed.devices.control.manager import DeviceControlManager
from holomed.devices.control.models import (
    CommandHandler,
    DeviceCommandDefinition,
    DeviceQueryDefinition,
    IdempotencyRecord,
    MAX_COMMAND_RESULT_BYTES,
    MAX_IDEMPOTENCY_KEYS,
    MAX_QUERY_RESULT_BYTES,
    MAX_REGISTERED_COMMANDS,
    MAX_REGISTERED_QUERIES,
    MAX_RESULT_CONTAINER_WIDTH,
    MAX_RESULT_DEPTH,
    MAX_RESULT_TOTAL_NODES,
    MAX_RESULT_TOTAL_UNITS,
    QueryHandler,
)
from holomed.devices.control.verifier import CommandVerifier

__all__ = [
    # Manager
    "DeviceControlManager",
    # Models & Handlers
    "DeviceCommandDefinition",
    "DeviceQueryDefinition",
    "IdempotencyRecord",
    "CommandHandler",
    "QueryHandler",
    # Components
    "IdempotencyTracker",
    "compute_payload_hash",
    "CommandVerifier",
    # Constants
    "MAX_REGISTERED_COMMANDS",
    "MAX_REGISTERED_QUERIES",
    "MAX_IDEMPOTENCY_KEYS",
    "MAX_COMMAND_RESULT_BYTES",
    "MAX_QUERY_RESULT_BYTES",
    "MAX_RESULT_DEPTH",
    "MAX_RESULT_CONTAINER_WIDTH",
    "MAX_RESULT_TOTAL_NODES",
    "MAX_RESULT_TOTAL_UNITS",
    # Exceptions
    "DeviceControlError",
    "CommandNotFoundError",
    "QueryNotFoundError",
    "DeviceNotActiveError",
    "DeviceStateError",
    "CapabilityUnauthorizedError",
    "DeviceCommandValidationError",
    "DeviceQueryResultValidationError",
    "DeviceCommandResultValidationError",
    "IdempotencyConflictError",
    "ControlCapacityError",
    "DeviceNotFoundError",
]
