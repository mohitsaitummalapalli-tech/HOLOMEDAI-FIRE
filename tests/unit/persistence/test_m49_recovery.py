import pytest
import os
import json
import time
from pathlib import Path
from datetime import datetime, timezone
from typing import Any
from holomed.persistence.sessions import DurableSessionStore, SessionStatus
from holomed.persistence.authority import ControllerAuthorityStore
from holomed.devices.control.recovery import StateRehydrationEngine

def test_rehydration_active_capacity(tmp_path):
    """Validate that ADMITTED but not TERMINATED operations hold capacity across rehydration."""
    store_dir = tmp_path / "sessions"
    auth = ControllerAuthorityStore(store_dir)
    auth.allocate_next_epoch()
    epoch = auth.read_current_epoch()
    store = DurableSessionStore(store_dir, epoch)

    session = store.start_session("sess1", epoch)

    # Admit one operation
    store.record_operation_admitted(
        session_id=session.session_id,
        endpoint_id="end1",
        device_id="dev1",
        device_epoch=1,
        controller_epoch=epoch,
        physical_operation_id="op1",
        command_nonce="nonce1",
        execution_id="exec1",
        command_name="test.cmd"
    )

    assert store.get_active_physical_operations() == 1

    # Simulate a crash and boot a new instance
    new_auth = ControllerAuthorityStore(store_dir)
    new_auth.allocate_next_epoch()
    new_epoch = new_auth.read_current_epoch()

    new_store = DurableSessionStore(store_dir, new_epoch)
    new_session = new_store.start_session("sess2", new_epoch)

    # The active operations should be 1
    assert new_store.get_active_physical_operations() == 1

    # Now run rehydration
    engine = StateRehydrationEngine(new_store, type('', (), {})(), new_auth) # type: ignore
    engine.rehydrate_controller_state(new_session.session_id)

    # Capacity should STILL be 1 because they are FAULTED_UNKNOWN
    assert new_store.get_active_physical_operations() == 1

    active_ops = new_store.get_active_operations_snapshot()
    canon = ("dev1", 1, epoch, "op1", "nonce1")
    assert active_ops[canon]["resolution"] == "FAULTED_UNKNOWN"


def test_rehydration_terminal_capacity(tmp_path):
    """Validate that TERMINATED operations do not hold capacity after rehydration."""
    store_dir = tmp_path / "sessions"
    auth = ControllerAuthorityStore(store_dir)

    auth.allocate_next_epoch()
    epoch = auth.read_current_epoch()
    store = DurableSessionStore(store_dir, epoch)

    session = store.start_session("sess1", epoch)

    store.record_operation_admitted(
        session_id=session.session_id,
        endpoint_id="end1",
        device_id="dev1",
        device_epoch=1,
        controller_epoch=epoch,
        physical_operation_id="op2",
        command_nonce="nonce2",
        execution_id="exec2",
        command_name="test.cmd"
    )

    store.record_operation_terminated(
        session_id=session.session_id,
        device_id="dev1",
        device_epoch=1,
        controller_epoch=epoch,
        physical_operation_id="op2",
        command_nonce="nonce2",
        resolution="COMPLETED"
    )

    assert store.get_active_physical_operations() == 0

    # Simulate crash
    new_auth = ControllerAuthorityStore(store_dir)
    new_auth.allocate_next_epoch()
    new_epoch = new_auth.read_current_epoch()

    new_store = DurableSessionStore(store_dir, new_epoch)
    new_session = new_store.start_session("sess2", new_epoch)

    engine = StateRehydrationEngine(new_store, type('', (), {})(), new_auth) # type: ignore
    engine.rehydrate_controller_state(new_session.session_id)

    assert new_store.get_active_physical_operations() == 0
    assert len(new_store.get_active_operations_snapshot()) == 0


def test_journal_tail_failure(tmp_path):
    """Validate safe truncation of incomplete journal entries."""
    store_dir = tmp_path / "sessions"
    auth = ControllerAuthorityStore(store_dir)

    auth.allocate_next_epoch()
    epoch = auth.read_current_epoch()
    store = DurableSessionStore(store_dir, epoch)
    session = store.start_session("sess1", epoch)

    store.record_operation_admitted(
        session_id=session.session_id,
        endpoint_id="end1",
        device_id="dev1",
        device_epoch=1,
        controller_epoch=epoch,
        physical_operation_id="op3",
        command_nonce="nonce3",
        execution_id="exec3",
        command_name="test.cmd"
    )

    # Corrupt the journal by appending a partial JSON line
    journal_file = store_dir / f"{session.session_id}.jsonl"
    with open(journal_file, "a") as f:
        f.write('{"entry_type": "OPERATION_TERMINATED", "payload": {"resolution": "COMP') # Cut off
        f.flush()

    # Re-read should safely ignore the truncated line
    new_auth = ControllerAuthorityStore(store_dir)
    new_auth.allocate_next_epoch()
    new_epoch = new_auth.read_current_epoch()
    new_store = DurableSessionStore(store_dir, new_epoch)

    # The admitted operation should still exist, meaning the truncated termination didn't corrupt the file parsing
    assert new_store.get_active_physical_operations() == 1


def test_old_operation_wrong_authority_rejection(tmp_path):
    """Validate that attempting to terminate an old operation with a wrong authority epoch fails."""
    from holomed.persistence.exceptions import PersistenceEpochMismatchError

    store_dir = tmp_path / "sessions"
    auth = ControllerAuthorityStore(store_dir)
    auth.allocate_next_epoch()
    epoch1 = auth.read_current_epoch()
    store1 = DurableSessionStore(store_dir, epoch1)

    session1 = store1.start_session("sess1", epoch1)
    store1.record_operation_admitted(
        session_id=session1.session_id,
        endpoint_id="end1",
        device_id="dev1",
        device_epoch=1,
        controller_epoch=epoch1,
        physical_operation_id="op_auth",
        command_nonce="nonce_auth",
        execution_id="exec_auth",
        command_name="test.cmd"
    )

    # New epoch
    auth.allocate_next_epoch()
    epoch2 = auth.read_current_epoch()
    store2 = DurableSessionStore(store_dir, epoch2)
    store2.restore_session_from_disk(session1.session_id)

    # Trying to terminate with the wrong authoritative_epoch
    with pytest.raises(PersistenceEpochMismatchError):
        store2.record_operation_terminated(
            session_id=session1.session_id,
            device_id="dev1",
            device_epoch=1,
            controller_epoch=epoch1,
            physical_operation_id="op_auth",
            command_nonce="nonce_auth",
            resolution="FAULTED_UNKNOWN",
            authoritative_epoch=epoch1  # WRONG! The current epoch is epoch2.
        )

def test_old_operation_new_authority_recovery(tmp_path):
    """Validate that old operations can be terminated if the new authoritative epoch is correct."""
    store_dir = tmp_path / "sessions"
    auth = ControllerAuthorityStore(store_dir)
    auth.allocate_next_epoch()
    epoch1 = auth.read_current_epoch()
    store1 = DurableSessionStore(store_dir, epoch1)

    session1 = store1.start_session("sess1", epoch1)
    store1.record_operation_admitted(
        session_id=session1.session_id,
        endpoint_id="end1",
        device_id="dev1",
        device_epoch=1,
        controller_epoch=epoch1,
        physical_operation_id="op_auth",
        command_nonce="nonce_auth",
        execution_id="exec_auth",
        command_name="test.cmd"
    )

    # New epoch
    auth.allocate_next_epoch()
    epoch2 = auth.read_current_epoch()
    store2 = DurableSessionStore(store_dir, epoch2)
    store2.restore_session_from_disk(session1.session_id)

    # Valid termination
    store2.record_operation_terminated(
        session_id=session1.session_id,
        device_id="dev1",
        device_epoch=1,
        controller_epoch=epoch1,
        physical_operation_id="op_auth",
        command_nonce="nonce_auth",
        resolution="FAULTED_UNKNOWN",
        authoritative_epoch=epoch2
    )

    # Verify canonical identity in journal
    journal_file = store_dir / f"{session1.session_id}.jsonl"
    with open(journal_file, "r") as f:
        lines = f.readlines()

    last_line = json.loads(lines[-1])
    assert last_line["entry_type"] == "OPERATION_TERMINATED"
    assert last_line["payload"]["controller_epoch"] == epoch1
    assert last_line["payload"]["resolution"] == "FAULTED_UNKNOWN"

def test_journal_middle_corruption_fail_closed(tmp_path):
    """Validate that middle-record corruption fails closed instead of truncating."""
    from holomed.persistence.exceptions import PersistenceCorruptionError

    store_dir = tmp_path / "sessions"
    auth = ControllerAuthorityStore(store_dir)
    auth.allocate_next_epoch()
    epoch = auth.read_current_epoch()
    store = DurableSessionStore(store_dir, epoch)
    session = store.start_session("sess1", epoch)

    store.record_operation_admitted(
        session_id=session.session_id,
        endpoint_id="end1",
        device_id="dev1",
        device_epoch=1,
        controller_epoch=epoch,
        physical_operation_id="op_middle1",
        command_nonce="nonce1",
        execution_id="exec1",
        command_name="test.cmd"
    )

    store.record_operation_admitted(
        session_id=session.session_id,
        endpoint_id="end2",
        device_id="dev1",
        device_epoch=1,
        controller_epoch=epoch,
        physical_operation_id="op_middle2",
        command_nonce="nonce2",
        execution_id="exec2",
        command_name="test.cmd"
    )

    journal_file = store_dir / f"{session.session_id}.jsonl"
    with open(journal_file, "r") as f:
        lines = f.readlines()

    # Corrupt the middle line (which is index 1 because index 0 is SESSION_STARTED)
    lines[1] = '{"entry_type": "OPERATION_ADMITTED", "payload": {"status": "CORRUPT"\n'

    with open(journal_file, "w") as f:
        f.writelines(lines)

    # Re-read should FAIL CLOSED with PersistenceCorruptionError
    new_auth = ControllerAuthorityStore(store_dir)
    new_epoch = new_auth.read_current_epoch()

    with pytest.raises(PersistenceCorruptionError):
        DurableSessionStore(store_dir, new_epoch).get_active_physical_operations()


def test_end_to_end_recovery_engine(tmp_path):
    """Validate end-to-end recovery path through StateRehydrationEngine."""
    from holomed.devices.control.recovery import StateRehydrationEngine

    store_dir = tmp_path / "sessions"
    auth = ControllerAuthorityStore(store_dir)
    auth.allocate_next_epoch()
    epoch1 = auth.read_current_epoch()
    store1 = DurableSessionStore(store_dir, epoch1)

    session1 = store1.start_session("sess1", epoch1)
    store1.record_operation_admitted(
        session_id=session1.session_id,
        endpoint_id="end1",
        device_id="dev1",
        device_epoch=1,
        controller_epoch=epoch1,
        physical_operation_id="op_e2e",
        command_nonce="nonce_e2e",
        execution_id="exec_e2e",
        command_name="test.cmd"
    )

    # Capacity is 1
    assert store1.get_active_physical_operations() == 1

    # Simulate restart
    auth.allocate_next_epoch()
    epoch2 = auth.read_current_epoch()
    store2 = DurableSessionStore(store_dir, epoch2)
    session2 = store2.start_session("sess2", epoch2)
    # Intentionally do not manually restore session1 - engine should handle it.

    # The active ops snapshot should show the old operation
    assert store2.get_active_physical_operations() == 1

    # Use rehydration engine
    engine = StateRehydrationEngine(store2, None, auth)
    engine.rehydrate_controller_state(session2.session_id)

    # The capacity is NOT released (still holding)
    assert store2.get_active_physical_operations() == 1

    # But it's in FAULTED_UNKNOWN state
    active = store2.get_active_operations_snapshot()
    canonical = ("dev1", 1, epoch1, "op_e2e", "nonce_e2e")
    assert active[canonical]["resolution"] == "FAULTED_UNKNOWN"

    # Check journal (MUST be routed to the ORIGINAL session)
    journal_file = store_dir / f"{session1.session_id}.jsonl"
    with open(journal_file, "r") as f:
        lines = f.readlines()

    last_line = json.loads(lines[-1])
    assert last_line["entry_type"] == "OPERATION_TERMINATED"
    assert last_line["payload"]["resolution"] == "FAULTED_UNKNOWN"
    assert last_line["payload"]["controller_epoch"] == epoch1
    assert last_line["epoch_id"] == epoch2  # Written with recovery authority
    assert last_line["session_id"] == session1.session_id
