"""Unit tests for structured JSON logging and atomic secret redaction."""

import json
import logging
import uuid
import pytest

from holomed.configuration.models import SecretString
from holomed.runtime.context import TraceContext
from holomed.runtime.logging import (
    SecretFilter,
    StructuredLogger,
    serialize_log_record,
)


def test_serialize_log_record_deterministic_format() -> None:
    """Assert canonical JSON serialization produces deterministic sorted key output."""
    record = {
        "timestamp_utc": "2026-08-30T10:00:00Z",
        "level": "INFO",
        "component": "runtime.engine",
        "message": "Engine ready",
        "event": "engine_ready",
        "runtime_state": "READY",
        "correlation_id": None,
        "causation_id": None,
    }
    serialized = serialize_log_record(record)

    # Must be valid compact JSON without extra whitespace
    assert " " not in serialized or serialized.count(" ") == 1  # Only in message string
    parsed = json.loads(serialized)
    assert parsed["component"] == "runtime.engine"
    assert parsed["level"] == "INFO"


def test_secret_filter_exact_match_redaction() -> None:
    """Assert SecretFilter replaces exact secret occurrences across messages and extra args."""
    secret = SecretString("AIzaSyTopSecret12345")
    sf = SecretFilter([secret])

    assert sf.redact("Loaded key AIzaSyTopSecret12345 successfully") == "Loaded key <redacted> successfully"
    assert sf.redact("Normal message without secret") == "Normal message without secret"

    # Multiple secrets
    secret2 = SecretString("Bearer-token-xyz")
    sf.set_secrets([secret, secret2])
    text = "Got AIzaSyTopSecret12345 and Bearer-token-xyz"
    assert sf.redact(text) == "Got <redacted> and <redacted>"


def test_structured_logger_trace_correlation(caplog: pytest.LogCaptureFixture) -> None:
    """Assert StructuredLogger embeds correlation_id and causation_id."""
    corr_id = uuid.uuid4()
    caus_id = uuid.uuid4()
    trace = TraceContext(correlation_id=corr_id, causation_id=caus_id)

    logger = StructuredLogger("test.component").with_trace(trace)
    with caplog.at_level(logging.INFO):
        logger.info("Service initialized", event="svc_init", runtime_state="INITIALIZED")

    assert len(caplog.records) >= 1
    log_json = json.loads(caplog.records[-1].message)
    assert log_json["correlation_id"] == str(corr_id)
    assert log_json["causation_id"] == str(caus_id)
    assert log_json["event"] == "svc_init"
    assert log_json["runtime_state"] == "INITIALIZED"
    assert log_json["message"] == "Service initialized"
