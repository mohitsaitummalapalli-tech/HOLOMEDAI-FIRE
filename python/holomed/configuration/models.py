"""Strongly typed immutable configuration models and SecretString protection."""

from dataclasses import dataclass
import enum
from typing import Any, Optional

from holomed.configuration.exceptions import ConfigurationTypeError


class EnvironmentProfile(enum.Enum):
    """Runtime environment deployment profiles."""

    DEVELOPMENT = "DEVELOPMENT"
    STAGING = "STAGING"
    PRODUCTION = "PRODUCTION"
    TESTING = "TESTING"


class LogLevel(enum.Enum):
    """Standardized logging severity levels."""

    DEBUG = "DEBUG"
    INFO = "INFO"
    WARNING = "WARNING"
    ERROR = "ERROR"
    CRITICAL = "CRITICAL"


class SecretString:
    """Genuinely immutable secret wrapper preventing accidental credential disclosure."""

    __slots__ = ("_secret_value",)

    def __init__(self, value: str) -> None:
        if not isinstance(value, str):
            raise ConfigurationTypeError(f"SecretString requires str, got {type(value).__name__}")
        super().__setattr__("_secret_value", value)

    def __setattr__(self, name: str, value: Any) -> None:
        raise AttributeError("SecretString instances are immutable")

    def __delattr__(self, name: str) -> None:
        raise AttributeError("SecretString instances are immutable")

    def get_secret_value(self) -> str:
        """Explicit application-level raw-value accessor."""
        return self._secret_value

    def __repr__(self) -> str:
        return "<redacted>"

    def __str__(self) -> str:
        return "<redacted>"

    def __format__(self, format_spec: str) -> str:
        return "<redacted>"

    def __eq__(self, other: object) -> bool:
        if isinstance(other, SecretString):
            return self._secret_value == other._secret_value
        return False

    def __hash__(self) -> int:
        return hash(self._secret_value)


@dataclass(frozen=True)
class AppConfig:
    """Immutable application configuration descriptor."""

    app_name: str
    environment: EnvironmentProfile
    host: str
    port: int
    log_level: LogLevel
    gemini_api_key: Optional[SecretString] = None
    protocol_version: str = "1.0"
