"""Tests for M00.4 Topic Grammar and Deterministic Segment Matcher."""

import pytest
from holomed.core.exceptions import TopicValidationError
from holomed.core.subscription import (
    topic_matches,
    validate_concrete_topic,
    validate_topic_pattern,
)


class TestConcreteTopicValidation:
    """Validate strict concrete topic grammar."""

    @pytest.mark.parametrize(
        "topic",
        [
            "telemetry",
            "telemetry.sensors",
            "device.camera.fps",
            "system.subsystem.component.metric",
            "a.b.c.d.e",
            "123.456.789",
            "alpha1.beta2.gamma3",
        ],
    )
    def test_valid_concrete_topics(self, topic: str) -> None:
        assert validate_concrete_topic(topic) == topic

    @pytest.mark.parametrize(
        "invalid_topic,reason",
        [
            ("", "empty topic"),
            ("   ", "whitespace only"),
            ("telemetry. sensors", "whitespace inside"),
            (".telemetry", "leading dot"),
            ("telemetry.", "trailing dot"),
            ("telemetry..sensors", "consecutive dots / empty segment"),
            ("Telemetry", "uppercase"),
            ("telemetry.Sensors", "uppercase segment"),
            ("telemetry.*", "wildcard * in concrete topic"),
            ("telemetry.**", "wildcard ** in concrete topic"),
            ("telemetry$foo", "illegal characters"),
            ("telemetry_foo", "underscore in concrete topic (only a-z0-9 allowed)"),
            ("telemetry-foo", "hyphen in concrete topic"),
            ("a" * 65 + ".b", "segment > 64 chars"),
            ("a" * 129, "total length > 128 chars"),
            (123, "non-string type"),
            (None, "none type"),
        ],
    )
    def test_invalid_concrete_topics_rejected(self, invalid_topic: object, reason: str) -> None:
        with pytest.raises(TopicValidationError):
            validate_concrete_topic(invalid_topic)  # type: ignore[arg-type]


class TestTopicPatternValidationAndNormalization:
    """Validate raw pattern checks and consecutive ** collapse."""

    @pytest.mark.parametrize(
        "raw_pattern,expected_normalized",
        [
            ("**", "**"),
            ("**.**", "**"),
            ("**.**.**", "**"),
            ("a.**.b", "a.**.b"),
            ("a.**.**.b", "a.**.b"),
            ("a.**.**.**.b", "a.**.b"),
            ("**.a.**.b.**", "**.a.**.b.**"),
            ("a.*.b", "a.*.b"),
            ("*.a.*", "*.a.*"),
            ("*.**.b", "*.**.b"),
            ("a.**.b.*.c", "a.**.b.*.c"),
            ("telemetry.*.sensors", "telemetry.*.sensors"),
        ],
    )
    def test_pattern_normalization_consecutive_glob(self, raw_pattern: str, expected_normalized: str) -> None:
        assert validate_topic_pattern(raw_pattern) == expected_normalized

    @pytest.mark.parametrize(
        "invalid_pattern,reason",
        [
            ("", "empty pattern"),
            ("   ", "whitespace only"),
            (".a.*", "leading dot"),
            ("a.*.", "trailing dot"),
            ("a..*", "consecutive dots"),
            ("A.*", "uppercase characters"),
            ("a.b.C", "uppercase characters in segment"),
            ("a.***.b", "illegal wildcard ***"),
            ("a.*foo.b", "illegal mixed wildcard *foo"),
            ("a.foo*.b", "illegal mixed wildcard foo*"),
            ("a.?*.b", "illegal characters ?"),
            ("a" * 65 + ".*", "segment > 64 characters"),
            ("a" * 120 + ".**.**", "raw pattern > 128 characters"),
            (None, "None type"),
            (42, "integer type"),
        ],
    )
    def test_invalid_patterns_rejected(self, invalid_pattern: object, reason: str) -> None:
        with pytest.raises(TopicValidationError):
            validate_topic_pattern(invalid_pattern)  # type: ignore[arg-type]


class TestTopicMatcherSemantics:
    """Test deterministic segment-by-segment matching rules."""

    def test_exact_concrete_matching(self) -> None:
        assert topic_matches("device.camera", "device.camera") is True
        assert topic_matches("device.camera", "device.audio") is False
        assert topic_matches("device.camera", "device.camera.front") is False
        assert topic_matches("device.camera.front", "device.camera") is False

    def test_single_segment_wildcard(self) -> None:
        assert topic_matches("device.camera", "device.*") is True
        assert topic_matches("device.audio", "device.*") is True
        assert topic_matches("device.camera.fps", "device.*") is False
        assert topic_matches("device", "device.*") is False
        assert topic_matches("sensors.temp.celsius", "*.temp.*") is True
        assert topic_matches("sensors.pressure.pascal", "*.temp.*") is False

    def test_standalone_multi_segment_wildcard(self) -> None:
        assert topic_matches("a", "**") is True
        assert topic_matches("a.b", "**") is True
        assert topic_matches("a.b.c.d.e", "**") is True

    def test_prefix_multi_segment_wildcard(self) -> None:
        assert topic_matches("telemetry", "**.telemetry") is True
        assert topic_matches("device.telemetry", "**.telemetry") is True
        assert topic_matches("lab.room1.device.telemetry", "**.telemetry") is True
        assert topic_matches("device.telemetry.other", "**.telemetry") is False

    def test_postfix_multi_segment_wildcard(self) -> None:
        assert topic_matches("device", "device.**") is True
        assert topic_matches("device.camera", "device.**") is True
        assert topic_matches("device.camera.fps", "device.**") is True
        assert topic_matches("system.camera", "device.**") is False

    def test_infix_multi_segment_wildcard(self) -> None:
        assert topic_matches("a.b", "a.**.b") is True
        assert topic_matches("a.mid.b", "a.**.b") is True
        assert topic_matches("a.mid1.mid2.b", "a.**.b") is True
        assert topic_matches("a.mid1.mid2.mid3.b", "a.**.b") is True
        assert topic_matches("a.mid1.mid2.c", "a.**.b") is False

    def test_multiple_non_adjacent_multi_segment_wildcards(self) -> None:
        pat = "a.**.b.**.c"
        assert topic_matches("a.b.c", pat) is True
        assert topic_matches("a.x.b.y.c", pat) is True
        assert topic_matches("a.x1.x2.b.y1.y2.c", pat) is True
        assert topic_matches("a.x.b.y.d", pat) is False
        assert topic_matches("z.a.x.b.y.c", pat) is False

    def test_combined_single_and_multi_wildcards(self) -> None:
        pat = "sys.*.events.**"
        assert topic_matches("sys.auth.events", pat) is True
        assert topic_matches("sys.auth.events.login", pat) is True
        assert topic_matches("sys.auth.events.login.success", pat) is True
        assert topic_matches("sys.events", pat) is False
        assert topic_matches("sys.auth.other.login", pat) is False
