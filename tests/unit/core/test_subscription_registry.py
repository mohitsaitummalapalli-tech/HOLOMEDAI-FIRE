"""Tests for M00.4 Subscription Registry."""

import pytest
from holomed.core.exceptions import (
    DuplicateSubscriptionError,
    SubscriptionError,
    TopicValidationError,
)
from holomed.core.models import CommandRegistration, EventSubscription, QueryRegistration
from holomed.core.subscription import SubscriptionRegistry
from holomed.protocol.models import MessageEnvelope


def dummy_msg_handler(env: MessageEnvelope) -> MessageEnvelope:
    return env


def dummy_event_handler(env: MessageEnvelope) -> None:
    pass


class TestSubscriptionRegistry:
    """Test command, query, and event registrations with deterministic ordering and sealing."""

    def test_command_registration_and_lookup(self) -> None:
        reg = SubscriptionRegistry()
        cmd = reg.register_command("device.power.on", dummy_msg_handler, "power_service")
        assert isinstance(cmd, CommandRegistration)
        assert cmd.topic == "device.power.on"
        assert cmd.service_name == "power_service"
        assert cmd.registration_index == 0

        lookup = reg.lookup_command("device.power.on")
        assert lookup is cmd
        assert reg.lookup_command("unknown.topic") is None

    def test_command_duplicate_registration_rejected(self) -> None:
        reg = SubscriptionRegistry()
        reg.register_command("device.power.on", dummy_msg_handler, "service_a")
        with pytest.raises(DuplicateSubscriptionError) as exc_info:
            reg.register_command("device.power.on", dummy_msg_handler, "service_b")
        assert "service_a" in str(exc_info.value)

    def test_query_registration_and_lookup(self) -> None:
        reg = SubscriptionRegistry()
        q = reg.register_query("device.status.get", dummy_msg_handler, "status_service")
        assert isinstance(q, QueryRegistration)
        assert q.topic == "device.status.get"
        assert q.service_name == "status_service"
        assert q.registration_index == 0

        lookup = reg.lookup_query("device.status.get")
        assert lookup is q
        assert reg.lookup_query("unknown.query") is None

    def test_query_duplicate_registration_rejected(self) -> None:
        reg = SubscriptionRegistry()
        reg.register_query("device.status.get", dummy_msg_handler, "service_a")
        with pytest.raises(DuplicateSubscriptionError):
            reg.register_query("device.status.get", dummy_msg_handler, "service_b")

    def test_event_subscription_and_deterministic_ordering(self) -> None:
        """Subscriber order MUST be (subscriber_service_name ASC, registration_index ASC)."""
        reg = SubscriptionRegistry()
        # Register out of alphabetical order
        sub_z1 = reg.subscribe_event("telemetry.**", dummy_event_handler, "zebra_service")
        sub_a1 = reg.subscribe_event("telemetry.*", dummy_event_handler, "alpha_service")
        sub_m1 = reg.subscribe_event("telemetry.temp", dummy_event_handler, "middle_service")
        sub_a2 = reg.subscribe_event("telemetry.**", dummy_event_handler, "alpha_service")

        matches = reg.matching_event_subscriptions("telemetry.temp")
        assert len(matches) == 4

        # Expected: alpha_service (idx 1), alpha_service (idx 3), middle_service (idx 2), zebra_service (idx 0)
        assert matches[0] is sub_a1
        assert matches[1] is sub_a2
        assert matches[2] is sub_m1
        assert matches[3] is sub_z1

    def test_event_matching_subsets(self) -> None:
        reg = SubscriptionRegistry()
        reg.subscribe_event("camera.front.*", dummy_event_handler, "svc_a")
        reg.subscribe_event("camera.**", dummy_event_handler, "svc_b")
        reg.subscribe_event("audio.**", dummy_event_handler, "svc_c")

        matches_cam = reg.matching_event_subscriptions("camera.front.frame")
        assert len(matches_cam) == 2
        svc_names = [m.service_name for m in matches_cam]
        assert svc_names == ["svc_a", "svc_b"]

        matches_audio = reg.matching_event_subscriptions("audio.mic.stream")
        assert len(matches_audio) == 1
        assert matches_audio[0].service_name == "svc_c"

        matches_none = reg.matching_event_subscriptions("network.packet")
        assert len(matches_none) == 0

    def test_seal_prohibits_further_registrations(self) -> None:
        reg = SubscriptionRegistry()
        reg.register_command("sys.ping", dummy_msg_handler, "ping_svc")
        reg.seal()
        assert reg.is_sealed is True

        with pytest.raises(SubscriptionError):
            reg.register_command("sys.pong", dummy_msg_handler, "pong_svc")

        with pytest.raises(SubscriptionError):
            reg.register_query("sys.info", dummy_msg_handler, "info_svc")

        with pytest.raises(SubscriptionError):
            reg.subscribe_event("sys.**", dummy_event_handler, "event_svc")

    def test_clear_resets_registrations_and_seal(self) -> None:
        reg = SubscriptionRegistry()
        reg.register_command("sys.ping", dummy_msg_handler, "ping_svc")
        reg.register_query("sys.info", dummy_msg_handler, "info_svc")
        reg.subscribe_event("sys.**", dummy_event_handler, "event_svc")
        reg.seal()

        reg.clear()
        assert reg.is_sealed is False
        assert reg.command_count == 0
        assert reg.query_count == 0
        assert reg.event_subscription_count == 0

        # After clear, can register anew starting from index 0
        new_cmd = reg.register_command("sys.ping", dummy_msg_handler, "new_ping_svc")
        assert new_cmd.registration_index == 0
