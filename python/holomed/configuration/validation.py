"""Strict semantic validators for configuration parameters."""

import ipaddress
import re
from typing import Any, Optional

from holomed.configuration.exceptions import ConfigurationTypeError, ConfigurationValueError
from holomed.configuration.models import EnvironmentProfile, LogLevel, SecretString
from holomed.protocol.models import CURRENT_PROTOCOL_VERSION

_APP_NAME_REGEX = re.compile(r"^[a-zA-Z0-9_ -]+$")
_HOSTNAME_REGEX = re.compile(r"^(?!-)[a-zA-Z0-9-]{1,63}(?<!-)(\.(?!-)[a-zA-Z0-9-]{1,63}(?<!-))*$")


def validate_app_name(value: Any) -> str:
    """Validate application name length and character set."""
    if not isinstance(value, str):
        raise ConfigurationTypeError(f"app_name must be a string, got {type(value).__name__}")
    if not (1 <= len(value) <= 128):
        raise ConfigurationValueError(f"app_name length must be between 1 and 128 characters, got {len(value)}")
    if not _APP_NAME_REGEX.match(value):
        raise ConfigurationValueError(f"app_name contains invalid characters: '{value}'")
    return value


def validate_environment(value: Any) -> EnvironmentProfile:
    """Validate and normalize environment deployment profile."""
    if isinstance(value, EnvironmentProfile):
        return value
    if isinstance(value, str):
        norm = value.strip().upper()
        try:
            return EnvironmentProfile[norm]
        except KeyError:
            valid = [e.name for e in EnvironmentProfile]
            raise ConfigurationValueError(f"Invalid environment profile '{value}'. Valid profiles: {valid}")
    raise ConfigurationTypeError(f"environment must be EnvironmentProfile or str, got {type(value).__name__}")


def validate_host(value: Any) -> str:
    """Validate semantic IPv4/IPv6 address or RFC 1123 hostname."""
    if not isinstance(value, str):
        raise ConfigurationTypeError(f"host must be a string, got {type(value).__name__}")
    value = value.strip()
    if not value or len(value) > 253:
        raise ConfigurationValueError(f"Invalid host value: '{value}'")

    # Try IP address parsing first
    try:
        ipaddress.ip_address(value)
        return value
    except ValueError:
        pass

    # Validate RFC 1123 hostname
    if _HOSTNAME_REGEX.match(value):
        return value

    raise ConfigurationValueError(f"Invalid host (must be valid IP address or RFC 1123 hostname): '{value}'")


def validate_port(value: Any) -> int:
    """Validate port number in range 1..65535."""
    if isinstance(value, bool) or not isinstance(value, int):
        raise ConfigurationTypeError(f"port must be an integer, got {type(value).__name__}")
    if not (1 <= value <= 65535):
        raise ConfigurationValueError(f"port must be between 1 and 65535, got {value}")
    return value


def validate_log_level(value: Any) -> LogLevel:
    """Validate and normalize log level."""
    if isinstance(value, LogLevel):
        return value
    if isinstance(value, str):
        norm = value.strip().upper()
        try:
            return LogLevel[norm]
        except KeyError:
            valid = [level.name for level in LogLevel]
            raise ConfigurationValueError(f"Invalid log level '{value}'. Valid levels: {valid}")
    raise ConfigurationTypeError(f"log_level must be LogLevel or str, got {type(value).__name__}")


def validate_gemini_api_key(value: Any) -> Optional[SecretString]:
    """Validate and wrap Gemini API key in SecretString."""
    if value is None:
        return None
    if isinstance(value, SecretString):
        return value
    if isinstance(value, str):
        return SecretString(value)
    raise ConfigurationTypeError(f"gemini_api_key must be SecretString, str, or None, got {type(value).__name__}")


def validate_protocol_version(value: Any) -> str:
    """Validate protocol version matches CURRENT_PROTOCOL_VERSION."""
    if not isinstance(value, str):
        raise ConfigurationTypeError(f"protocol_version must be a string, got {type(value).__name__}")
    if value != CURRENT_PROTOCOL_VERSION:
        raise ConfigurationValueError(
            f"Unsupported protocol_version '{value}'. Supported version: '{CURRENT_PROTOCOL_VERSION}'"
        )
    return value
