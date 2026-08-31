"""M00.4 Pipeline Coordinator — deterministic DAG pipeline execution and rollback engine.

Implements ``IService`` from ``holomed.runtime.service``.

Guarantees:
  * Topologically ordered execution via deterministic Kahn + min-heap.
  * Intermediate and result context immutability (MappingProxyType).
  * State isolation: stages cannot mutate prior or downstream stage outputs.
  * Immediate execution halt on stage failure.
  * Reverse-order rollback execution (stage_index DESC) continuing through failures.
  * Strict exception chaining preserving original stage error and all rollback failure records.
"""

from __future__ import annotations

import heapq
from datetime import datetime, timezone
from types import MappingProxyType
from typing import Any, Callable, Dict, List, Mapping, Optional, Tuple

from holomed.core.exceptions import (
    PipelineError,
    PipelineRollbackError,
    PipelineStageExecutionError,
    PipelineTopologyError,
)
from holomed.core.models import (
    MAX_PIPELINE_STAGES,
    PipelineResult,
    PipelineStageDescriptor,
    PipelineStageResult,
    PipelineStageStatus,
)
from holomed.runtime.context import RuntimeContext
from holomed.runtime.exceptions import ServiceShutdownError, ShutdownFailureRecord
from holomed.runtime.logging import SecretFilter, StructuredLogger
from holomed.runtime.models import (
    HealthStatus,
    OwnedResourceSet,
    ServiceHealth,
)
from holomed.runtime.service import IService


class PipelineCoordinator(IService):
    """Authoritative transactional pipeline orchestrator."""

    SERVICE_NAME = "core.pipeline"
    RESOURCE_NAME = "pipeline.coordinator"

    def __init__(
        self,
        *,
        secret_filter: Optional[SecretFilter] = None,
        logger: Optional[StructuredLogger] = None,
    ) -> None:
        self._secret_filter = secret_filter or SecretFilter()
        self._logger = logger or StructuredLogger(self.SERVICE_NAME, secret_filter=self._secret_filter)
        self._resources = OwnedResourceSet(self.SERVICE_NAME, epoch_id=0)

        self._stages: dict[str, PipelineStageDescriptor] = {}
        self._compiled_topology: tuple[str, ...] = ()
        self._is_initialized = False
        self._is_started = False
        self._is_failed = False

    # -----------------------------------------------------------------------
    # IService interface implementation
    # -----------------------------------------------------------------------

    @property
    def name(self) -> str:
        return self.SERVICE_NAME

    @property
    def dependencies(self) -> tuple[str, ...]:
        return ("core.dispatcher",)

    @property
    def resources(self) -> OwnedResourceSet:
        return self._resources

    @property
    def compiled_topology(self) -> tuple[str, ...]:
        return self._compiled_topology

    def initialize(self, context: RuntimeContext) -> None:
        """Acquire pipeline coordinator resource handle (INITIALIZED)."""
        self._resources = OwnedResourceSet(self.SERVICE_NAME, epoch_id=context.epoch_id)
        self._resources.acquire(self.RESOURCE_NAME)
        self._is_initialized = True
        self._is_started = False
        self._is_failed = False
        self._logger.info("PipelineCoordinator initialized", event="pipeline_initialized")

    def start(self) -> None:
        """Compile topology and activate coordinator (STARTED). No new resources."""
        if not self._is_initialized:
            raise PipelineError("PipelineCoordinator cannot start without prior initialization")
        if not self._compiled_topology and self._stages:
            self.compile()
        self._is_started = True
        self._logger.info("PipelineCoordinator started", event="pipeline_started")

    def stop(self) -> None:
        """Release owned resources and reset state."""
        failures: list[ShutdownFailureRecord] = []
        try:
            self._resources.release(self.RESOURCE_NAME)
        except Exception as e:
            self._resources.mark_release_failed(self.RESOURCE_NAME, str(e))
            failures.append(
                ShutdownFailureRecord(
                    service_name=self.SERVICE_NAME,
                    original_exception=e,
                    execution_index=0,
                    unreleased_resources=(self.RESOURCE_NAME,),
                )
            )

        self._stages.clear()
        self._compiled_topology = ()
        self._is_initialized = False
        self._is_started = False

        if failures or not self._resources.is_empty:
            self._is_failed = True
            raise ServiceShutdownError(
                f"Teardown failed on {self.SERVICE_NAME}",
                failures=tuple(failures),
            )

        self._logger.info("PipelineCoordinator stopped", event="pipeline_stopped")

    def health(self) -> ServiceHealth:
        """Synchronously evaluate in-process pipeline coordinator health."""
        now_str = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
        if self._is_failed or not self._resources.is_empty and not self._is_initialized:
            status = HealthStatus.FAILED
            msg = "PipelineCoordinator in failed or dirty resource state"
        elif self._is_started:
            status = HealthStatus.HEALTHY
            msg = f"Pipeline operational ({len(self._compiled_topology)} compiled stages)"
        elif self._is_initialized:
            status = HealthStatus.HEALTHY
            msg = f"Pipeline initialized ({len(self._stages)} registered stages)"
        else:
            status = HealthStatus.HEALTHY
            msg = "Pipeline uninitialized"

        return ServiceHealth(
            name=self.SERVICE_NAME,
            status=status,
            message=self._secret_filter.redact(msg),
            timestamp_utc=now_str,
        )

    # -----------------------------------------------------------------------
    # Registration & Topological Compilation (INITIALIZED only)
    # -----------------------------------------------------------------------

    def register_stage(self, stage: PipelineStageDescriptor) -> None:
        """Register a pipeline stage. Permitted ONLY in INITIALIZED state."""
        if not self._is_initialized or self._is_started:
            raise PipelineError(
                "register_stage allowed ONLY after initialize() and before start()"
            )
        if len(self._stages) >= MAX_PIPELINE_STAGES:
            raise PipelineTopologyError(
                f"Maximum pipeline stage capacity of {MAX_PIPELINE_STAGES} exceeded"
            )
        if stage.name in self._stages:
            raise PipelineTopologyError(f"Duplicate stage name: '{stage.name}'")
        if not stage.name or not isinstance(stage.name, str):
            raise PipelineTopologyError("Stage name must be a non-empty string")
        if not callable(stage.execute):
            raise PipelineTopologyError(f"Stage '{stage.name}' executor must be callable")

        self._stages[stage.name] = stage
        self._compiled_topology = ()  # Invalidate cached compilation

    def compile(self) -> tuple[str, ...]:
        """Compile stages into deterministic topological order using Kahn's algorithm with min-heap."""
        # 1. Validate missing dependencies
        for name, stage in self._stages.items():
            for dep in stage.dependencies:
                if dep not in self._stages:
                    raise PipelineTopologyError(
                        f"Stage '{name}' depends on unregistered stage '{dep}'"
                    )
                if dep == name:
                    raise PipelineTopologyError(f"Stage '{name}' cannot depend on itself")

        # 2. In-degree and adjacency calculation
        in_degree: dict[str, int] = {name: len(stage.dependencies) for name, stage in self._stages.items()}
        dependents: dict[str, list[str]] = {name: [] for name in self._stages}
        for name, stage in self._stages.items():
            for dep in stage.dependencies:
                dependents[dep].append(name)

        # 3. Min-heap queue for deterministic alphabetical tie-breaking
        ready_queue: list[str] = [name for name, deg in in_degree.items() if deg == 0]
        heapq.heapify(ready_queue)

        topology: list[str] = []
        while ready_queue:
            current = heapq.heappop(ready_queue)
            topology.append(current)
            for dep_on_curr in sorted(dependents[current]):
                in_degree[dep_on_curr] -= 1
                if in_degree[dep_on_curr] == 0:
                    heapq.heappush(ready_queue, dep_on_curr)

        if len(topology) != len(self._stages):
            raise PipelineTopologyError("Pipeline stages contain a cyclic dependency")

        self._compiled_topology = tuple(topology)
        return self._compiled_topology

    # -----------------------------------------------------------------------
    # Execution & Rollback Engine (STARTED only)
    # -----------------------------------------------------------------------

    def execute(self, initial_context: Optional[Mapping[str, Any]] = None) -> PipelineResult:
        """Execute compiled pipeline stages with transactional rollback on failure."""
        if not self._is_started:
            raise PipelineError("execute allowed ONLY in STARTED state")

        if not self._compiled_topology and self._stages:
            self.compile()

        # Immutable base context
        accumulated_context: dict[str, Any] = dict(initial_context or {})
        stage_results: list[PipelineStageResult] = []
        completed_stages: list[tuple[int, PipelineStageDescriptor, MappingProxyType]] = []

        for stage_idx, stage_name in enumerate(self._compiled_topology):
            stage = self._stages[stage_name]
            # Context passed to stage is strictly immutable defensive copy
            stage_input_context = MappingProxyType(dict(accumulated_context))

            try:
                raw_output = stage.execute(stage_input_context)
                if raw_output is None:
                    raw_output = {}
                elif not isinstance(raw_output, (dict, Mapping)):
                    raise TypeError(f"Stage '{stage_name}' output must be a Mapping, got {type(raw_output).__name__}")

                # Freeze stage output immutably
                frozen_output = MappingProxyType(dict(raw_output))
                # Update accumulated context without permitting mutation of prior keys
                accumulated_context.update(frozen_output)

                result = PipelineStageResult(
                    stage_name=stage_name,
                    stage_index=stage_idx,
                    status=PipelineStageStatus.COMPLETED,
                    output=frozen_output,
                )
                stage_results.append(result)
                completed_stages.append((stage_idx, stage, MappingProxyType(dict(accumulated_context))))

            except Exception as original_error:
                # Execution failed: halt immediately and record failure
                self._logger.error(
                    "Pipeline stage execution failed; initiating rollback",
                    extra={"stage_name": stage_name, "stage_index": stage_idx, "error": str(original_error)},
                    exc_info=True,
                )
                failed_result = PipelineStageResult(
                    stage_name=stage_name,
                    stage_index=stage_idx,
                    status=PipelineStageStatus.FAILED,
                    error=self._secret_filter.redact(str(original_error)),
                )
                stage_results.append(failed_result)

                # Rollback previously completed stages in DESC order
                rollback_failures: list[tuple[str, int, Exception]] = []
                for comp_idx, comp_stage, comp_ctx in reversed(completed_stages):
                    if comp_stage.rollback is not None:
                        try:
                            comp_stage.rollback(comp_ctx)
                        except Exception as rollback_err:
                            self._logger.error(
                                "Pipeline rollback stage failed",
                                extra={"stage_name": comp_stage.name, "stage_index": comp_idx, "error": str(rollback_err)},
                                exc_info=True,
                            )
                            rollback_failures.append((comp_stage.name, comp_idx, rollback_err))

                redacted_orig_msg = self._secret_filter.redact(str(original_error))
                if rollback_failures:
                    raise PipelineRollbackError(
                        f"Stage '{stage_name}' failed ({redacted_orig_msg}) and rollback encountered "
                        f"{len(rollback_failures)} failure(s)",
                        stage_name=stage_name,
                        stage_index=stage_idx,
                        original_stage_error=original_error,
                        rollback_failures=tuple(rollback_failures),
                    ) from original_error

                raise PipelineStageExecutionError(
                    f"Stage '{stage_name}' failed: {redacted_orig_msg}",
                    stage_name=stage_name,
                    stage_index=stage_idx,
                    original_stage_error=original_error,
                ) from original_error

        return PipelineResult(
            stages=tuple(stage_results),
            success=True,
        )
