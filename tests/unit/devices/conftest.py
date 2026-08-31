"""Test helpers and fixtures for M00.5 device tests."""

from holomed.configuration.models import AppConfig, EnvironmentProfile, LogLevel, SecretString
from holomed.runtime.context import RuntimeContext


def make_test_context(epoch_id: int = 1) -> RuntimeContext:
    config = AppConfig(
        app_name="HoloMed AI",
        environment=EnvironmentProfile.TESTING,
        host="127.0.0.1",
        port=8000,
        log_level=LogLevel.DEBUG,
        gemini_api_key=SecretString("test_secret_key_12345"),
    )
    return RuntimeContext(app_config=config, epoch_id=epoch_id)
