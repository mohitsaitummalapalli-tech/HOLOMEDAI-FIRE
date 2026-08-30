"""Sentinel-based configuration precedence loader."""

import os
from typing import Any, Mapping, Optional

from holomed.configuration.exceptions import ConfigurationValueError
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
from holomed.protocol.models import CURRENT_PROTOCOL_VERSION

_UNSET = object()


def load_config(
    app_name: Any = _UNSET,
    environment: Any = _UNSET,
    host: Any = _UNSET,
    port: Any = _UNSET,
    log_level: Any = _UNSET,
    gemini_api_key: Any = _UNSET,
    protocol_version: Any = _UNSET,
    env: Optional[Mapping[str, str]] = None,
) -> AppConfig:
    """Load application configuration resolving precedence: kwargs > env > os.environ > defaults."""
    env_source = os.environ if env is None else env

    # 1. app_name
    if app_name is not _UNSET:
        resolved_app_name = validate_app_name(app_name)
    elif "HOLOMED_APP_NAME" in env_source:
        resolved_app_name = validate_app_name(env_source["HOLOMED_APP_NAME"])
    else:
        resolved_app_name = "HoloMed AI"

    # 2. environment
    if environment is not _UNSET:
        resolved_environment = validate_environment(environment)
    elif "HOLOMED_ENV" in env_source:
        resolved_environment = validate_environment(env_source["HOLOMED_ENV"])
    else:
        resolved_environment = EnvironmentProfile.DEVELOPMENT

    # 3. host
    if host is not _UNSET:
        resolved_host = validate_host(host)
    elif "HOLOMED_HOST" in env_source:
        resolved_host = validate_host(env_source["HOLOMED_HOST"])
    else:
        resolved_host = "127.0.0.1"

    # 4. port
    if port is not _UNSET:
        resolved_port = validate_port(port)
    elif "HOLOMED_PORT" in env_source:
        port_raw = env_source["HOLOMED_PORT"]
        try:
            port_val = int(port_raw)
        except (ValueError, TypeError):
            raise ConfigurationValueError(f"Invalid integer value for HOLOMED_PORT: '{port_raw}'")
        resolved_port = validate_port(port_val)
    else:
        resolved_port = 8000

    # 5. log_level
    if log_level is not _UNSET:
        resolved_log_level = validate_log_level(log_level)
    elif "HOLOMED_LOG_LEVEL" in env_source:
        resolved_log_level = validate_log_level(env_source["HOLOMED_LOG_LEVEL"])
    else:
        resolved_log_level = LogLevel.INFO

    # 6. gemini_api_key
    if gemini_api_key is not _UNSET:
        resolved_gemini_key = validate_gemini_api_key(gemini_api_key)
    elif "GEMINI_API_KEY" in env_source and env_source["GEMINI_API_KEY"]:
        resolved_gemini_key = SecretString(env_source["GEMINI_API_KEY"])
    else:
        resolved_gemini_key = None

    # 7. protocol_version
    if protocol_version is not _UNSET:
        resolved_protocol_version = validate_protocol_version(protocol_version)
    elif "HOLOMED_PROTOCOL_VERSION" in env_source:
        resolved_protocol_version = validate_protocol_version(env_source["HOLOMED_PROTOCOL_VERSION"])
    else:
        resolved_protocol_version = CURRENT_PROTOCOL_VERSION

    return AppConfig(
        app_name=resolved_app_name,
        environment=resolved_environment,
        host=resolved_host,
        port=resolved_port,
        log_level=resolved_log_level,
        gemini_api_key=resolved_gemini_key,
        protocol_version=resolved_protocol_version,
    )
