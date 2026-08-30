"""Unit tests for configuration loader and sentinel precedence."""

import pytest

from holomed.configuration.exceptions import ConfigurationTypeError, ConfigurationValueError
from holomed.configuration.loader import load_config
from holomed.configuration.models import EnvironmentProfile, LogLevel, SecretString


def test_sentinel_loader_defaults() -> None:
    """Assert load_config() returns documented defaults when no arguments or env vars are given."""
    config = load_config(env={})

    assert config.app_name == "HoloMed AI"
    assert config.environment == EnvironmentProfile.DEVELOPMENT
    assert config.host == "127.0.0.1"
    assert config.port == 8000
    assert config.log_level == LogLevel.INFO
    assert config.gemini_api_key is None
    assert config.protocol_version == "1.0"


def test_sentinel_loader_env_precedence() -> None:
    """Assert environment variables override defaults."""
    env = {
        "HOLOMED_APP_NAME": "Custom Medical App",
        "HOLOMED_ENV": "STAGING",
        "HOLOMED_HOST": "192.168.1.100",
        "HOLOMED_PORT": "9090",
        "HOLOMED_LOG_LEVEL": "DEBUG",
        "GEMINI_API_KEY": "env-secret-token",
        "HOLOMED_PROTOCOL_VERSION": "1.0",
    }
    config = load_config(env=env)

    assert config.app_name == "Custom Medical App"
    assert config.environment == EnvironmentProfile.STAGING
    assert config.host == "192.168.1.100"
    assert config.port == 9090
    assert config.log_level == LogLevel.DEBUG
    assert config.gemini_api_key == SecretString("env-secret-token")
    assert config.protocol_version == "1.0"


def test_sentinel_loader_kwarg_precedence() -> None:
    """Assert explicit kwargs override both environment variables and defaults."""
    env = {
        "HOLOMED_APP_NAME": "EnvApp",
        "HOLOMED_PORT": "9000",
        "GEMINI_API_KEY": "env-key",
    }
    config = load_config(
        app_name="KwargApp",
        port=8080,
        gemini_api_key="kwarg-key",
        env=env,
    )

    assert config.app_name == "KwargApp"
    assert config.port == 8080
    assert config.gemini_api_key == SecretString("kwarg-key")


def test_sentinel_loader_explicit_none_on_non_optional() -> None:
    """Assert explicit None on non-optional fields raises ConfigurationTypeError."""
    with pytest.raises(ConfigurationTypeError):
        load_config(app_name=None, env={})

    with pytest.raises(ConfigurationTypeError):
        load_config(environment=None, env={})

    with pytest.raises(ConfigurationTypeError):
        load_config(host=None, env={})

    with pytest.raises(ConfigurationTypeError):
        load_config(port=None, env={})

    with pytest.raises(ConfigurationTypeError):
        load_config(log_level=None, env={})

    with pytest.raises(ConfigurationTypeError):
        load_config(protocol_version=None, env={})


def test_sentinel_loader_explicit_none_on_gemini_key() -> None:
    """Assert explicit None on gemini_api_key explicitly disables the key even if env is set."""
    env = {"GEMINI_API_KEY": "env-key-present"}
    config = load_config(gemini_api_key=None, env=env)
    assert config.gemini_api_key is None


def test_sentinel_loader_invalid_port_in_env() -> None:
    """Assert invalid port integer string in env raises ConfigurationValueError."""
    env = {"HOLOMED_PORT": "invalid-port-string"}
    with pytest.raises(ConfigurationValueError):
        load_config(env=env)
