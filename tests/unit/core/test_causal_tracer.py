"""Tests for M00.4 CausalTracer."""

from datetime import datetime, timezone
import pytest
from holomed.core.tracing import CausalTracer
from holomed.protocol.builders import create_command, create_query
from holomed.protocol.models import MessageType
from holomed.protocol.validation import validate_envelope, validate_uuid_v4


class TestCausalTracer:
    """Test derive_reply, derive_event, immutability, and M00.2 validation."""

    def test_derive_reply_invariants(self) -> None:
        cmd = create_command(
            message_name="device.power.on",
            source="ui_controller",
            target="device_manager",
            payload={"voltage": 12},
        )
        cmd_dict_before = cmd.to_dict()

        reply = CausalTracer.derive_reply(
            request=cmd,
            responder_source="device_manager",
            payload={"status": "powered_on"},
        )

        # Invariants
        assert reply.message_type == MessageType.RESPONSE
        assert reply.correlation_id == cmd.correlation_id
        assert reply.causation_id == cmd.message_id
        assert reply.target == cmd.source
        assert reply.source == "device_manager"
        assert reply.message_name == "device.power.on.response"
        assert reply.payload == {"status": "powered_on"}

        # Validates against M00.2
        validate_envelope(reply)
        validate_uuid_v4(reply.message_id, "reply.message_id")

        # Input envelope never mutated
        assert cmd.to_dict() == cmd_dict_before

    def test_derive_reply_custom_name_and_type(self) -> None:
        qry = create_query(
            message_name="system.health.get",
            source="monitor_svc",
        )
        reply = CausalTracer.derive_reply(
            request=qry,
            responder_source="health_svc",
            payload={"healthy": True},
            message_name="system.health.report",
            message_type=MessageType.RESPONSE,
        )
        assert reply.message_name == "system.health.report"
        assert reply.message_type == MessageType.RESPONSE
        assert reply.correlation_id == qry.correlation_id
        assert reply.causation_id == qry.message_id
        assert reply.target == qry.source
        validate_envelope(reply)

    def test_derive_event_root(self) -> None:
        """Root event without cause generates fresh correlation_id and causation_id None."""
        event = CausalTracer.derive_event(
            source="sensor_svc",
            message_name="sensor.temperature.read",
            payload={"temp_c": 36.6},
        )
        assert event.message_type == MessageType.EVENT
        assert event.causation_id is None
        assert event.correlation_id == event.message_id
        assert event.target is None
        assert event.source == "sensor_svc"
        validate_envelope(event)

    def test_derive_event_with_cause(self) -> None:
        """Derived event inherits correlation_id and sets causation_id = cause.message_id."""
        cmd = create_command("device.calibrate", "admin_svc")
        cmd_dict_before = cmd.to_dict()

        event = CausalTracer.derive_event(
            source="device_svc",
            message_name="device.calibrated",
            cause=cmd,
            payload={"offset": 0.05},
        )

        assert event.message_type == MessageType.EVENT
        assert event.correlation_id == cmd.correlation_id
        assert event.causation_id == cmd.message_id
        assert event.source == "device_svc"
        assert event.payload == {"offset": 0.05}
        validate_envelope(event)

        # Input cause untouched
        assert cmd.to_dict() == cmd_dict_before

    def test_derive_event_deterministic_now_utc(self) -> None:
        dt = datetime(2026, 8, 31, 12, 0, 0, 123456, tzinfo=timezone.utc)
        event = CausalTracer.derive_event(
            source="svc_a",
            message_name="svc.heartbeat",
            now_utc=dt,
        )
        assert event.timestamp_utc == "2026-08-31T12:00:00.123456Z"
        validate_envelope(event)
