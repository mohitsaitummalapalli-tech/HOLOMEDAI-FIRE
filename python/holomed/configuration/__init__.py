"""Configuration management subsystem for HoloMed AI."""

from holomed.configuration.exceptions import (
    ConfigurationError,
    ConfigurationMissingError,
    ConfigurationSecretError,
    ConfigurationTypeError,
    ConfigurationValueError,
)
from holomed.configuration.loader import _UNSET, load_config
from holomed.configuration.models import AppConfig, EnvironmentProfile, LogLevel, SecretString
from holomed.configuration.validation import (
    validate_app_name,
    validate_environment,
    validate_gemini_api_key,
    validate_host,
    validate_log_level,
    validate_port,
    validate_protocol_version,
)

__all__ = [
    "_UNSET",
    "AppConfig",
    "ConfigurationError",
    "ConfigurationMissingError",
    "ConfigurationSecretError",
    "ConfigurationTypeError",
    "ConfigurationValueError",
    "EnvironmentProfile",
    "LogLevel",
    "SecretString",
    "load_config",
    "validate_app_name",
    "validate_environment",
    "validate_gemini_api_key",
    "validate_host",
    "validate_log_level",
    "validate_port",
    "validate_protocol_version",
]
