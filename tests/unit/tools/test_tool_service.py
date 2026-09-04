# -*- coding: utf-8 -*-
"""Unit tests for M31 ToolService dispatcher route hardening and tools.reset removal."""

from __future__ import annotations

import pytest

from holomed.core.dispatcher import MessageDispatcher
from holomed.core.exceptions import TopicValidationError, UnroutableMessageError
from holomed.devices.manager import DeviceManager
from holomed.execution._capability import _create_execution_capability
from holomed.protocol.builders import create_command, create_query
from holomed.runtime.context import RuntimeContext
from holomed.runtime.logging import SecretFilter
from holomed.runtime.service import ServiceState
from holomed.tools.models import (
    ToolCategory,
    ToolDescriptor,
    ToolExecutionStatus,
    ToolInvocationContext,
    ToolResult,
    ToolSafetyClassification,
)
from holomed.tools.service import ToolService


def dummy_tool() -> ToolDescriptor:
    return ToolDescriptor(
        tool_id="anatomy.query_organ",
        name="Query Organ",
        version="1.0",
        category=ToolCategory.QUERY_ANATOMY,
        safety_classification=ToolSafetyClassification.READ_ONLY_INFORMATIVE,
        description="desc",
        required_parameters=("entity_id",),
        optional_parameters=(),
        max_execution_budget_ms=10.0,
        is_idempotent=True,
        handler=lambda ctx: ToolResult(
            ctx.invocation_id,
            "anatomy.query_organ",
            ToolExecutionStatus.SUCCESS,
            {"found": True},
            1.0,
            1.0,
            0.0,
            ctx.epoch_id,
        ),
    )


def test_tools_reset_not_registered_on_dispatcher(
    runtime_context: RuntimeContext,
    device_manager: DeviceManager,
    message_dispatcher: MessageDispatcher,
    secret_filter: SecretFilter,
) -> None:
    """Verify M31: tools.reset is no longer registered on MessageDispatcher."""
    srv = ToolService(
        device_manager=device_manager,
        dispatcher=message_dispatcher,
        secret_filter=secret_filter,
    )
    srv.initialize(runtime_context)
    message_dispatcher.start()
    srv.start()

    # Pre-populate session sequences in engine
    assert srv._engine is not None
    srv._engine._session_sequences["session_A"] = 42

    # Attempt to dispatch tools.reset
    cmd = create_command("tools.reset", "operator", payload={"epoch_id": 1})
    with pytest.raises(UnroutableMessageError):
        message_dispatcher.dispatch(cmd)

    # Verify session sequence was NOT cleared
    assert srv._engine._session_sequences.get("session_A") == 42

    srv.stop()


def test_approved_tool_query_routes_remain_registered(
    runtime_context: RuntimeContext,
    device_manager: DeviceManager,
    message_dispatcher: MessageDispatcher,
    secret_filter: SecretFilter,
) -> None:
    """Verify tools.status, tools.registry, and tools.result remain registered and functional."""
    srv = ToolService(
        device_manager=device_manager,
        dispatcher=message_dispatcher,
        secret_filter=secret_filter,
    )
    srv.initialize(runtime_context)
    srv.register_tool(dummy_tool())
    message_dispatcher.start()
    srv.start()

    # 1. tools.status query
    q_status = create_query("tools.status", "client", payload={})
    resp_status = message_dispatcher.dispatch(q_status)
    assert resp_status is not None
    assert resp_status.message_type.value == "RESPONSE"
    assert resp_status.payload["tool_count"] == 1

    # 2. tools.registry query
    q_reg = create_query("tools.registry", "client", payload={})
    resp_reg = message_dispatcher.dispatch(q_reg)
    assert resp_reg is not None
    assert resp_reg.message_type.value == "RESPONSE"
    assert len(resp_reg.payload["tools"]) == 1

    # 3. tools.result query (not found)
    q_res = create_query("tools.result", "client", payload={"invocation_id": "nonexistent"})
    resp_res = message_dispatcher.dispatch(q_res)
    assert resp_res is not None
    assert resp_res.message_type.value == "ERROR"
    assert resp_res.payload["error_code"] == "ERR_RESULT_NOT_FOUND"

    srv.stop()


def test_internal_tool_lifecycle_preserved(
    runtime_context: RuntimeContext,
    device_manager: DeviceManager,
    message_dispatcher: MessageDispatcher,
    secret_filter: SecretFilter,
) -> None:
    """Verify internal ToolService.evict_session, reset, and clear continue functioning cleanly."""
    srv = ToolService(
        device_manager=device_manager,
        dispatcher=message_dispatcher,
        secret_filter=secret_filter,
    )
    srv.initialize(runtime_context)
    message_dispatcher.start()
    srv.start()

    assert srv._engine is not None
    srv._engine._session_sequences["session_to_evict"] = 10
    srv._engine._session_sequences["session_to_keep"] = 20

    # 1. evict_session under M29 capability
    cap = _create_execution_capability(
        service_instance_id=id(srv),
        session_id="session_to_evict",
        action="SESSION_TEARDOWN",
        sequence_number=1,
    )
    evicted = srv.evict_session("session_to_evict", cap)
    assert evicted is True
    assert "session_to_evict" not in srv._engine._session_sequences
    assert srv._engine._session_sequences.get("session_to_keep") == 20

    # 2. internal reset for matching epoch
    srv.reset(epoch_id=1)
    assert srv._epoch_id == 1
    assert len(srv._engine._session_sequences) == 0

    srv.stop()


def test_m32_tool_result_ownership_and_lifecycle(
    runtime_context: RuntimeContext,
    device_manager: DeviceManager,
    message_dispatcher: MessageDispatcher,
    secret_filter: SecretFilter,
) -> None:
    """M32 Invariants: ToolResult session ownership, query authorization, and post-teardown eviction."""
    srv = ToolService(
        device_manager=device_manager,
        dispatcher=message_dispatcher,
        secret_filter=secret_filter,
    )
    srv.initialize(runtime_context)
    srv.register_tool(dummy_tool())
    message_dispatcher.start()
    srv.start()

    # 1. Session A invokes tool
    cap_a = _create_execution_capability(
        service_instance_id=id(srv),
        session_id="session_A",
        action="TOOL_INVOCATION",
        sequence_number=1,
    )
    ctx_a = ToolInvocationContext(
        invocation_id="inv_session_a_001",
        tool_id="anatomy.query_organ",
        epoch_id=1,
        session_id="session_A",
        sequence_number=1,
        depth=1,
        parameters={"entity_id": "liver_segment_4"},
        correlation_id="corr_01",
        causation_id=None,
        timestamp_utc="2026-09-04T00:00:00Z",
    )
    res_a = srv.invoke_tool(ctx_a, capability=cap_a)
    assert res_a.invocation_id == "inv_session_a_001"
    assert res_a.session_id == "session_A"
    assert srv._engine is not None
    assert len(srv._engine.result_history) == 1

    # 2. Session A retrieves own result via tools.result dispatcher query
    q_a = create_query(
        "tools.result",
        "client_a",
        payload={"invocation_id": "inv_session_a_001", "session_id": "session_A"},
    )
    resp_a = message_dispatcher.dispatch(q_a)
    assert resp_a is not None
    assert resp_a.message_type.value == "RESPONSE"
    assert resp_a.payload["invocation_id"] == "inv_session_a_001"
    assert resp_a.payload["session_id"] == "session_A"
    assert resp_a.payload["result_payload"]["found"] is True

    # 3. Session B attempts to query Session A's invocation_id -> DENIED (ERR_RESULT_NOT_FOUND)
    q_b = create_query(
        "tools.result",
        "client_b",
        payload={"invocation_id": "inv_session_a_001", "session_id": "session_B"},
    )
    resp_b = message_dispatcher.dispatch(q_b)
    assert resp_b is not None
    assert resp_b.message_type.value == "ERROR"
    assert resp_b.payload["error_code"] == "ERR_RESULT_NOT_FOUND"

    # 4. Unknown invocation_id returns ERR_RESULT_NOT_FOUND
    q_unk = create_query(
        "tools.result",
        "client_a",
        payload={"invocation_id": "inv_nonexistent_999", "session_id": "session_A"},
    )
    resp_unk = message_dispatcher.dispatch(q_unk)
    assert resp_unk is not None
    assert resp_unk.message_type.value == "ERROR"
    assert resp_unk.payload["error_code"] == "ERR_RESULT_NOT_FOUND"

    # 5. Session A teardown occurs -> evict_session
    cap_td = _create_execution_capability(
        service_instance_id=id(srv),
        session_id="session_A",
        action="SESSION_TEARDOWN",
        sequence_number=2,
    )
    evicted = srv.evict_session("session_A", capability=cap_td)
    assert evicted is True

    # 6. Result is no longer resident in _result_history
    assert len(srv._engine.result_history) == 0

    # 7. Neither Session A nor Session B can retrieve the evicted result
    resp_post_a = message_dispatcher.dispatch(q_a)
    assert resp_post_a is not None
    assert resp_post_a.payload["error_code"] == "ERR_RESULT_NOT_FOUND"

    resp_post_b = message_dispatcher.dispatch(q_b)
    assert resp_post_b is not None
    assert resp_post_b.payload["error_code"] == "ERR_RESULT_NOT_FOUND"

    srv.stop()


def test_m32_tools_hostile_authorization_bypasses(
    runtime_context: RuntimeContext,
    device_manager: DeviceManager,
    message_dispatcher: MessageDispatcher,
    secret_filter: SecretFilter,
) -> None:
    """M32 Regression: Explicit hostile attack vectors on tools.result authorization."""
    srv = ToolService(
        device_manager=device_manager,
        dispatcher=message_dispatcher,
        secret_filter=secret_filter,
    )
    srv.initialize(runtime_context)
    srv.register_tool(dummy_tool())
    message_dispatcher.start()
    srv.start()
    assert srv._engine is not None

    # A. Session A creates result
    cap_a = _create_execution_capability(
        service_instance_id=id(srv),
        session_id="session_A",
        action="TOOL_INVOCATION",
        sequence_number=1,
    )
    ctx_a = ToolInvocationContext(
        invocation_id="inv_hostile_001",
        tool_id="anatomy.query_organ",
        epoch_id=1,
        session_id="session_A",
        sequence_number=1,
        depth=1,
        parameters={"entity_id": "liver_segment_4"},
        correlation_id="corr_hostile_01",
        causation_id=None,
        timestamp_utc="2026-09-04T00:00:00Z",
    )
    res_a = srv.invoke_tool(ctx_a, capability=cap_a)
    assert res_a.invocation_id == "inv_hostile_001"
    assert res_a.session_id == "session_A"

    # Prove protected object exists before request
    assert len(srv._engine.result_history) == 1
    engine_res = srv._engine.get_result("inv_hostile_001", caller_session_id="session_A")
    assert engine_res is not None
    assert engine_res.result_payload["found"] is True

    # B. Session B queries same invocation_id normally
    q_b_normal = create_query(
        "tools.result",
        "client_b",
        payload={"invocation_id": "inv_hostile_001", "session_id": "session_B"},
    )
    resp_b = message_dispatcher.dispatch(q_b_normal)
    assert resp_b is not None
    assert resp_b.message_type.value == "ERROR"
    assert resp_b.payload.get("error_code") == "ERR_RESULT_NOT_FOUND"
    assert "result_payload" not in resp_b.payload

    # C. Session B queries with session_id omitted
    # Scenario C1: Direct query with session_id omitted from payload and metadata
    q_c_omitted_direct = create_query(
        "tools.result",
        "client_b",
        payload={"invocation_id": "inv_hostile_001"},
    )
    resp_c1 = message_dispatcher.dispatch(q_c_omitted_direct)
    assert resp_c1 is not None
    assert resp_c1.message_type.value == "ERROR"
    assert resp_c1.payload.get("error_code") == "ERR_RESULT_NOT_FOUND"
    assert "result_payload" not in resp_c1.payload

    # Scenario C2: Authenticated as session_B in metadata with payload session_id omitted
    q_c_omitted_meta = create_query(
        "tools.result",
        "client_b",
        payload={"invocation_id": "inv_hostile_001"},
        metadata={"session_id": "session_B"},
    )
    resp_c2 = message_dispatcher.dispatch(q_c_omitted_meta)
    assert resp_c2 is not None
    assert resp_c2.message_type.value == "ERROR"
    assert resp_c2.payload.get("error_code") == "ERR_RESULT_NOT_FOUND"
    assert "result_payload" not in resp_c2.payload

    # D. Session B queries with session_id=None
    # Scenario D1: Direct query with payload session_id=None
    q_d_null_direct = create_query(
        "tools.result",
        "client_b",
        payload={"invocation_id": "inv_hostile_001", "session_id": None},
    )
    resp_d1 = message_dispatcher.dispatch(q_d_null_direct)
    assert resp_d1 is not None
    assert resp_d1.message_type.value == "ERROR"
    assert resp_d1.payload.get("error_code") == "ERR_RESULT_NOT_FOUND"
    assert "result_payload" not in resp_d1.payload

    # Scenario D2: Authenticated as session_B in metadata with payload session_id=None
    q_d_null_meta = create_query(
        "tools.result",
        "client_b",
        payload={"invocation_id": "inv_hostile_001", "session_id": None},
        metadata={"session_id": "session_B"},
    )
    resp_d2 = message_dispatcher.dispatch(q_d_null_meta)
    assert resp_d2 is not None
    assert resp_d2.message_type.value == "ERROR"
    assert resp_d2.payload.get("error_code") == "ERR_RESULT_NOT_FOUND"
    assert "result_payload" not in resp_d2.payload

    # E. Session B queries with forged Session-A session_id (authenticated as session_B in metadata)
    q_e_forged = create_query(
        "tools.result",
        "client_b",
        payload={"invocation_id": "inv_hostile_001", "session_id": "session_A"},
        metadata={"session_id": "session_B"},
    )
    resp_e = message_dispatcher.dispatch(q_e_forged)
    assert resp_e is not None
    assert resp_e.message_type.value == "ERROR"
    assert resp_e.payload.get("error_code") == "ERR_RESULT_NOT_FOUND"
    assert "result_payload" not in resp_e.payload

    # F. Direct engine lookup with caller_session_id=None -> FAIL CLOSED
    res_f = srv._engine.get_result("inv_hostile_001", caller_session_id=None)  # type: ignore
    assert res_f is None

    # G. Direct engine lookup without caller identity (empty string, whitespace, missing) -> FAIL CLOSED
    assert srv._engine.get_result("inv_hostile_001", caller_session_id="") is None
    assert srv._engine.get_result("inv_hostile_001", caller_session_id="   ") is None
    with pytest.raises(TypeError):
        srv._engine.get_result("inv_hostile_001")  # type: ignore

    # Verify protected object still exists and was not mutated after all attacks
    assert len(srv._engine.result_history) == 1
    engine_res_after = srv._engine.get_result("inv_hostile_001", caller_session_id="session_A")
    assert engine_res_after is not None
    assert engine_res_after.result_payload["found"] is True

    # Session A authorized query works cleanly
    q_a_allowed = create_query(
        "tools.result",
        "client_a",
        payload={"invocation_id": "inv_hostile_001", "session_id": "session_A"},
    )
    resp_a = message_dispatcher.dispatch(q_a_allowed)
    assert resp_a is not None
    assert resp_a.message_type.value == "RESPONSE"
    assert resp_a.payload["invocation_id"] == "inv_hostile_001"
    assert resp_a.payload["result_payload"]["found"] is True

    srv.stop()
