# -*- coding: utf-8 -*-
"""Unit Tests for PersistenceService Lifecycle, Resources, and Dispatcher Routes."""

from __future__ import annotations

from pathlib import Path
import pytest

from holomed.core.dispatcher import MessageDispatcher
from holomed.persistence.exceptions import PersistenceLifecycleError
from holomed.persistence.service import PersistenceService
from holomed.platform.models import CycleStatus, CycleSummary
from holomed.protocol.builders import create_command, create_query
from holomed.runtime.context import RuntimeContext
from holomed.runtime.logging import SecretFilter
from holomed.runtime.models import HealthStatus
from holomed.runtime.service import ServiceState


def test_persistence_service_lifecycle_and_structural_resources(
    temp_storage_root: Path,
    runtime_context: RuntimeContext,
    message_dispatcher: MessageDispatcher,
    secret_filter: SecretFilter,
) -> None:
    """Verify PersistenceService lifecycle and exact acquisition of 4 structural handles."""
    srv = PersistenceService(
        dispatcher=message_dispatcher,
        storage_root=temp_storage_root,
        secret_filter=secret_filter,
    )
    assert srv.state == ServiceState.UNINITIALIZED

    srv.initialize(runtime_context)
    assert srv.state == ServiceState.INITIALIZED
    assert len(srv.resources.outstanding_handles) == 4
    handle_ids = {h.resource_id for h in srv.resources.outstanding_handles}
    assert handle_ids == {
        "persistence.journal",
        "persistence.sessions",
        "persistence.replay",
        "persistence.metrics",
    }

    message_dispatcher.start()
    srv.start()
    assert srv.state == ServiceState.STARTED

    h = srv.health()
    assert h.status == HealthStatus.HEALTHY

    srv.stop()
    assert srv.state == ServiceState.STOPPED
    assert len(srv.resources.outstanding_handles) == 0


def test_reentrancy_guard(
    temp_storage_root: Path,
    runtime_context: RuntimeContext,
    message_dispatcher: MessageDispatcher,
    secret_filter: SecretFilter,
) -> None:
    """Verify reentrant mutation calls are rejected by transaction guard."""
    srv = PersistenceService(
        dispatcher=message_dispatcher,
        storage_root=temp_storage_root,
        secret_filter=secret_filter,
    )
    srv.initialize(runtime_context)
    srv.start()

    srv._in_transaction = True
    with pytest.raises(PersistenceLifecycleError):
        srv.start_session("sess_reentrant")

    srv._in_transaction = False
    srv.stop()


def test_dispatcher_routes(
    temp_storage_root: Path,
    runtime_context: RuntimeContext,
    message_dispatcher: MessageDispatcher,
    secret_filter: SecretFilter,
) -> None:
    """Verify dispatcher query and command handlers route cleanly."""
    srv = PersistenceService(
        dispatcher=message_dispatcher,
        storage_root=temp_storage_root,
        secret_filter=secret_filter,
    )
    srv.initialize(runtime_context)
    message_dispatcher.start()
    srv.start()

    # 1. Query status
    q_status = create_query("persistence.status", "client")
    resp_status = message_dispatcher.dispatch(q_status)
    assert resp_status.message_type.value == "RESPONSE"
    assert resp_status.payload["service_name"] == "persistence_service"

    # Start session directly to populate data
    srv.start_session("sess_disp_01")
    summary = CycleSummary(
        cycle_id="c_disp_01",
        epoch_id=1,
        session_id="sess_disp_01",
        sequence_number=0,
        status=CycleStatus.COMPLETED,
        execution_time_ms=2.0,
        phases_completed=("PERCEPTION", "REASONING"),
        fused_entity_count=1,
        tool_invocations_count=0,
        xr_node_count=0,
        confidence=1.0,
        uncertainty_metric=0.0,
    )
    srv.record_cycle("sess_disp_01", sequence_number=0, summary=summary)

    # 2. Query session
    q_sess = create_query("persistence.session.get", "client", payload={"session_id": "sess_disp_01"})
    resp_sess = message_dispatcher.dispatch(q_sess)
    assert resp_sess.message_type.value == "RESPONSE"
    assert resp_sess.payload["session_id"] == "sess_disp_01"
    assert resp_sess.payload["total_cycles"] == 1

    # 3. Query cycle
    q_cycle = create_query(
        "persistence.cycle.get",
        "client",
        payload={"session_id": "sess_disp_01", "sequence_number": 0},
    )
    resp_cycle = message_dispatcher.dispatch(q_cycle)
    assert resp_cycle.message_type.value == "RESPONSE"
    assert resp_cycle.payload["cycle_id"] == "c_disp_01"

    # 4. Command replay
    cmd_rep = create_command("persistence.replay", "client", payload={"session_id": "sess_disp_01"})
    resp_rep = message_dispatcher.dispatch(cmd_rep)
    assert resp_rep.message_type.value == "RESPONSE"
    assert resp_rep.payload["replay_status"] == "VERIFIED"

    srv.stop()


def test_m32_persistence_path_security(
    temp_storage_root: Path,
    runtime_context: RuntimeContext,
    message_dispatcher: MessageDispatcher,
    secret_filter: SecretFilter,
) -> None:
    """M32: Verify session identifier validation prevents path traversal and malformed access."""
    srv = PersistenceService(
        dispatcher=message_dispatcher,
        storage_root=temp_storage_root,
        secret_filter=secret_filter,
    )
    srv.initialize(runtime_context)
    message_dispatcher.start()
    srv.start()

    # 1. Valid session ID succeeds
    srv.start_session("valid_session_123")
    summary = CycleSummary(
        cycle_id="c_valid_01",
        epoch_id=1,
        session_id="valid_session_123",
        sequence_number=0,
        status=CycleStatus.COMPLETED,
        execution_time_ms=2.0,
        phases_completed=("PERCEPTION", "REASONING"),
        fused_entity_count=1,
        tool_invocations_count=0,
        xr_node_count=0,
        confidence=1.0,
        uncertainty_metric=0.0,
    )
    srv.record_cycle("valid_session_123", sequence_number=0, summary=summary)

    q_valid = create_query(
        "persistence.cycle.get",
        "client",
        payload={"session_id": "valid_session_123", "sequence_number": 0},
    )
    resp_valid = message_dispatcher.dispatch(q_valid)
    assert resp_valid.message_type.value == "RESPONSE"
    assert resp_valid.payload["cycle_id"] == "c_valid_01"

    files_before = set(temp_storage_root.rglob("*"))

    # 2. Traversal attempt fails
    q_traversal = create_query(
        "persistence.cycle.get",
        "client",
        payload={"session_id": "../traversal_sess", "sequence_number": 0},
    )
    resp_traversal = message_dispatcher.dispatch(q_traversal)
    assert resp_traversal.message_type.value == "ERROR"
    assert resp_traversal.payload["error_code"] == "ERR_PERSISTENCE_SECURITY_ERROR"

    # 3. Absolute path fails
    q_abs = create_query(
        "persistence.cycle.get",
        "client",
        payload={"session_id": "/etc/passwd", "sequence_number": 0},
    )
    resp_abs = message_dispatcher.dispatch(q_abs)
    assert resp_abs.message_type.value == "ERROR"
    assert resp_abs.payload["error_code"] == "ERR_PERSISTENCE_SECURITY_ERROR"

    # 4. Malformed session path fails
    q_malformed = create_query(
        "persistence.cycle.get",
        "client",
        payload={"session_id": "sess$invalid;rm", "sequence_number": 0},
    )
    resp_malformed = message_dispatcher.dispatch(q_malformed)
    assert resp_malformed.message_type.value == "ERROR"
    assert resp_malformed.payload["error_code"] == "ERR_PERSISTENCE_SECURITY_ERROR"

    # 5. Storage remains untouched on validation failure
    files_after = set(temp_storage_root.rglob("*"))
    assert files_before == files_after

    srv.stop()
