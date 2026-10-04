"""Tests for restart behavior during AUTH-01 active_commands tombstone retention."""

import pytest
import time
import json
import threading
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
    store.start_session("session-1", 1)
    return store

def test_restart_with_tombstone(tmp_path):
    store = create_store(tmp_path)
    
    exec_id_1 = "exec-pre-claim"
    exec_id_2 = "exec-accepted-dropped"
    
    # 1. Pre-claim cancelled
    store.record_operation_admitted(
        session_id="session-1", endpoint_id="USB:1", device_id="cam1", device_epoch=1,
        controller_epoch=1, physical_operation_id="op-1", command_nonce="nonce-1", correlation_id="nonce-1",
        execution_id=exec_id_1, command_name="test1"
    )
    store.record_operation_terminated(
        session_id="session-1", device_id="cam1", device_epoch=1, controller_epoch=1,
        physical_operation_id="op-1", command_nonce="nonce-1", resolution="PREEMPTED"
    )
    
    # 2. Accepted but not claimed cancelled (same durable state, just for completeness)
    store.record_operation_admitted(
        session_id="session-1", endpoint_id="USB:1", device_id="cam1", device_epoch=1,
        controller_epoch=1, physical_operation_id="op-2", command_nonce="nonce-2", correlation_id="nonce-2",
        execution_id=exec_id_2, command_name="test2"
    )
    store.record_operation_terminated(
        session_id="session-1", device_id="cam1", device_epoch=1, controller_epoch=1,
        physical_operation_id="op-2", command_nonce="nonce-2", resolution="PREEMPTED"
    )
    
    # 3. Terminal execution awaiting tombstone expiry
    store.record_operation_admitted(
        session_id="session-1", endpoint_id="USB:1", device_id="cam1", device_epoch=1,
        controller_epoch=1, physical_operation_id="op-3", command_nonce="nonce-3", correlation_id="nonce-3",
        execution_id="exec-3", command_name="test3"
    )
    store.record_operation_terminated(
        session_id="session-1", device_id="cam1", device_epoch=1, controller_epoch=1,
        physical_operation_id="op-3", command_nonce="nonce-3", resolution="OPERATION_COMPLETED"
    )
    
    # Now simulate a restart
    new_store = DurableSessionStore(tmp_path, epoch_id=2)
    # The active/terminated will be rebuilt
    
    active, term = new_store._reconstruct_reservations_locked()
    
    # Ensure they are all in terminated, and none in active
    canon1 = ("cam1", 1, 1, "op-1", "nonce-1")
    canon2 = ("cam1", 1, 1, "op-2", "nonce-2")
    canon3 = ("cam1", 1, 1, "op-3", "nonce-3")
    
    assert canon1 not in active
    assert canon2 not in active
    assert canon3 not in active
    
    assert canon1 in term
    assert term[canon1]["resolution"] == "PREEMPTED"
    
    assert canon2 in term
    assert term[canon2]["resolution"] == "PREEMPTED"
    
    assert canon3 in term
    assert term[canon3]["resolution"] == "OPERATION_COMPLETED"
    
    # Because they are not in active, StateRehydrationEngine will not emit any work for them.
    # Therefore, no stale work can become executable after restart.
