# -*- coding: utf-8 -*-
"""M19 — Navigation Safety Gate Execution Enforcement & Coordination Gateway.

This package establishes the canonical dual-gate execution coordination layer
enforcing M18 cross-service safety decisions and M10 clinical workflow authorization
before invoking M14 tracked navigation mechanics.
"""

from __future__ import annotations

from holomed.execution.constants import (
    EXECUTION_SCHEMA_VERSION,
    MAX_ACTIVE_EXECUTION_SESSIONS,
    MAX_EXECUTION_HISTORY,
    SERVICE_NAME,
    STRUCTURAL_RESOURCE_IDS,
)
from holomed.execution.exceptions import (
    ExecutionBlockedError,
    ExecutionCapacityError,
    ExecutionError,
    ExecutionGeometryError,
    ExecutionLifecycleError,
    ExecutionShutdownError,
    ExecutionValidationError,
)
from holomed.execution.models import (
    ExecutionStatus,
    NavigationExecutionRequest,
    NavigationExecutionResult,
    NavigationExecutionStatusRecord,
    RecoveryReorientationExecutionRequest,
    RecoveryReorientationExecutionResult,
    TrajectoryBindingExecutionRequest,
    TrajectoryBindingExecutionResult,
)
from holomed.execution.service import NavigationExecutionService

__all__ = (
    # Constants
    "EXECUTION_SCHEMA_VERSION",
    "MAX_ACTIVE_EXECUTION_SESSIONS",
    "MAX_EXECUTION_HISTORY",
    "SERVICE_NAME",
    "STRUCTURAL_RESOURCE_IDS",
    # Enums & Models
    "ExecutionStatus",
    "NavigationExecutionRequest",
    "NavigationExecutionResult",
    "NavigationExecutionStatusRecord",
    "RecoveryReorientationExecutionRequest",
    "RecoveryReorientationExecutionResult",
    "TrajectoryBindingExecutionRequest",
    "TrajectoryBindingExecutionResult",
    # Exceptions
    "ExecutionBlockedError",
    "ExecutionCapacityError",
    "ExecutionError",
    "ExecutionGeometryError",
    "ExecutionLifecycleError",
    "ExecutionShutdownError",
    "ExecutionValidationError",
    # Service
    "NavigationExecutionService",
)
