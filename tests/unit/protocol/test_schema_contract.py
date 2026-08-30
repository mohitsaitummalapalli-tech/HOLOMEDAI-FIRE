"""Unit tests verifying JSON Schema self-integrity, Python contract consistency, and golden fixtures."""

import json
from pathlib import Path
import pytest

from holomed.protocol.codec import deserialize_envelope, serialize_envelope
from holomed.protocol.models import MessageType
from holomed.protocol.validation import (
    REQUIRED_ENVELOPE_FIELDS,
    REQUIRED_ERROR_FIELDS,
)


def get_repo_root() -> Path:
    """Return repository root path."""
    return Path(__file__).resolve().parents[3]


def test_schema_self_integrity():
    """Verify envelope and error JSON Schemas are valid Draft 2020-12 documents."""
    schema_dir = get_repo_root() / "protocol" / "schemas"

    for schema_name in ["envelope.schema.json", "error.schema.json"]:
        schema_path = schema_dir / schema_name
        assert schema_path.exists(), f"Schema file not found: {schema_path}"

        with open(schema_path, "r", encoding="utf-8") as f:
            data = json.load(f)

        assert data.get("$schema") == "https://json-schema.org/draft/2020-12/schema"
        assert data.get("type") == "object"
        assert data.get("additionalProperties") is False
        assert isinstance(data.get("properties"), dict)
        assert isinstance(data.get("required"), list)


def test_python_and_json_schema_structural_consistency():
    """Verify Python protocol models and JSON Schema match in required fields, enums, property names, nullability, regex patterns, lengths, and conditional structure."""
    from holomed.protocol.validation import (
        ERROR_CODE_PATTERN,
        IDENTIFIER_PATTERN,
        MAX_ERROR_CODE_LENGTH,
        MAX_ERROR_MESSAGE_LENGTH,
        MAX_IDENTIFIER_LENGTH,
        TIMESTAMP_UTC_PATTERN,
        UUID_PATTERN,
        UUID_V4_PATTERN,
        VERSION_PATTERN,
    )

    envelope_schema_path = get_repo_root() / "protocol" / "schemas" / "envelope.schema.json"
    with open(envelope_schema_path, "r", encoding="utf-8") as f:
        schema = json.load(f)

    # 1. Properties & Required fields match
    schema_props = set(schema["properties"].keys())
    assert schema_props == REQUIRED_ENVELOPE_FIELDS

    schema_required = set(schema["required"])
    assert schema_required == REQUIRED_ENVELOPE_FIELDS

    # 2. Enum values match
    schema_enums = set(schema["properties"]["message_type"]["enum"])
    python_enums = {t.value for t in MessageType}
    assert schema_enums == python_enums

    # 3. Nullability checks
    props = schema["properties"]
    assert props["causation_id"]["type"] == ["string", "null"]
    assert props["target"]["type"] == ["string", "null"]
    assert props["message_id"]["type"] == "string"
    assert props["correlation_id"]["type"] == "string"
    assert props["protocol_version"]["type"] == "string"
    assert props["timestamp_utc"]["type"] == "string"
    assert props["payload"]["type"] == "object"
    assert props["metadata"]["type"] == "object"

    # 4. Regex Pattern & Length Consistency
    assert props["protocol_version"]["pattern"] == VERSION_PATTERN.pattern
    assert props["message_id"]["pattern"] == UUID_V4_PATTERN.pattern
    assert props["correlation_id"]["pattern"] == UUID_PATTERN.pattern
    assert props["causation_id"]["pattern"] == UUID_PATTERN.pattern
    assert props["message_name"]["pattern"] == IDENTIFIER_PATTERN.pattern
    assert props["message_name"]["maxLength"] == MAX_IDENTIFIER_LENGTH
    assert props["source"]["pattern"] == IDENTIFIER_PATTERN.pattern
    assert props["source"]["maxLength"] == MAX_IDENTIFIER_LENGTH
    assert props["target"]["pattern"] == IDENTIFIER_PATTERN.pattern
    assert props["target"]["maxLength"] == MAX_IDENTIFIER_LENGTH
    assert props["timestamp_utc"]["pattern"] == TIMESTAMP_UTC_PATTERN.pattern

    # 5. Strict additionalProperties
    assert schema["additionalProperties"] is False

    # 6. ERROR conditional branch referencing error.schema.json
    assert schema.get("if") == {"properties": {"message_type": {"const": "ERROR"}}}
    assert schema.get("then") == {"properties": {"payload": {"$ref": "error.schema.json"}}}

    # 7. Error Schema Consistency
    error_schema_path = get_repo_root() / "protocol" / "schemas" / "error.schema.json"
    with open(error_schema_path, "r", encoding="utf-8") as f:
        error_schema = json.load(f)

    assert set(error_schema["required"]) == REQUIRED_ERROR_FIELDS
    assert set(error_schema["properties"].keys()) == REQUIRED_ERROR_FIELDS
    assert error_schema["additionalProperties"] is False

    err_props = error_schema["properties"]
    assert err_props["error_code"]["pattern"] == ERROR_CODE_PATTERN.pattern
    assert err_props["error_code"]["maxLength"] == MAX_ERROR_CODE_LENGTH
    assert err_props["error_message"]["maxLength"] == MAX_ERROR_MESSAGE_LENGTH
    assert err_props["error_message"]["minLength"] == 1
    assert err_props["details"]["type"] == "object"
    assert err_props["recoverable"]["type"] == "boolean"


@pytest.mark.parametrize(
    "fixture_filename",
    [
        "envelope_command.json",
        "envelope_query.json",
        "envelope_event.json",
        "envelope_response.json",
        "envelope_error.json",
    ],
)
def test_golden_fixtures_fidelity(fixture_filename: str):
    """Verify all golden fixtures deserialize cleanly and re-serialize with exact canonical equality."""
    fixture_path = get_repo_root() / "protocol" / "examples" / fixture_filename
    assert fixture_path.exists(), f"Golden fixture not found: {fixture_path}"

    with open(fixture_path, "r", encoding="utf-8") as f:
        raw_text = f.read()

    # Normalize fixture whitespace to compact canonical form for exact string comparison
    parsed_json = json.loads(raw_text)
    canonical_json_str = json.dumps(parsed_json, sort_keys=True, separators=(",", ":"), ensure_ascii=False)

    envelope = deserialize_envelope(raw_text)
    reserialized_str = serialize_envelope(envelope)

    assert reserialized_str == canonical_json_str
