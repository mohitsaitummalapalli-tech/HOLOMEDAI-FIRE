"""Unit tests for HoloMed protocol builder factory functions and causal invariants."""

import pytest

from holomed.protocol.builders import (
    create_command,
    create_error_response,
    create_event,
    create_query,
    create_response,
)
from holomed.protocol.exceptions import ProtocolValidationError
from holomed.protocol.models import MessageType


def test_create_command_causal_semantics():
    """Verify root command creates matching correlation_id and null causation_id."""
    cmd = create_command(
        message_name="device.camera.start",
        source="core.orchestrator",
        target="device.camera",
        payload={"fps": 60},
    )

    assert cmd.message_type == MessageType.COMMAND
    assert cmd.message_id == cmd.correlation_id
    assert cmd.causation_id is None
    assert cmd.source == "core.orchestrator"
    assert cmd.target == "device.camera"


def test_create_query_causal_semantics():
    """Verify root query creates matching correlation_id and null causation_id."""
    qry = create_query(
        message_name="device.camera.get_status",
        source="core.orchestrator",
        target="device.camera",
    )

    assert qry.message_type == MessageType.QUERY
    assert qry.message_id == qry.correlation_id
    assert qry.causation_id is None


def test_create_event_root_and_derived_causal_semantics():
    """Verify root vs derived event correlation and causation propagation."""
    # Root event
    root_event = create_event(
        message_name="device.camera.frame_ready",
        source="device.camera",
        payload={"frame_id": 1},
    )
    assert root_event.correlation_id == root_event.message_id
    assert root_event.causation_id is None

    # Derived event
    derived_event = create_event(
        message_name="vision.pipeline.processed",
        source="vision.tracker",
        payload={"features": [1, 2, 3]},
        parent=root_event,
    )
    assert derived_event.correlation_id == root_event.correlation_id
    assert derived_event.causation_id == root_event.message_id
    assert derived_event.message_id != root_event.message_id


def test_create_response_enforces_invariants():
    """Verify response builder strictly binds correlation_id, causation_id, and target to request."""
    cmd = create_command(
        message_name="device.camera.start",
        source="core.orchestrator",
        target="device.camera",
        payload={"fps": 60},
    )

    resp = create_response(
        request=cmd,
        responder_source="device.camera",
        payload={"status": "started"},
    )

    assert resp.message_type == MessageType.RESPONSE
    assert resp.message_name == "device.camera.start.response"
    assert resp.source == "device.camera"
    # Immutable invariants
    assert resp.target == cmd.source  # target must equal request.source
    assert resp.correlation_id == cmd.correlation_id
    assert resp.causation_id == cmd.message_id


def test_create_error_response_enforces_invariants():
    """Verify error response strictly binds invariants and formats ErrorPayload."""
    qry = create_query(
        message_name="device.camera.get_status",
        source="core.orchestrator",
        target="device.camera",
    )

    err_resp = create_error_response(
        request=qry,
        responder_source="device.camera",
        error_code="ERR_CAMERA_OFFLINE",
        error_message="Camera is disconnected.",
        details={"port": "usb_0"},
        recoverable=True,
    )

    assert err_resp.message_type == MessageType.ERROR
    assert err_resp.message_name == "device.camera.get_status.error"
    assert err_resp.target == qry.source
    assert err_resp.correlation_id == qry.correlation_id
    assert err_resp.causation_id == qry.message_id
    assert err_resp.payload == {
        "error_code": "ERR_CAMERA_OFFLINE",
        "error_message": "Camera is disconnected.",
        "details": {"port": "usb_0"},
        "recoverable": True,
    }


def test_builder_rejects_invalid_inputs_at_construction():
    """Verify builder functions reject invalid arguments immediately."""
    with pytest.raises(ProtocolValidationError):
        create_command(
            message_name="INVALID_UPPERCASE_NAME",
            source="core.orchestrator",
        )

    with pytest.raises(ProtocolValidationError):
        create_command(
            message_name="device.camera.start",
            source="Core Orchestrator with spaces",
        )


def test_builder_explicit_dictionary_normalization_and_rejection():
    """Verify None produces empty dictionary, while non-dict types are strictly rejected without coercion."""
    # None inputs produce empty dicts
    cmd = create_command(
        message_name="device.camera.start",
        source="core.orchestrator",
        payload=None,
        metadata=None,
    )
    assert cmd.payload == {}
    assert cmd.metadata == {}

    # Non-dictionary payload rejected
    with pytest.raises(ProtocolValidationError) as exc:
        create_command(
            message_name="device.camera.start",
            source="core.orchestrator",
            payload="not_a_dict",  # type: ignore
        )
    assert "payload must be a dictionary or None, got str" in str(exc.value)

    with pytest.raises(ProtocolValidationError) as exc:
        create_command(
            message_name="device.camera.start",
            source="core.orchestrator",
            payload=[1, 2, 3],  # type: ignore
        )
    assert "payload must be a dictionary or None, got list" in str(exc.value)

    # Non-dictionary metadata rejected
    with pytest.raises(ProtocolValidationError) as exc:
        create_command(
            message_name="device.camera.start",
            source="core.orchestrator",
            metadata=12345,  # type: ignore
        )
    assert "metadata must be a dictionary or None, got int" in str(exc.value)

    # Non-dictionary details rejected in error response
    with pytest.raises(ProtocolValidationError) as exc:
        create_error_response(
            request=cmd,
            responder_source="device.camera",
            error_code="ERR_FAILED",
            error_message="Failure",
            details="invalid_details",  # type: ignore
        )
    assert "details must be a dictionary or None, got str" in str(exc.value)
