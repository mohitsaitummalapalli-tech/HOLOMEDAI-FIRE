"""Unit tests for strict semantic validators."""

import pytest

from holomed.configuration.exceptions import ConfigurationTypeError, ConfigurationValueError
from holomed.configuration.models import EnvironmentProfile, LogLevel, SecretString
from holomed.configuration.validation import (
    validate_app_name,
    validate_environment,
    validate_gemini_api_key,
    validate_host,
    validate_log_level,
    validate_port,
    validate_protocol_version,
)


def test_strict_port_and_host_validation() -> None:
    """Assert valid and invalid port numbers and hostnames/IPs."""
    # Ports
    assert validate_port(1) == 1
    assert validate_port(8000) == 8000
    assert validate_port(65535) == 65535

    with pytest.raises(ConfigurationValueError):
        validate_port(0)
    with pytest.raises(ConfigurationValueError):
        validate_port(-1)
    with pytest.raises(ConfigurationValueError):
        validate_port(65536)

    with pytest.raises(ConfigurationTypeError):
        validate_port(True)  # bool is subclass of int in Python
    with pytest.raises(ConfigurationTypeError):
        validate_port("8000")  # type: ignore
    with pytest.raises(ConfigurationTypeError):
        validate_port(8000.0)  # type: ignore

    # Hosts
    assert validate_host("127.0.0.1") == "127.0.0.1"
    assert validate_host("::1") == "::1"
    assert validate_host("0.0.0.0") == "0.0.0.0"
    assert validate_host("localhost") == "localhost"
    assert validate_host("api.holomed.internal") == "api.holomed.internal"
    assert validate_host("med-node-1") == "med-node-1"

    with pytest.raises(ConfigurationValueError):
        validate_host("http://localhost")
    with pytest.raises(ConfigurationValueError):
        validate_host("invalid host with spaces")
    with pytest.raises(ConfigurationValueError):
        validate_host("")
    with pytest.raises(ConfigurationValueError):
        validate_host("-invalid-prefix")

    with pytest.raises(ConfigurationTypeError):
        validate_host(127001)  # type: ignore


def test_validate_app_name() -> None:
    """Assert application name validation."""
    assert validate_app_name("HoloMed AI") == "HoloMed AI"
    assert validate_app_name("HoloMed-AI_2026") == "HoloMed-AI_2026"

    with pytest.raises(ConfigurationValueError):
        validate_app_name("")
    with pytest.raises(ConfigurationValueError):
        validate_app_name("A" * 129)
    with pytest.raises(ConfigurationValueError):
        validate_app_name("App@Invalid")

    with pytest.raises(ConfigurationTypeError):
        validate_app_name(123)  # type: ignore


def test_validate_environment() -> None:
    """Assert environment profile validation and normalization."""
    assert validate_environment(EnvironmentProfile.DEVELOPMENT) == EnvironmentProfile.DEVELOPMENT
    assert validate_environment("development") == EnvironmentProfile.DEVELOPMENT
    assert validate_environment("PRODUCTION") == EnvironmentProfile.PRODUCTION
    assert validate_environment("Staging") == EnvironmentProfile.STAGING
    assert validate_environment("testing") == EnvironmentProfile.TESTING

    with pytest.raises(ConfigurationValueError):
        validate_environment("UNKNOWN_PROFILE")

    with pytest.raises(ConfigurationTypeError):
        validate_environment(123)  # type: ignore


def test_validate_log_level() -> None:
    """Assert log level validation and normalization."""
    assert validate_log_level(LogLevel.INFO) == LogLevel.INFO
    assert validate_log_level("debug") == LogLevel.DEBUG
    assert validate_log_level("CRITICAL") == LogLevel.CRITICAL
    assert validate_log_level("warning") == LogLevel.WARNING

    with pytest.raises(ConfigurationValueError):
        validate_log_level("VERBOSE")

    with pytest.raises(ConfigurationTypeError):
        validate_log_level(None)  # type: ignore


def test_validate_gemini_api_key() -> None:
    """Assert gemini_api_key validator handles SecretString, str, and None."""
    assert validate_gemini_api_key(None) is None
    assert validate_gemini_api_key(SecretString("abc")) == SecretString("abc")
    assert validate_gemini_api_key("raw-token") == SecretString("raw-token")

    with pytest.raises(ConfigurationTypeError):
        validate_gemini_api_key(12345)  # type: ignore


def test_validate_protocol_version() -> None:
    """Assert protocol version validation requires current supported version."""
    assert validate_protocol_version("1.0") == "1.0"

    with pytest.raises(ConfigurationValueError):
        validate_protocol_version("2.0")

    with pytest.raises(ConfigurationTypeError):
        validate_protocol_version(1.0)  # type: ignore
