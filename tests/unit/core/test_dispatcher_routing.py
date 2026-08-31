"""Tests for M00.4 MessageDispatcher Routing, Postconditions, Safety Limits, and Tracing."""

from datetime import datetime, timedelta, timezone
from typing import Any
import pytest

from holomed.configuration.models import AppConfig, EnvironmentProfile, LogLevel, SecretString
from holomed.core.dispatcher import MessageDispatcher
from holomed.core.exceptions import (
    CorrelationError,
    CycleDetectedError,
    InvalidHandlerResponseError,
    PayloadValidationError,
    RecursionDepthExceededError,
    TimestampValidationError,
    UnroutableMessageError,
)
from holomed.core.models import DeadLetterReason
from holomed.core.tracing import CausalTracer
from holomed.protocol.builders import (
    create_command,
    create_event,
    create_query,
    create_response,
)
from holomed.protocol.models import MessageEnvelope, MessageType
from holomed.runtime.context import RuntimeContext


def create_test_context(secret: str = "TOP_SECRET_KEY") -> RuntimeContext:
    config = AppConfig(
        app_name="HoloMed AI",
        environment=EnvironmentProfile.TESTING,
        host="127.0.0.1",
        port=8000,
        log_level=LogLevel.DEBUG,
        gemini_api_key=SecretString(secret),
    )
    return RuntimeContext(app_config=config, epoch_id=1)


class TestDispatcherRouting:
    """Test command, query, event, and response message routing."""

    def test_command_routing_success(self) -> None:
        dispatcher = MessageDispatcher()
        dispatcher.initialize(create_test_context())

        def handle_ping(req: MessageEnvelope) -> MessageEnvelope:
            return CausalTracer.derive_reply(req, responder_source="pong_service", payload={"pong": True})

        dispatcher.register_command_handler("system.ping", handle_ping, "pong_service")
        dispatcher.start()

        cmd = create_command("system.ping", source="client_service", payload={"seq": 1})
        resp = dispatcher.dispatch(cmd)

        assert resp is not None
        assert resp.message_type == MessageType.RESPONSE
        assert resp.correlation_id == cmd.correlation_id
        assert resp.causation_id == cmd.message_id
        assert resp.target == "client_service"
        assert resp.payload == {"pong": True}
        assert dispatcher.dead_letter_queue.count == 0

    def test_command_no_handler_dlq_and_unroutable(self) -> None:
        dispatcher = MessageDispatcher()
        dispatcher.initialize(create_test_context())
        dispatcher.start()

        cmd = create_command("unregistered.cmd", source="client_service")
        with pytest.raises(UnroutableMessageError) as exc_info:
            dispatcher.dispatch(cmd)

        assert "No command handler" in str(exc_info.value)
        assert dispatcher.dead_letter_queue.count == 1
        rec = dispatcher.dead_letter_queue.records[0]
        assert rec.reason == DeadLetterReason.NO_HANDLER
        assert rec.envelope is cmd

    def test_query_routing_and_no_handler(self) -> None:
        dispatcher = MessageDispatcher()
        dispatcher.initialize(create_test_context())

        def handle_status(req: MessageEnvelope) -> MessageEnvelope:
            return CausalTracer.derive_reply(req, "status_service", {"status": "ok"})

        dispatcher.register_query_handler("system.status", handle_status, "status_service")
        dispatcher.start()

        qry = create_query("system.status", source="monitor")
        resp = dispatcher.dispatch(qry)
        assert resp is not None
        assert resp.payload == {"status": "ok"}

        unreg_qry = create_query("unknown.query", source="monitor")
        with pytest.raises(UnroutableMessageError):
            dispatcher.dispatch(unreg_qry)
        assert dispatcher.dead_letter_queue.count == 1
        assert dispatcher.dead_letter_queue.records[0].reason == DeadLetterReason.NO_HANDLER

    def test_event_fan_out_and_subscriber_isolation(self) -> None:
        dispatcher = MessageDispatcher()
        dispatcher.initialize(create_test_context())

        delivered: list[str] = []

        def sub_good_1(env: MessageEnvelope) -> None:
            delivered.append("good_1")

        def sub_failing(env: MessageEnvelope) -> None:
            raise RuntimeError("Subscriber explosion with secret TOP_SECRET_KEY")

        def sub_good_2(env: MessageEnvelope) -> None:
            delivered.append("good_2")

        # Register: order will be (service_name ASC, registration_index ASC)
        dispatcher.subscribe_event("telemetry.**", sub_good_1, "svc_a")
        dispatcher.subscribe_event("telemetry.**", sub_failing, "svc_b")
        dispatcher.subscribe_event("telemetry.**", sub_good_2, "svc_c")

        dispatcher.start()

        ev = create_event("telemetry.temp", source="sensor")
        result = dispatcher.dispatch(ev)
        assert result is None

        # Both good subscribers ran despite sub_failing raising an exception
        assert delivered == ["good_1", "good_2"]

        # Failure recorded in DLQ with SUBSCRIBER_EXCEPTION and secret redacted
        assert dispatcher.dead_letter_queue.count == 1
        rec = dispatcher.dead_letter_queue.records[0]
        assert rec.reason == DeadLetterReason.SUBSCRIBER_EXCEPTION
        assert "<redacted>" in rec.diagnostic
        assert "TOP_SECRET_KEY" not in rec.diagnostic

    def test_event_zero_subscribers_no_dlq_noise(self) -> None:
        dispatcher = MessageDispatcher()
        dispatcher.initialize(create_test_context())
        dispatcher.start()

        ev = create_event("unheard.event", source="lonely_sensor")
        dispatcher.dispatch(ev)
        assert dispatcher.dead_letter_queue.count == 0

    def test_response_correlation_listener_one_shot(self) -> None:
        dispatcher = MessageDispatcher()
        dispatcher.initialize(create_test_context())
        dispatcher.start()

        cmd = create_command("sys.req", source="client")
        callback_received: list[MessageEnvelope] = []

        def callback(resp_env: MessageEnvelope) -> None:
            callback_received.append(resp_env)

        dispatcher.register_correlation_listener(cmd.correlation_id, callback, timeout_seconds=10.0)

        # Dispatch correlated response
        resp = CausalTracer.derive_reply(cmd, responder_source="server", payload={"ack": True})
        dispatcher.dispatch(resp)

        assert len(callback_received) == 1
        assert callback_received[0].correlation_id == cmd.correlation_id

        # Second delivery of same response must fail because listener was one-shot removed BEFORE callback
        with pytest.raises(UnroutableMessageError):
            dispatcher.dispatch(resp)

        assert dispatcher.dead_letter_queue.count == 1
        assert dispatcher.dead_letter_queue.records[0].reason == DeadLetterReason.NO_HANDLER


class TestHandlerPostconditionsAndBoundaries:
    """Test handler postconditions, exception boundaries, and secret redaction."""

    def test_handler_postcondition_violations(self) -> None:
        dispatcher = MessageDispatcher()
        dispatcher.initialize(create_test_context())

        # Test case 1: Handler returns non-MessageEnvelope
        dispatcher.register_command_handler("test.badtype", lambda req: "not an envelope", "svc_bad")  # type: ignore[return-value]

        # Test case 2: Handler returns mismatched correlation_id
        def bad_corr_id(req: MessageEnvelope) -> MessageEnvelope:
            reply = CausalTracer.derive_reply(req, "svc_bad")
            object.__setattr__(reply, "correlation_id", "00000000-0000-4000-8000-000000000000")
            return reply

        dispatcher.register_command_handler("test.badcorr", bad_corr_id, "svc_bad")
        dispatcher.start()

        cmd1 = create_command("test.badtype", "client")
        with pytest.raises(InvalidHandlerResponseError):
            dispatcher.dispatch(cmd1)

        cmd2 = create_command("test.badcorr", "client")
        with pytest.raises(InvalidHandlerResponseError):
            dispatcher.dispatch(cmd2)

        assert dispatcher.dead_letter_queue.count == 2
        for rec in dispatcher.dead_letter_queue.records:
            assert rec.reason == DeadLetterReason.INVALID_HANDLER_RESPONSE

    def test_handler_exception_synthesizes_error_envelope(self) -> None:
        dispatcher = MessageDispatcher()
        dispatcher.initialize(create_test_context(secret="DB_PASSWORD_12345"))

        def exploding_handler(req: MessageEnvelope) -> MessageEnvelope:
            raise ValueError("Failed connecting to DB with password DB_PASSWORD_12345")

        dispatcher.register_command_handler("db.query", exploding_handler, "db_svc")
        dispatcher.start()

        cmd = create_command("db.query", "client")
        err_resp = dispatcher.dispatch(cmd)

        assert err_resp is not None
        assert err_resp.message_type == MessageType.ERROR
        assert err_resp.correlation_id == cmd.correlation_id
        assert err_resp.causation_id == cmd.message_id
        assert err_resp.target == "client"
        # Secret was redacted from error message and payload
        assert "DB_PASSWORD_12345" not in err_resp.payload["error_message"]
        assert "<redacted>" in err_resp.payload["error_message"]

        assert dispatcher.dead_letter_queue.count == 1
        rec = dispatcher.dead_letter_queue.records[0]
        assert rec.reason == DeadLetterReason.HANDLER_EXCEPTION
        assert "DB_PASSWORD_12345" not in rec.diagnostic
        assert "<redacted>" in rec.diagnostic


class TestPayloadAndTimestampValidation:
    """Test payload canonical serialization, 1 MiB limit, and exact timestamp tolerance boundaries."""

    def test_payload_over_1_mib_rejected(self) -> None:
        dispatcher = MessageDispatcher()
        dispatcher.initialize(create_test_context())
        dispatcher.start()

        # Create envelope with payload > 1 MiB
        large_payload = {"data": "x" * (1_048_576 + 10)}
        cmd = create_command("test.large", "client", payload=large_payload)

        with pytest.raises(PayloadValidationError):
            dispatcher.dispatch(cmd)

        assert dispatcher.dead_letter_queue.count == 1
        assert dispatcher.dead_letter_queue.records[0].reason == DeadLetterReason.CORRUPTED_ENVELOPE

    def test_timestamp_tolerance_exact_boundaries(self) -> None:
        dispatcher = MessageDispatcher()
        dispatcher.initialize(create_test_context())
        dispatcher.register_command_handler("test.time", lambda req: CausalTracer.derive_reply(req, "svc"), "svc")
        dispatcher.start()

        fixed_now = datetime(2026, 8, 31, 12, 0, 0, 0, tzinfo=timezone.utc)
        dispatcher.set_time_provider(lambda: fixed_now)

        # 1. Future check: exactly +5.0 sec => ACCEPTED
        dt_exact_future = fixed_now + timedelta(seconds=5.0)
        cmd_exact_future = create_command("test.time", "client")
        object.__setattr__(cmd_exact_future, "timestamp_utc", dt_exact_future.strftime("%Y-%m-%dT%H:%M:%S.%fZ"))
        dispatcher.dispatch(cmd_exact_future)

        # 2. Future check: +5.001 sec => REJECTED (FUTURE_TIMESTAMP_EXCEEDED)
        dt_over_future = fixed_now + timedelta(seconds=5.001)
        cmd_over_future = create_command("test.time", "client")
        object.__setattr__(cmd_over_future, "timestamp_utc", dt_over_future.strftime("%Y-%m-%dT%H:%M:%S.%fZ"))
        with pytest.raises(TimestampValidationError):
            dispatcher.dispatch(cmd_over_future)

        # 3. Max age check: exactly 300.0 sec old => ACCEPTED
        dt_exact_age = fixed_now - timedelta(seconds=300.0)
        cmd_exact_age = create_command("test.time", "client")
        object.__setattr__(cmd_exact_age, "timestamp_utc", dt_exact_age.strftime("%Y-%m-%dT%H:%M:%S.%fZ"))
        dispatcher.dispatch(cmd_exact_age)

        # 4. Max age check: 300.001 sec old => REJECTED (MESSAGE_EXPIRED)
        dt_expired = fixed_now - timedelta(seconds=300.001)
        cmd_expired = create_command("test.time", "client")
        object.__setattr__(cmd_expired, "timestamp_utc", dt_expired.strftime("%Y-%m-%dT%H:%M:%S.%fZ"))
        with pytest.raises(TimestampValidationError):
            dispatcher.dispatch(cmd_expired)

        # DLQ has exactly 2 records from the 2 violations
        assert dispatcher.dead_letter_queue.count == 2
        assert dispatcher.dead_letter_queue.records[0].reason == DeadLetterReason.FUTURE_TIMESTAMP_EXCEEDED
        assert dispatcher.dead_letter_queue.records[1].reason == DeadLetterReason.MESSAGE_EXPIRED


class TestRecursionAndCycleSafety:
    """Test recursion depth 16 legal, 17 rejected, direct re-entry, and causal cycles."""

    def test_recursion_depth_16_legal_17_rejected(self) -> None:
        dispatcher = MessageDispatcher()
        dispatcher.initialize(create_test_context())

        depth_counter = 0

        def recursive_handler(req: MessageEnvelope) -> MessageEnvelope:
            nonlocal depth_counter
            depth_counter += 1
            if depth_counter < 16:
                # Dispatch sub-command with distinct message_id
                sub_cmd = create_command(
                    "recursive.cmd",
                    source="recursive_svc",
                    payload={"depth": depth_counter},
                )
                dispatcher.dispatch(sub_cmd)
                return CausalTracer.derive_reply(req, "recursive_svc", {"depth": depth_counter})
            elif depth_counter == 16:
                # Attempt 17th dispatch: MUST be rejected before push!
                sub_cmd_17 = create_command(
                    "recursive.cmd",
                    source="recursive_svc",
                    payload={"depth": 17},
                )
                with pytest.raises(RecursionDepthExceededError):
                    dispatcher.dispatch(sub_cmd_17)
                return CausalTracer.derive_reply(req, "recursive_svc", {"reached": 16})
            return CausalTracer.derive_reply(req, "recursive_svc")

        dispatcher.register_command_handler("recursive.cmd", recursive_handler, "recursive_svc")
        dispatcher.start()

        root_cmd = create_command("recursive.cmd", "client")
        resp = dispatcher.dispatch(root_cmd)
        assert resp is not None
        assert depth_counter == 16
        # DLQ has the 17th rejected insertion
        assert dispatcher.dead_letter_queue.count == 1
        assert dispatcher.dead_letter_queue.records[0].reason == DeadLetterReason.RECURSION_DEPTH_EXCEEDED

    def test_cycle_direct_reentry_rejected(self) -> None:
        """A -> A direct re-entry with identical message_id."""
        dispatcher = MessageDispatcher()
        dispatcher.initialize(create_test_context())

        def reentrant_handler(req: MessageEnvelope) -> MessageEnvelope:
            # Re-dispatch exact same envelope
            return dispatcher.dispatch(req)  # type: ignore[return-value]

        dispatcher.register_command_handler("reentrant.cmd", reentrant_handler, "svc")
        dispatcher.start()

        cmd = create_command("reentrant.cmd", "client")
        with pytest.raises(CycleDetectedError):
            dispatcher.dispatch(cmd)

        assert dispatcher.dead_letter_queue.count == 1
        assert dispatcher.dead_letter_queue.records[0].reason == DeadLetterReason.CYCLE_DETECTED

    def test_same_metadata_different_message_ids_succeed(self) -> None:
        """Legitimate distinct messages with identical metadata MUST succeed."""
        dispatcher = MessageDispatcher()
        dispatcher.initialize(create_test_context())

        call_count = 0

        def standard_handler(req: MessageEnvelope) -> MessageEnvelope:
            nonlocal call_count
            call_count += 1
            return CausalTracer.derive_reply(req, "svc", {"count": call_count})

        dispatcher.register_command_handler("telemetry.collect", standard_handler, "svc")
        dispatcher.start()

        # Two distinct envelopes with identical message_name, source, target, payload
        cmd1 = create_command("telemetry.collect", "sensor_node", payload={"v": 1})
        cmd2 = create_command("telemetry.collect", "sensor_node", payload={"v": 1})
        assert cmd1.message_id != cmd2.message_id

        resp1 = dispatcher.dispatch(cmd1)
        resp2 = dispatcher.dispatch(cmd2)

        assert resp1 is not None and resp2 is not None
        assert call_count == 2
        assert dispatcher.dead_letter_queue.count == 0


class TestCorrelationListenerManagement:
    """Test correlation listener registration, capacity, and deterministic pruning."""

    def test_correlation_listener_capacity_1024(self) -> None:
        dispatcher = MessageDispatcher()
        dispatcher.initialize(create_test_context())
        dispatcher.start()

        import uuid
        for i in range(1024):
            corr_id = str(uuid.uuid4())
            dispatcher.register_correlation_listener(corr_id, lambda e: None, 60.0)

        assert len(dispatcher.correlation_listeners) == 1024

        # 1025th registration must raise CorrelationError
        with pytest.raises(CorrelationError):
            dispatcher.register_correlation_listener(str(uuid.uuid4()), lambda e: None, 60.0)

    def test_prune_expired_listeners_deterministic_order(self) -> None:
        dispatcher = MessageDispatcher()
        dispatcher.initialize(create_test_context())
        dispatcher.start()

        base_time = datetime(2026, 8, 31, 10, 0, 0, tzinfo=timezone.utc)
        dispatcher.set_time_provider(lambda: base_time)

        # Register listeners with different timeouts: uuid1 (+10s), uuid2 (+5s), uuid3 (+5s)
        id1 = "11111111-1111-4111-8111-111111111111"
        id2 = "22222222-2222-4222-8222-222222222222"
        id3 = "33333333-3333-4333-8333-333333333333"

        dispatcher.register_correlation_listener(id1, lambda e: None, 10.0)
        dispatcher.register_correlation_listener(id3, lambda e: None, 5.0)
        dispatcher.register_correlation_listener(id2, lambda e: None, 5.0)

        # Advance clock by 6 seconds: id2 and id3 expire (+5s), id1 (+10s) remains active
        check_time = base_time + timedelta(seconds=6.0)
        pruned = dispatcher.prune_expired_listeners(now_utc=check_time)

        # Deterministic prune order: deadline_utc ASC then correlation_id ASC
        # id2 and id3 have same deadline, id2 < id3 alphabetically
        assert pruned == (id2, id3)
        assert id1 in dispatcher.correlation_listeners
        assert len(dispatcher.correlation_listeners) == 1
        assert dispatcher.dead_letter_queue.count == 2
        for rec in dispatcher.dead_letter_queue.records:
            assert rec.reason == DeadLetterReason.CORRELATION_TIMEOUT
