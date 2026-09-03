# -*- coding: utf-8 -*-
"""M19/M21 — Clinical Execution Gateway & Safety Gate Enforcement Service.

This package establishes the canonical dual-gate execution coordination layer
enforcing M18 cross-service safety decisions and M10 clinical workflow authorization
before invoking M14 tracked navigation mechanics or M07 clinical tools.
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
    ExecutionAuthorizationError,
    ExecutionBlockedError,
    ExecutionCapacityError,
    ExecutionError,
    ExecutionGeometryError,
    ExecutionLifecycleError,
    ExecutionSequenceError,
    ExecutionShutdownError,
    ExecutionValidationError,
)
from holomed.execution.models import (
    ExecutionStatus,
    NavigationExecutionRequest,
    NavigationExecutionResult,
    NavigationExecutionStatusRecord,
    PlanningExecutionRequest,
    PlanningExecutionResult,
    RecoveryReorientationExecutionRequest,
    RecoveryReorientationExecutionResult,
    RegistrationExecutionRequest,
    RegistrationExecutionResult,
    ToolExecutionRequest,
    ToolExecutionResult,
    TrajectoryBindingExecutionRequest,
    TrajectoryBindingExecutionResult,
    WorkflowResumptionExecutionRequest,
    WorkflowResumptionExecutionResult,
)
from holomed.execution.service import (
    ClinicalExecutionGatewayService,
    NavigationExecutionService,
)

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
    "PlanningExecutionRequest",
    "PlanningExecutionResult",
    "RecoveryReorientationExecutionRequest",
    "RecoveryReorientationExecutionResult",
    "RegistrationExecutionRequest",
    "RegistrationExecutionResult",
    "ToolExecutionRequest",
    "ToolExecutionResult",
    "TrajectoryBindingExecutionRequest",
    "TrajectoryBindingExecutionResult",
    "WorkflowResumptionExecutionRequest",
    "WorkflowResumptionExecutionResult",
    # Exceptions
    "ExecutionAuthorizationError",
    "ExecutionBlockedError",
    "ExecutionCapacityError",
    "ExecutionError",
    "ExecutionGeometryError",
    "ExecutionLifecycleError",
    "ExecutionSequenceError",
    "ExecutionShutdownError",
    "ExecutionValidationError",
    # Service
    "ClinicalExecutionGatewayService",
    "NavigationExecutionService",
)
