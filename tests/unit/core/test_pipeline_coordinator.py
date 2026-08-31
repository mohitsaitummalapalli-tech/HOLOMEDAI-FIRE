"""Tests for M00.4 Pipeline Coordinator."""

from typing import Any, Mapping
import pytest

from holomed.configuration.models import AppConfig, EnvironmentProfile, LogLevel
from holomed.core.exceptions import (
    PipelineError,
    PipelineRollbackError,
    PipelineStageExecutionError,
    PipelineTopologyError,
)
from holomed.core.models import (
    PipelineStageDescriptor,
    PipelineStageResult,
    PipelineStageStatus,
)
from holomed.core.pipeline import PipelineCoordinator
from holomed.runtime.context import RuntimeContext


def create_test_context() -> RuntimeContext:
    config = AppConfig(
        app_name="HoloMed AI",
        environment=EnvironmentProfile.TESTING,
        host="127.0.0.1",
        port=8000,
        log_level=LogLevel.DEBUG,
    )
    return RuntimeContext(app_config=config, epoch_id=1)


class TestPipelineCoordinator:
    """Test deterministic Kahn+min-heap compilation, execution, immutability, and rollback."""

    def test_pipeline_dependencies_and_resources(self) -> None:
        coordinator = PipelineCoordinator()
        assert coordinator.dependencies == ("core.dispatcher",)
        assert coordinator.resources.is_empty is True

        ctx = create_test_context()
        coordinator.initialize(ctx)
        assert not coordinator.resources.is_empty
        assert "pipeline.coordinator" in [h.resource_id for h in coordinator.resources.outstanding_handles]

        coordinator.start()
        coordinator.stop()
        assert coordinator.resources.is_empty is True

    def test_deterministic_kahn_min_heap_topological_compilation(self) -> None:
        coordinator = PipelineCoordinator()
        coordinator.initialize(create_test_context())

        # Register stages out of topological/alphabetical order:
        # gamma depends on beta
        # beta depends on alpha
        # delta has no deps (ties with alpha, alpha < delta alphabetically)
        s_gamma = PipelineStageDescriptor("gamma", lambda ctx: {"g": 3}, dependencies=("beta",))
        s_delta = PipelineStageDescriptor("delta", lambda ctx: {"d": 4}, dependencies=())
        s_beta = PipelineStageDescriptor("beta", lambda ctx: {"b": 2}, dependencies=("alpha",))
        s_alpha = PipelineStageDescriptor("alpha", lambda ctx: {"a": 1}, dependencies=())

        coordinator.register_stage(s_gamma)
        coordinator.register_stage(s_delta)
        coordinator.register_stage(s_beta)
        coordinator.register_stage(s_alpha)

        topo = coordinator.compile()
        # Alphabetical tie-break between alpha and delta: alpha chosen first
        # alpha enables beta, but delta was also ready.
        # Let's verify Kahn with min-heap:
        # Initial ready: alpha, delta -> pop alpha -> dependents: beta (in_degree: 0 -> push beta)
        # Heap has: beta, delta -> pop beta (beta < delta) -> dependents: gamma (in_degree: 0 -> push gamma)
        # Heap has: delta, gamma -> pop delta (delta < gamma)
        # Heap has: gamma -> pop gamma
        assert topo == ("alpha", "beta", "delta", "gamma")

    def test_cycle_and_missing_dependency_rejection(self) -> None:
        coordinator = PipelineCoordinator()
        coordinator.initialize(create_test_context())

        # Missing dependency
        coordinator.register_stage(PipelineStageDescriptor("st1", lambda ctx: {}, dependencies=("nonexistent",)))
        with pytest.raises(PipelineTopologyError):
            coordinator.compile()

        # Cycle rejection
        c2 = PipelineCoordinator()
        c2.initialize(create_test_context())
        c2.register_stage(PipelineStageDescriptor("node_a", lambda ctx: {}, dependencies=("node_b",)))
        c2.register_stage(PipelineStageDescriptor("node_b", lambda ctx: {}, dependencies=("node_a",)))
        with pytest.raises(PipelineTopologyError):
            c2.compile()

    def test_stage_capacity_64_exceeded(self) -> None:
        coordinator = PipelineCoordinator()
        coordinator.initialize(create_test_context())

        for i in range(64):
            coordinator.register_stage(PipelineStageDescriptor(f"stage_{i:02d}", lambda ctx: {}))

        with pytest.raises(PipelineTopologyError):
            coordinator.register_stage(PipelineStageDescriptor("stage_64", lambda ctx: {}))

    def test_successful_execution_and_context_immutability(self) -> None:
        coordinator = PipelineCoordinator()
        coordinator.initialize(create_test_context())

        execution_order: list[str] = []

        def run_a(ctx: Mapping[str, Any]) -> dict[str, Any]:
            execution_order.append("A")
            # Verify input context cannot be mutated
            with pytest.raises(TypeError):
                ctx["mutate"] = True  # type: ignore[index]
            return {"step_a": 100}

        def run_b(ctx: Mapping[str, Any]) -> dict[str, Any]:
            execution_order.append("B")
            assert ctx["step_a"] == 100
            return {"step_b": 200}

        coordinator.register_stage(PipelineStageDescriptor("stage_a", run_a))
        coordinator.register_stage(PipelineStageDescriptor("stage_b", run_b, dependencies=("stage_a",)))
        coordinator.start()

        res = coordinator.execute({"init": 1})
        assert res.success is True
        assert execution_order == ["A", "B"]
        assert len(res.stages) == 2
        assert res.stages[0].status == PipelineStageStatus.COMPLETED
        assert res.stages[0].output["step_a"] == 100
        assert res.stages[1].status == PipelineStageStatus.COMPLETED
        assert res.stages[1].output["step_b"] == 200

    def test_execution_failure_clean_rollback(self) -> None:
        coordinator = PipelineCoordinator()
        coordinator.initialize(create_test_context())

        rolled_back: list[str] = []

        def rollback_1(ctx: Mapping[str, Any]) -> None:
            rolled_back.append("stage_1")

        def rollback_2(ctx: Mapping[str, Any]) -> None:
            rolled_back.append("stage_2")

        def failing_stage_3(ctx: Mapping[str, Any]) -> dict[str, Any]:
            raise ArithmeticError("Stage 3 calculation division by zero")

        coordinator.register_stage(PipelineStageDescriptor("s1", lambda ctx: {"v1": 1}, rollback=rollback_1))
        coordinator.register_stage(PipelineStageDescriptor("s2", lambda ctx: {"v2": 2}, dependencies=("s1",), rollback=rollback_2))
        coordinator.register_stage(PipelineStageDescriptor("s3", failing_stage_3, dependencies=("s2",)))
        coordinator.start()

        with pytest.raises(PipelineStageExecutionError) as exc_info:
            coordinator.execute()

        err = exc_info.value
        assert err.stage_name == "s3"
        assert err.stage_index == 2
        assert isinstance(err.original_stage_error, ArithmeticError)
        assert err.__cause__ is err.original_stage_error

        # Rollback executed in DESC order: s2 first, then s1
        assert rolled_back == ["stage_2", "stage_1"]

    def test_execution_failure_with_rollback_failure(self) -> None:
        """Rollback continues after individual rollback failures and raises PipelineRollbackError."""
        coordinator = PipelineCoordinator()
        coordinator.initialize(create_test_context())

        rollback_attempts: list[str] = []

        def rollback_1(ctx: Mapping[str, Any]) -> None:
            rollback_attempts.append("s1_ok")

        def failing_rollback_2(ctx: Mapping[str, Any]) -> None:
            rollback_attempts.append("s2_fail")
            raise RuntimeError("Rollback 2 disk IO error")

        coordinator.register_stage(PipelineStageDescriptor("s1", lambda ctx: {}, rollback=rollback_1))
        coordinator.register_stage(PipelineStageDescriptor("s2", lambda ctx: {}, dependencies=("s1",), rollback=failing_rollback_2))
        coordinator.register_stage(PipelineStageDescriptor("s3", lambda ctx: (_ for _ in ()).throw(ValueError("Stage 3 failed")), dependencies=("s2",)))
        coordinator.start()

        with pytest.raises(PipelineRollbackError) as exc_info:
            coordinator.execute()

        err = exc_info.value
        assert err.stage_name == "s3"
        assert err.stage_index == 2
        assert isinstance(err.original_stage_error, ValueError)
        assert err.__cause__ is err.original_stage_error
        assert len(err.rollback_failures) == 1
        failed_rb_name, failed_rb_idx, failed_rb_exc = err.rollback_failures[0]
        assert failed_rb_name == "s2"
        assert failed_rb_idx == 1
        assert isinstance(failed_rb_exc, RuntimeError)

        # Rollback continued after s2 failed, so s1 was also attempted!
        assert rollback_attempts == ["s2_fail", "s1_ok"]
