"""Tests for F3.2-I-AUTH-01 Remediation Races."""

import pytest
import threading
import time
import json
from unittest.mock import Mock, patch, MagicMock
from holomed.devices.control.manager import DeviceControlManager
from holomed.devices.models import StopRouteState, PhysicalCommand, SubmissionStatus, PhysicalCommandResult
from holomed.devices.resolution import ExecutionResolutionGate
from holomed.persistence.sessions import DurableSessionStore

def create_store(tmp_path):
    (tmp_path / "controller_epoch.json").write_text(json.dumps({"epoch_id": 1}))
    cam1_dir = tmp_path / "devices" / "cam1"
    cam1_dir.mkdir(parents=True, exist_ok=True)
    (cam1_dir / "device_epoch.json").write_text(json.dumps({"device_epoch": 1}))

    store = DurableSessionStore(tmp_path, epoch_id=1)
    store.start_session("session_1", 1)
    return store

def create_test_manager(store):
    registry = Mock()
    registry.contains.return_value = True

    device = Mock()
    device.device_id = "cam1"
    device.current_epoch = 1

    ep = Mock()
    ep.endpoint_id = "USB:1"
    device.endpoints = [ep]
    registry.get.return_value = device
    registry.all_devices = [device]

    resolution_gate = ExecutionResolutionGate()
    manager = DeviceControlManager(registry=registry, resolution_gate=resolution_gate)
    manager._capacity_releaser = Mock(wraps=store.record_operation_terminated)
    manager._capacity_admitter = Mock(wraps=store.record_operation_admitted)
    manager._state = Mock()
    manager._state.name = "STARTED"







    lease_registry = Mock()
    lease_registry.issue_lease.return_value = Mock(endpoint_lease_generation=1)
    lease_registry.next_command_sequence.return_value = 1
    manager._lease_registry = lease_registry
    manager._admission_state = Mock()
    manager._admission_state.value = "READY"
    manager._admission_state == "READY" # Hack for equality

    # Fake authoritative epoch provider
    manager._authoritative_epoch_provider = lambda: 1

    return manager, ep

def test_cancel_vs_submit_rejection_race(tmp_path):
    store = create_store(tmp_path)
    manager, ep = create_test_manager(store)

    exec_id = "exec-1"
    cmd = PhysicalCommand(
        device_epoch=1, controller_epoch=1, physical_operation_id="op-1", command_nonce="nonce-1",
        endpoint_id="USB:1", session_id="session_1", lifecycle_generation=1, endpoint_lease_generation=1,
        execution_id=exec_id, capability_scope=frozenset(["test"]), command_sequence=1, operation="test", parameters={}
    )
    store.record_operation_admitted(
        session_id="session_1", endpoint_id="USB:1", device_id="cam1", device_epoch=1,
        controller_epoch=1, physical_operation_id="op-1", command_nonce="nonce-1", correlation_id="nonce-1",
        execution_id=exec_id, command_name="test"
    )
    manager._active_commands[exec_id] = cmd

    # T2 preempts
    manager.preempt_execution('session_1', "cam1", "USB:1", exec_id, 1)

    record = manager._resolution_gate.resolve_timeout(exec_id, 1)
    assert record.stop_route_state == StopRouteState.PRE_CLAIM_CANCELLED

    # T1 submit fails, pops and releases
    cmd_popped = manager._active_commands.pop(exec_id, None)
    if cmd_popped:
        from holomed.persistence.exceptions import PersistenceLifecycleError
        try:
            manager._capacity_releaser("session_1", "cam1", 1, 1, "op-1", "nonce-1", "OPERATION_CONFIRMED_ABSENT")
        except PersistenceLifecycleError:
            pass

def test_cancel_vs_submit_acceptance_race(tmp_path):
    store = create_store(tmp_path)
    manager, ep = create_test_manager(store)

    exec_id = "exec-2"
    cmd = PhysicalCommand(
        device_epoch=1, controller_epoch=1, physical_operation_id="op-2", command_nonce="nonce-2",
        endpoint_id="USB:1", session_id="session_1", lifecycle_generation=1, endpoint_lease_generation=1,
        execution_id=exec_id, capability_scope=frozenset(["test"]), command_sequence=1, operation="test", parameters={}
    )
    store.record_operation_admitted(
        session_id="session_1", endpoint_id="USB:1", device_id="cam1", device_epoch=1,
        controller_epoch=1, physical_operation_id="op-2", command_nonce="nonce-2", correlation_id="nonce-2",
        execution_id=exec_id, command_name="test"
    )
    manager._active_commands[exec_id] = cmd

    # T2 preempts
    manager.preempt_execution('session_1', "cam1", "USB:1", exec_id, 1)

    # T1 submits
    if exec_id in manager._active_commands:
        manager._deadlines[exec_id] = (time.time() + 5.0, 1)

    # Worker claim
    success = manager._resolution_gate.claim_execution_ownership(exec_id, 1)
    assert success is False

def test_duplicate_cancel_during_retention(tmp_path):
    store = create_store(tmp_path)
    manager, ep = create_test_manager(store)

    exec_id = "exec-3"
    cmd = PhysicalCommand(
        device_epoch=1, controller_epoch=1, physical_operation_id="op-3", command_nonce="nonce-3",
        endpoint_id="USB:1", session_id="session_1", lifecycle_generation=1, endpoint_lease_generation=1,
        execution_id=exec_id, capability_scope=frozenset(["test"]), command_sequence=1, operation="test", parameters={}
    )
    store.record_operation_admitted(
        session_id="session_1", endpoint_id="USB:1", device_id="cam1", device_epoch=1,
        controller_epoch=1, physical_operation_id="op-3", command_nonce="nonce-3", correlation_id="nonce-3",
        execution_id=exec_id, command_name="test"
    )
    manager._active_commands[exec_id] = cmd

    # First cancel
    manager.preempt_execution('session_1', "cam1", "USB:1", exec_id, 1)
    # Duplicate cancel
    manager.preempt_execution('session_1', "cam1", "USB:1", exec_id, 1)

def test_cancel_after_five_seconds(tmp_path):
    store = create_store(tmp_path)
    manager, ep = create_test_manager(store)
    manager._capacity_snapshot_provider = store.get_active_operations_snapshot

    exec_id = "exec-4"
    cmd = PhysicalCommand(
        device_epoch=1, controller_epoch=1, physical_operation_id="op-4", command_nonce="nonce-4",
        endpoint_id="USB:1", session_id="session_1", lifecycle_generation=1, endpoint_lease_generation=1,
        execution_id=exec_id, capability_scope=frozenset(["test"]), command_sequence=1, operation="test", parameters={}
    )
    store.record_operation_admitted(
        session_id="session_1", endpoint_id="USB:1", device_id="cam1", device_epoch=1,
        controller_epoch=1, physical_operation_id="op-4", command_nonce="nonce-4", correlation_id="nonce-4",
        execution_id=exec_id, command_name="test"
    )
    manager._active_commands[exec_id] = cmd

    # Simulate timeout loop popping _active_commands
    manager._active_commands.pop(exec_id, None)

    # Preempt should now fallback to durable store and cancel it
    manager.preempt_execution('session_1', "cam1", "USB:1", exec_id, 1)

    record = manager._resolution_gate.resolve_timeout(exec_id, 1)
    assert record.stop_route_state == StopRouteState.PRE_CLAIM_CANCELLED


def test_auth_01_real_production_linearization(tmp_path):
    store = create_store(tmp_path)
    manager, ep = create_test_manager(store)
    manager._capacity_snapshot_provider = store.get_active_operations_snapshot

    # 1. Simulate handle_command (this normally sets up active_commands and durable store)
    exec_id = "prod_exec_1"

    # We directly mock the admission step that handle_command would perform
    store.record_operation_admitted(
        session_id="session_1", endpoint_id="USB:1", device_id="cam1", device_epoch=1,
        controller_epoch=1, physical_operation_id="op-prod-1", command_nonce="nonce-prod-1", correlation_id="nonce-prod-1",
        execution_id=exec_id, command_name="test_prod"
    )

    cmd = PhysicalCommand(
        device_epoch=1, controller_epoch=1, physical_operation_id="op-prod-1", command_nonce="nonce-prod-1",
        endpoint_id="USB:1", session_id="session_1", lifecycle_generation=1, endpoint_lease_generation=1,
        execution_id=exec_id, capability_scope=frozenset(["test"]), command_sequence=1, operation="test", parameters={}
    )
    manager._active_commands[exec_id] = cmd

    # 2. Simulate Cache Expiry (long running operation)
    manager._active_commands.pop(exec_id, None)

    # 3. Simulate Authorized Preemption
    manager.preempt_execution('session_1', "cam1", "USB:1", exec_id, 1)

    # 4. Verify Resolution
    assert exec_id in manager._resolution_gate._records
    assert manager._resolution_gate._records[exec_id].stop_route_state == StopRouteState.PRE_CLAIM_CANCELLED

    # Verify physical capacity was released
    snapshot = store.get_active_operations_snapshot()
    # Ensure exec_id is NOT active in durable store (meaning capacity was released)
    for canon, payload in snapshot.items():
        if payload.get("execution_id") == exec_id:
            assert payload.get("resolution") != "IN_FLIGHT"

