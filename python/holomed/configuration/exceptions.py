"""Configuration subsystem exceptions."""

from holomed.common.exceptions import HoloMedError


class ConfigurationError(HoloMedError):
    """Root exception for configuration-related failures."""

    pass


class ConfigurationMissingError(ConfigurationError):
    """Raised when a mandatory configuration element is missing."""

    pass


class ConfigurationTypeError(ConfigurationError):
    """Raised when a configuration value does not match the expected type."""

    pass


class ConfigurationValueError(ConfigurationError):
    """Raised when a configuration value fails semantic domain validation."""

    pass


class ConfigurationSecretError(ConfigurationError):
    """Raised when secret string operations or redaction fails."""

    pass
