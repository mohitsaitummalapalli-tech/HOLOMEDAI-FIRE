"""Tests for F3.2-I Queued Cancel Remediation."""

import pytest
import threading
import json
from unittest.mock import Mock, patch
from pathlib import Path
from holomed.devices.control.manager import DeviceControlManager
from holomed.devices.models import StopRouteState, PhysicalCommand, DeviceState
from holomed.devices.registry import DeviceRegistry
from holomed.devices.interfaces import RegistryAuthorityToken
from holomed.persistence.exceptions import PersistenceLifecycleError
from holomed.persistence.sessions import DurableSessionStore
from holomed.persistence.authority import ControllerAuthorityStore, DeviceEpochAuthority
from tests.unit.devices.conftest import make_test_context

def create_store(tmp_path):
    (tmp_path / "controller_epoch.json").write_text(json.dumps({"epoch_id": 1}))
    # Device epoch for cam1 must exist in the root since sessions.py creates it there
    cam1_dir = tmp_path / "devices" / "cam1"
    cam1_dir.mkdir(parents=True, exist_ok=True)
    (cam1_dir / "device_epoch.json").write_text(json.dumps({"device_epoch": 1}))

    store = DurableSessionStore(tmp_path, epoch_id=1)
    store.start_session("session_1", 1)
    return store

def create_manager(store):
    token = RegistryAuthorityToken()
    registry = DeviceRegistry(token)

    manager = DeviceControlManager(registry=registry)
    manager._capacity_releaser = store.record_operation_terminated
    manager._state = Mock()
    manager._state.name = "STARTED"






    manager._registry = registry
    return manager, token

def create_mock_device_and_endpoint():
    mock_device = Mock()
    mock_device.device_id = "cam1"
    mock_device.physical_id = "USB:1"
    mock_device.type = "SIMULATED"
    mock_device.state = DeviceState.UNREGISTERED
    mock_ep = Mock()
    mock_ep.endpoint_id = "USB:1"
    mock_device.endpoints = (mock_ep,)
    return mock_device, mock_ep

def admit_test_command(store, session_id, ep_id, op_id, nonce):
    store.record_operation_admitted(
        session_id=session_id,
        endpoint_id=ep_id,
        device_id="cam1",
        device_epoch=1,
        controller_epoch=1,
        physical_operation_id=str(op_id),
        command_nonce=str(nonce),
        execution_id=f"exec-{op_id}",
        command_name="test"
    )
    cmd = Mock(spec=PhysicalCommand)
    cmd.session_id = session_id
    cmd.endpoint_id = ep_id
    cmd.device_epoch = 1
    cmd.controller_epoch = 1
    cmd.physical_operation_id = str(op_id)
    cmd.command_nonce = str(nonce)
    cmd.execution_id = f"exec-{op_id}"
    cmd.operation = "test"
    return cmd

def test_queued_cancellation_normal(tmp_path):
    store = create_store(tmp_path)
    manager, token = create_manager(store)
    device, ep = create_mock_device_and_endpoint()
    manager._registry.register(device, token)

    cmd = admit_test_command(store, "session_1", ep.endpoint_id, 100, 1)
    manager._active_commands[cmd.execution_id] = cmd

    mock_gate = Mock()
    def fake_route(*args, **kwargs):
        return StopRouteState.PRE_CLAIM_CANCELLING
    mock_gate.route_stop_request.side_effect = fake_route
    mock_gate.commit_pre_claim_cancel = Mock()
    mock_gate.abort_pre_claim_cancel = Mock()
    manager._resolution_gate = mock_gate

    manager.preempt_execution('session_1', "cam1", ep.endpoint_id, cmd.execution_id, 1)

    ep.request_stop.assert_not_called()
    assert cmd.execution_id in manager._active_commands

    active, term = store._reconstruct_reservations_locked()
    key = ("cam1", 1, 1, "100", "1")
    assert key in term
    assert term[key]["resolution"] == "PREEMPTED"

def test_persistence_failure(tmp_path):
    store = create_store(tmp_path)
    manager, token = create_manager(store)
    device, ep = create_mock_device_and_endpoint()
    manager._registry.register(device, token)

    mock_gate = Mock()
    def fake_route(*args, **kwargs):
        return StopRouteState.PRE_CLAIM_CANCELLING
    mock_gate.route_stop_request.side_effect = fake_route
    mock_gate.commit_pre_claim_cancel = Mock()
    mock_gate.abort_pre_claim_cancel = Mock()
    manager._resolution_gate = mock_gate

    cmd = admit_test_command(store, "session_1", ep.endpoint_id, 101, 1)
    manager._active_commands[cmd.execution_id] = cmd

    original_releaser = manager._capacity_releaser
    def failing_releaser(*args, **kwargs):
        raise PersistenceLifecycleError("Injected failure")

    manager._capacity_releaser = failing_releaser

    import pytest
    with pytest.raises(PersistenceLifecycleError):
        manager.preempt_execution('session_1', "cam1", ep.endpoint_id, cmd.execution_id, 1)

    manager._capacity_releaser = original_releaser
    manager.preempt_execution('session_1', "cam1", ep.endpoint_id, cmd.execution_id, 1)

    assert cmd.execution_id in manager._active_commands

    active, term = store._reconstruct_reservations_locked()
    key = ("cam1", 1, 1, "101", "1")
    assert key in term
    assert term[key]["resolution"] == "PREEMPTED"

def test_different_message_duplicate_cancel(tmp_path):
    store = create_store(tmp_path)
    manager, token = create_manager(store)
    device, ep = create_mock_device_and_endpoint()
    manager._registry.register(device, token)

    mock_gate = Mock()
    def fake_route(*args, **kwargs):
        return StopRouteState.PRE_CLAIM_CANCELLING
    mock_gate.route_stop_request.side_effect = fake_route
    mock_gate.commit_pre_claim_cancel = Mock()
    mock_gate.abort_pre_claim_cancel = Mock()
    manager._resolution_gate = mock_gate

    cmd1 = admit_test_command(store, "session_1", ep.endpoint_id, 104, 1)
    manager._active_commands[cmd1.execution_id] = cmd1

    cmd2 = Mock(spec=PhysicalCommand)
    cmd2.session_id = cmd1.session_id
    cmd2.endpoint_id = cmd1.endpoint_id
    cmd2.device_epoch = cmd1.device_epoch
    cmd2.controller_epoch = cmd1.controller_epoch
    cmd2.physical_operation_id = cmd1.physical_operation_id
    cmd2.command_nonce = cmd1.command_nonce
    cmd2.execution_id = "exec-104-duplicate-message"
    cmd2.operation = "test"
    manager._active_commands[cmd2.execution_id] = cmd2

    # Two distinct request identities sharing the same physical canonical identity
    manager.preempt_execution('session_1', "cam1", ep.endpoint_id, cmd1.execution_id, 1)
    manager.preempt_execution('session_1', "cam1", ep.endpoint_id, cmd2.execution_id, 1)

    assert cmd1.execution_id in manager._active_commands
    assert cmd2.execution_id in manager._active_commands

    active, term = store._reconstruct_reservations_locked()
    key = ("cam1", 1, 1, "104", "1")
    assert key in term
    assert term[key]["resolution"] == "PREEMPTED"

def test_targeted_identity_replacement(tmp_path):
    store = create_store(tmp_path)
    manager, token = create_manager(store)
    device, ep = create_mock_device_and_endpoint()
    manager._registry.register(device, token)

    mock_gate = Mock()
    def fake_route(*args, **kwargs):
        return StopRouteState.PRE_CLAIM_CANCELLING
    mock_gate.route_stop_request.side_effect = fake_route
    mock_gate.commit_pre_claim_cancel = Mock()
    mock_gate.abort_pre_claim_cancel = Mock()
    manager._resolution_gate = mock_gate

    cmd1 = admit_test_command(store, "session_1", ep.endpoint_id, 110, 1)

    # We rig capacity releaser to swap the active command right before popping
    original_releaser = manager._capacity_releaser
    def capacity_releaser_with_swap(*args, **kwargs):
        # Allow durable persistence to succeed for the original operation
        if original_releaser:
            original_releaser(*args, **kwargs)

        # Now an adversary replaces the active command with a different canonical identity
        # (e.g., a different device_epoch) but reuses the exact same execution_id!
        cmd2 = Mock(spec=PhysicalCommand)
        cmd2.session_id = cmd1.session_id
        cmd2.endpoint_id = cmd1.endpoint_id
        cmd2.device_epoch = 9999  # DIFFERENT CANONICAL IDENTITY
        cmd2.controller_epoch = cmd1.controller_epoch
        cmd2.physical_operation_id = cmd1.physical_operation_id
        cmd2.command_nonce = cmd1.command_nonce
        cmd2.execution_id = cmd1.execution_id
        cmd2.operation = "test"
        manager._active_commands[cmd1.execution_id] = cmd2

    manager._capacity_releaser = capacity_releaser_with_swap

    manager._active_commands[cmd1.execution_id] = cmd1
    manager.preempt_execution('session_1', "cam1", ep.endpoint_id, cmd1.execution_id, 1)

    # Prove that the replacement command was NOT popped because its canonical identity didn't match!
    assert manager._active_commands[cmd1.execution_id].device_epoch == 9999


def test_concurrent_duplicate_cancellation(tmp_path):
    store = create_store(tmp_path)
    manager, token = create_manager(store)
    device, ep = create_mock_device_and_endpoint()
    manager._registry.register(device, token)

    cmd = admit_test_command(store, "session_1", ep.endpoint_id, 105, 1)
    manager._active_commands[cmd.execution_id] = cmd

    mock_gate = Mock()
    def fake_route(*args, **kwargs):
        return StopRouteState.PRE_CLAIM_CANCELLING
    mock_gate.route_stop_request.side_effect = fake_route
    mock_gate.commit_pre_claim_cancel = Mock()
    mock_gate.abort_pre_claim_cancel = Mock()
    manager._resolution_gate = mock_gate

    errors = []
    barrier = threading.Barrier(5)
    original_releaser = manager._capacity_releaser

    def synchronized_releaser(*args, **kwargs):
        barrier.wait()
        if original_releaser:
            return original_releaser(*args, **kwargs)
        return None

    manager._capacity_releaser = synchronized_releaser

    def cancel_task():
        try:
            manager.preempt_execution('session_1', "cam1", ep.endpoint_id, cmd.execution_id, 1)
        except Exception as e:
            errors.append(e)

    threads = [threading.Thread(target=cancel_task) for _ in range(5)]
    for t in threads: t.start()
    for t in threads: t.join()

    # The store idempotency logic ensures no duplicates raise errors or leak capacity
    assert len(errors) == 0
    assert cmd.execution_id in manager._active_commands

    active, term = store._reconstruct_reservations_locked()
    key = ("cam1", 1, 1, "105", "1")
    assert key in term
    assert term[key]["resolution"] == "PREEMPTED"

def test_concurrent_cancel_vs_completed_race(tmp_path):
    store = create_store(tmp_path)
    manager, token = create_manager(store)
    device, ep = create_mock_device_and_endpoint()
    manager._registry.register(device, token)

    cmd = admit_test_command(store, "session_1", ep.endpoint_id, 106, 1)
    manager._active_commands[cmd.execution_id] = cmd

    barrier = threading.Barrier(2)
    original_releaser = manager._capacity_releaser

    def synchronized_releaser(*args, **kwargs):
        barrier.wait()
        if original_releaser:
            return original_releaser(*args, **kwargs)
        return None

    manager._capacity_releaser = synchronized_releaser

    results: dict = {"completed": None, "preempted": None}

    def thread_a_completed():
        try:
            manager.durably_record_terminal_state(cmd.execution_id, "COMPLETED")
            results["completed"] = "success"
        except Exception as e:
            results["completed"] = e

    def thread_b_preempted():
        try:
            manager.durably_record_terminal_state(cmd.execution_id, "PREEMPTED")
            results["preempted"] = "success"
        except Exception as e:
            results["preempted"] = e

    t_a = threading.Thread(target=thread_a_completed)
    t_b = threading.Thread(target=thread_b_preempted)
    t_a.start(); t_b.start()
    t_a.join(); t_b.join()

    # Exactly one terminal resolution wins. The other gets PersistenceLifecycleError.
    successes = [k for k, v in results.items() if v == "success"]
    failures = [k for k, v in results.items() if isinstance(v, PersistenceLifecycleError)]
    assert len(successes) == 1
    assert len(failures) == 1

    active, term = store._reconstruct_reservations_locked()
    key = ("cam1", 1, 1, "106", "1")
    assert key in term
    assert term[key]["resolution"] == successes[0].upper()

# Removed test_durable_success_before_pop_interruption_and_retry because
# active commands are now retained as tombstones and not popped during preemption.

def test_restart_after_preempted_journal(tmp_path):
    # This verifies the journal state can be reconstructed and PREEMPTED remains
    store = create_store(tmp_path)
    manager, token = create_manager(store)
    device, ep = create_mock_device_and_endpoint()
    manager._registry.register(device, token)

    cmd = admit_test_command(store, "session_1", ep.endpoint_id, 108, 1)
    store.record_operation_terminated(
        cmd.session_id, "cam1", 1, 1, cmd.physical_operation_id, cmd.command_nonce, "PREEMPTED"
    )

    # Recovery primitive: get_active_operations_snapshot is used by StateRehydrationEngine
    # It ensures that terminated operations are not rehydrated as active
    new_store = DurableSessionStore(tmp_path, epoch_id=1)
    new_store.start_session("session_1", 1)
    active_snapshot = new_store.get_active_operations_snapshot()

    key = ("cam1", 1, 1, "108", "1")
    assert key not in active_snapshot
