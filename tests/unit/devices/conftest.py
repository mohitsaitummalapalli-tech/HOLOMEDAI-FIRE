"""Test helpers and fixtures for M00.5 device tests."""

import pytest
from holomed.configuration.models import AppConfig, EnvironmentProfile, LogLevel, SecretString
from holomed.runtime.context import RuntimeContext


@pytest.fixture(autouse=True)
def reset_admission_verifier():
    """Ensure authoritative verifier is reset between tests."""
    from holomed.devices.control import admission
    # Reset both styles if we are testing different versions or during transitions
    if hasattr(admission, "_verifier_ref"):
        admission._verifier_ref = None
    if hasattr(admission, "_verifier"):
        setattr(admission, "_verifier", None)
    yield
    if hasattr(admission, "_verifier_ref"):
        admission._verifier_ref = None
    if hasattr(admission, "_verifier"):
        setattr(admission, "_verifier", None)


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
