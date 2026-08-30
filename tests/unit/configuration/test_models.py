"""Unit tests for configuration data models and SecretString protection."""

from dataclasses import FrozenInstanceError
import pytest

from holomed.configuration.exceptions import ConfigurationTypeError
from holomed.configuration.models import AppConfig, EnvironmentProfile, LogLevel, SecretString


def test_app_config_frozen_immutability() -> None:
    """Assert AppConfig instances are strictly immutable."""
    config = AppConfig(
        app_name="TestApp",
        environment=EnvironmentProfile.DEVELOPMENT,
        host="127.0.0.1",
        port=8000,
        log_level=LogLevel.INFO,
        gemini_api_key=SecretString("secret-123"),
        protocol_version="1.0",
    )
    with pytest.raises((FrozenInstanceError, AttributeError)):
        config.port = 9000  # type: ignore


def test_secret_string_reassignment_raises() -> None:
    """Assert SecretString cannot be mutated or reassigned."""
    secret = SecretString("super-secret-key")
    with pytest.raises(AttributeError, match="immutable"):
        secret._secret_value = "new-key"  # type: ignore

    with pytest.raises(AttributeError, match="immutable"):
        del secret._secret_value  # type: ignore


def test_secret_string_masking() -> None:
    """Assert SecretString masks credentials across all string representations."""
    raw = "AIzaSySecretToken123456"
    secret = SecretString(raw)

    assert str(secret) == "<redacted>"
    assert repr(secret) == "<redacted>"
    assert f"Key: {secret}" == "Key: <redacted>"
    assert format(secret, "") == "<redacted>"
    assert secret.get_secret_value() == raw


def test_secret_string_equality_and_hash() -> None:
    """Assert SecretString equality and hashing semantics."""
    s1 = SecretString("token-abc")
    s2 = SecretString("token-abc")
    s3 = SecretString("token-xyz")

    assert s1 == s2
    assert s1 != s3
    assert s1 != "token-abc"  # Cannot equal raw str
    assert hash(s1) == hash(s2)
    assert len({s1, s2, s3}) == 2


def test_secret_string_invalid_type() -> None:
    """Assert SecretString constructor rejects non-string values."""
    with pytest.raises(ConfigurationTypeError):
        SecretString(12345)  # type: ignore
    with pytest.raises(ConfigurationTypeError):
        SecretString(None)  # type: ignore


def test_environment_profile_enum() -> None:
    """Assert EnvironmentProfile enum has exact expected values."""
    expected = {"DEVELOPMENT", "STAGING", "PRODUCTION", "TESTING"}
    assert {e.name for e in EnvironmentProfile} == expected
    assert {e.value for e in EnvironmentProfile} == expected


def test_log_level_enum() -> None:
    """Assert LogLevel enum has exact expected values."""
    expected = {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}
    assert {l.name for l in LogLevel} == expected
    assert {l.value for l in LogLevel} == expected
