"""Hostile adversarial hardening suite for M00.4 Core subsystem."""

import sys
import uuid
import pytest

from holomed.configuration.models import AppConfig, EnvironmentProfile, LogLevel, SecretString
from holomed.core.dead_letter import DeadLetterQueue
from holomed.core.dispatcher import MessageDispatcher
from holomed.core.exceptions import (
    CoreError,
    CorrelationError,
    DeadLetterCapacityError,
    DispatcherLifecycleError,
    DuplicateSubscriptionError,
    InvalidHandlerResponseError,
    PayloadValidationError,
    PipelineRollbackError,
    PipelineStageExecutionError,
    PipelineTopologyError,
    RecursionDepthExceededError,
    ResourceCleanupRequiredError,
    TimestampValidationError,
    TopicValidationError,
    UnroutableMessageError,
)
from holomed.core.models import (
    DeadLetterOverflowPolicy,
    DeadLetterReason,
    DispatcherState,
    PipelineStageDescriptor,
)
from holomed.core.pipeline import PipelineCoordinator
from holomed.core.subscription import (
    SubscriptionRegistry,
    topic_matches,
    validate_concrete_topic,
    validate_topic_pattern,
)
from holomed.core.tracing import CausalTracer
from holomed.protocol.builders import create_command, create_event, create_query
from holomed.protocol.models import MessageEnvelope, MessageType
from holomed.runtime.context import RuntimeContext


def make_context(secret: str = "HOSTILE_AUDIT_SECRET_999") -> RuntimeContext:
    config = AppConfig(
        app_name="HoloMed AI",
        environment=EnvironmentProfile.TESTING,
        host="127.0.0.1",
        port=8000,
        log_level=LogLevel.DEBUG,
        gemini_api_key=SecretString(secret),
    )
    return RuntimeContext(app_config=config, epoch_id=42)


class TestHostileHardening:
    """Hostile adversarial stress tests."""

    def test_ten_thousand_dlq_insertions_drop_oldest(self) -> None:
        """10,000 DLQ insertions must maintain fixed bounded memory, correct evicted_count, and bounded warnings."""
        import warnings
        dlq = DeadLetterQueue(capacity=1000, overflow_policy=DeadLetterOverflowPolicy.DROP_OLDEST)
        with warnings.catch_warnings(record=True) as recorded:
            warnings.simplefilter("always")
            for i in range(10_000):
                dlq.record(envelope=None, reason=DeadLetterReason.NO_HANDLER, diagnostic=f"burst_{i}")
            assert len(recorded) == 1
            assert "Dead-letter queue overflow" in str(recorded[0].message)

        assert dlq.count == 1000
        assert dlq.evicted_count == 9000
        assert dlq.overflow_warned is True
        assert dlq.records[0].diagnostic == "burst_9000"
        assert dlq.records[-1].diagnostic == "burst_9999"

    def test_ten_thousand_dispatches_stress(self) -> None:
        """10,000 rapid dispatches through MessageDispatcher."""
        dispatcher = MessageDispatcher()
        dispatcher.initialize(make_context())

        call_count = 0

        def bench_handler(req: MessageEnvelope) -> MessageEnvelope:
            nonlocal call_count
            call_count += 1
            return CausalTracer.derive_reply(req, "bench_service", {"seq": call_count})

        dispatcher.register_command_handler("bench.ping", bench_handler, "bench_service")
        dispatcher.start()

        for i in range(10_000):
            cmd = create_command("bench.ping", source="bench_client", payload={"i": i})
            resp = dispatcher.dispatch(cmd)
            assert resp is not None
            assert resp.payload["seq"] == i + 1

        assert call_count == 10_000
        assert dispatcher.dead_letter_queue.count == 0

    def test_rollback_storm_stress(self) -> None:
        """Pipeline rollback storm with multiple cascading stage failures."""
        coordinator = PipelineCoordinator()
        coordinator.initialize(make_context())

        # Register 20 stages: stage 10 fails, stages 1..9 rollback, stage 5 and 3 fail rollback
        failed_rollbacks: list[str] = []

        def make_stage(idx: int) -> PipelineStageDescriptor:
            def execute_stage(ctx: dict[str, Any]) -> dict[str, Any]:
                if idx == 10:
                    raise RuntimeError(f"Storm failure at stage {idx}")
                return {f"k_{idx}": idx}

            def rollback_stage(ctx: dict[str, Any]) -> None:
                if idx in (3, 5):
                    failed_rollbacks.append(f"rb_fail_{idx}")
                    raise IOError(f"Rollback disk failure at stage {idx}")

            deps = (f"stage_{idx-1:02d}",) if idx > 0 else ()
            return PipelineStageDescriptor(f"stage_{idx:02d}", execute_stage, rollback=rollback_stage, dependencies=deps)

        for i in range(20):
            coordinator.register_stage(make_stage(i))

        coordinator.start()

        with pytest.raises(PipelineRollbackError) as exc_info:
            coordinator.execute()

        err = exc_info.value
        assert err.stage_name == "stage_10"
        assert len(err.rollback_failures) == 2
        # Both rollbacks failed as expected
        rb_failed_names = [f[0] for f in err.rollback_failures]
        assert "stage_05" in rb_failed_names
        assert "stage_03" in rb_failed_names

    def test_secret_leakage_adversarial_attempt(self) -> None:
        """Adversarial attempt to leak secrets through DLQ, logs, and error responses."""
        secret_token = "SUPER_SECRET_PATIENT_DATA_777"
        dispatcher = MessageDispatcher()
        dispatcher.initialize(make_context(secret=secret_token))

        def leaking_handler(req: MessageEnvelope) -> MessageEnvelope:
            raise ValueError(f"Secret exposed in error: {secret_token}")

        dispatcher.register_command_handler("confidential.cmd", leaking_handler, "leak_svc")
        dispatcher.start()

        cmd = create_command("confidential.cmd", "client")
        err_env = dispatcher.dispatch(cmd)

        assert err_env is not None
        assert err_env.message_type == MessageType.ERROR
        # Inspect all payload and error fields
        assert secret_token not in str(err_env.payload)
        assert "<redacted>" in err_env.payload["error_message"]

        # Inspect DLQ
        for rec in dispatcher.dead_letter_queue.records:
            assert secret_token not in rec.diagnostic
            assert "<redacted>" in rec.diagnostic

        # Inspect health check
        h = dispatcher.health()
        assert secret_token not in h.message

    def test_prohibited_imports_audit(self) -> None:
        """Verify no prohibited runtime modules are imported anywhere in holomed.core source code."""
        import ast
        from pathlib import Path
        core_dir = Path(__file__).resolve().parents[3] / "python" / "holomed" / "core"
        prohibited = {
            "socket",
            "asyncio",
            "threading",
            "multiprocessing",
            "subprocess",
            "cv2",
            "mediapipe",
            "google.generativeai",
        }
        for py_file in core_dir.glob("*.py"):
            tree = ast.parse(py_file.read_text(encoding="utf-8"), filename=str(py_file))
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        root_mod = alias.name.split(".")[0]
                        assert root_mod not in prohibited, f"Prohibited import '{alias.name}' found in {py_file.name}"
                elif isinstance(node, ast.ImportFrom):
                    if node.module:
                        root_mod = node.module.split(".")[0]
                        assert root_mod not in prohibited, f"Prohibited from-import '{node.module}' found in {py_file.name}"

    def test_recycled_message_id_rejection(self) -> None:
        """Attempting to dispatch an envelope with an in-flight message_id is rejected."""
        dispatcher = MessageDispatcher()
        dispatcher.initialize(make_context())

        cmd = create_command("sys.ping", "client")

        def recursive_caller(req: MessageEnvelope) -> MessageEnvelope:
            # Try to re-dispatch the exact same message_id
            return dispatcher.dispatch(cmd)  # type: ignore[return-value]

        dispatcher.register_command_handler("sys.ping", recursive_caller, "svc")
        dispatcher.start()

        with pytest.raises(CoreError):
            dispatcher.dispatch(cmd)

    def test_malformed_topics_hostile_corpus(self) -> None:
        """Adversarial corpus of malformed topics."""
        corpus = [
            "\x00",
            "\n\r",
            "../etc/passwd",
            "../../../",
            "SELECT * FROM topics;",
            "<script>alert(1)</script>",
            "a." * 100,
            "*.**.*.**",
            "a" * 65,
            ".",
            "..",
            "...",
            "valid.topic\x00extra",
            "UPPERCASE.TOPIC",
        ]
        for hostile_str in corpus:
            with pytest.raises(TopicValidationError):
                validate_concrete_topic(hostile_str)

    def test_repeated_cleanup_retries_resilience(self) -> None:
        """Verify retry_cleanup can be executed repeatedly until clean."""
        dispatcher = MessageDispatcher()
        dispatcher.initialize(make_context())
        dispatcher.start()

        orig_release = dispatcher.resources.release
        # Force failure
        dispatcher.resources.release = lambda res_id: (_ for _ in ()).throw(IOError("disk locked"))  # type: ignore[assignment]
        with pytest.raises(Exception):
            dispatcher.stop()

        assert dispatcher.state == DispatcherState.FAILED

        # 3 failed retries
        for _ in range(3):
            with pytest.raises(ResourceCleanupRequiredError):
                dispatcher.retry_cleanup()
            assert dispatcher.state == DispatcherState.FAILED

        # Restore working release function
        dispatcher.resources.release = orig_release  # type: ignore[assignment]
        # Clean retry
        dispatcher.retry_cleanup()
        assert dispatcher.resources.is_empty is True
        assert dispatcher.state == DispatcherState.FAILED
        dispatcher.stop()
        assert dispatcher.state == DispatcherState.STOPPED
