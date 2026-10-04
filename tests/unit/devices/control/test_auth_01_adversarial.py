"""AUTH-01 Adversarial Tests: Three-state persistence, crash cutpoints, claim barriers, lease invariants."""

import pytest
import threading
import json
from unittest.mock import Mock, patch, MagicMock
from pathlib import Path
from holomed.devices.control.manager import DeviceControlManager
from holomed.devices.models import (
    StopRouteState, PhysicalCommand, DeviceState, CommandState,
    AuthoritativeExecutionRecord,
)
from holomed.devices.resolution import ExecutionResolutionGate
from holomed.devices.registry import DeviceRegistry
from holomed.devices.interfaces import RegistryAuthorityToken
from holomed.persistence.exceptions import (
    PersistenceLifecycleError,
    PersistenceValidationError,
    PersistenceEpochMismatchError,
    PersistenceTerminationConflictError,
)
from holomed.persistence.sessions import DurableSessionStore


# ===========================================================================
# Shared fixtures
# ===========================================================================

def create_store(tmp_path):
    (tmp_path / "controller_epoch.json").write_text(json.dumps({"epoch_id": 1}))
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
        command_nonce=str(nonce), correlation_id=str(nonce),
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


def setup_manager_with_real_gate(tmp_path, op_id, nonce=1):
    """Create a manager with a REAL ExecutionResolutionGate (not mocked)."""
    store = create_store(tmp_path)
    manager, token = create_manager(store)
    device, ep = create_mock_device_and_endpoint()
    manager._registry.register(device, token)

    gate = ExecutionResolutionGate()
    manager._resolution_gate = gate

    cmd = admit_test_command(store, "session_1", ep.endpoint_id, op_id, nonce)
    manager._active_commands[cmd.execution_id] = cmd

    return store, manager, gate, device, ep, cmd


# ===========================================================================
# Requirement 1: Three-state persistence outcome
# ===========================================================================


class TestThreeStatePersistenceOutcome:
    """Prove that COMMITTED / NOT_COMMITTED / COMMIT_OUTCOME_UNKNOWN are
    handled with distinct gate transitions and resource outcomes."""

    def test_committed_path(self, tmp_path):
        """COMMITTED: gate → PRE_CLAIM_CANCELLED, resources released."""
        store, manager, gate, device, ep, cmd = setup_manager_with_real_gate(tmp_path, 200)

        manager.preempt_execution("session_1", "cam1", ep.endpoint_id, cmd.execution_id, 1)

        rec = gate._records[cmd.execution_id]
        assert rec.stop_route_state == StopRouteState.PRE_CLAIM_CANCELLED
        assert rec.current_state == CommandState.PREEMPTED
        assert rec.terminal_resolution_status is True

        # Durable truth: terminated
        active, term = store._reconstruct_reservations_locked()
        key = ("cam1", 1, 1, "200", "1")
        assert key in term
        assert term[key]["resolution"] == "PREEMPTED"

    def test_not_committed_lifecycle_error(self, tmp_path):
        """NOT_COMMITTED (PersistenceLifecycleError): gate → abort → NOT_REQUESTED."""
        store, manager, gate, device, ep, cmd = setup_manager_with_real_gate(tmp_path, 201)

        original = manager._capacity_releaser
        def lifecycle_fail(*args, **kwargs):
            raise PersistenceLifecycleError("Session inactive")
        manager._capacity_releaser = lifecycle_fail

        with pytest.raises(PersistenceLifecycleError):
            manager.preempt_execution("session_1", "cam1", ep.endpoint_id, cmd.execution_id, 1)

        rec = gate._records[cmd.execution_id]
        assert rec.stop_route_state == StopRouteState.NOT_REQUESTED
        assert rec.current_state == CommandState.ACCEPTED
        assert rec.terminal_resolution_status is False

        # Durable truth: still active
        active, term = store._reconstruct_reservations_locked()
        key = ("cam1", 1, 1, "201", "1")
        assert key in active
        assert key not in term

    def test_not_committed_validation_error(self, tmp_path):
        """NOT_COMMITTED (PersistenceValidationError): gate → abort → NOT_REQUESTED."""
        store, manager, gate, device, ep, cmd = setup_manager_with_real_gate(tmp_path, 202)

        def validation_fail(*args, **kwargs):
            raise PersistenceValidationError("Bad params")
        manager._capacity_releaser = validation_fail

        with pytest.raises(PersistenceValidationError):
            manager.preempt_execution("session_1", "cam1", ep.endpoint_id, cmd.execution_id, 1)

        rec = gate._records[cmd.execution_id]
        assert rec.stop_route_state == StopRouteState.NOT_REQUESTED
        assert rec.terminal_resolution_status is False

    def test_not_committed_epoch_mismatch(self, tmp_path):
        """NOT_COMMITTED (PersistenceEpochMismatchError): gate → abort."""
        store, manager, gate, device, ep, cmd = setup_manager_with_real_gate(tmp_path, 203)

        def epoch_fail(*args, **kwargs):
            raise PersistenceEpochMismatchError("Stale epoch")
        manager._capacity_releaser = epoch_fail

        with pytest.raises(PersistenceEpochMismatchError):
            manager.preempt_execution("session_1", "cam1", ep.endpoint_id, cmd.execution_id, 1)

        rec = gate._records[cmd.execution_id]
        assert rec.stop_route_state == StopRouteState.NOT_REQUESTED
        assert rec.terminal_resolution_status is False

    def test_commit_outcome_unknown_oserror(self, tmp_path):
        """COMMIT_OUTCOME_UNKNOWN (OSError): gate → quarantine, resources retained."""
        store, manager, gate, device, ep, cmd = setup_manager_with_real_gate(tmp_path, 204)

        def io_fail(*args, **kwargs):
            raise OSError("Disk I/O timeout")
        manager._capacity_releaser = io_fail

        with pytest.raises(OSError):
            manager.preempt_execution("session_1", "cam1", ep.endpoint_id, cmd.execution_id, 1)

        rec = gate._records[cmd.execution_id]
        assert rec.stop_route_state == StopRouteState.PRE_CLAIM_CANCEL_QUARANTINED
        assert rec.quarantine_consequence is True
        assert rec.terminal_resolution_status is False

        # Resources MUST remain occupied (not released)
        active, term = store._reconstruct_reservations_locked()
        key = ("cam1", 1, 1, "204", "1")
        assert key in active

    def test_persistence_commit_outcome_unknown_does_not_abort_cancel_intent(self, tmp_path):
        """COMMIT_OUTCOME_UNKNOWN (RuntimeError): gate → quarantine, does NOT revert."""
        store, manager, gate, device, ep, cmd = setup_manager_with_real_gate(tmp_path, 205)

        def unexpected_fail(*args, **kwargs):
            raise RuntimeError("Unexpected hardware failure")
        manager._capacity_releaser = unexpected_fail

        with pytest.raises(RuntimeError):
            manager.preempt_execution("session_1", "cam1", ep.endpoint_id, cmd.execution_id, 1)

        rec = gate._records[cmd.execution_id]

        # 1. MUST NOT revert PRE_CLAIM_CANCELLING to NOT_REQUESTED
        assert rec.stop_route_state != StopRouteState.NOT_REQUESTED
        # 2. MUST remain fail-closed / Quarantined
        assert rec.stop_route_state == StopRouteState.PRE_CLAIM_CANCEL_QUARANTINED
        assert rec.quarantine_consequence is True
        assert rec.terminal_resolution_status is False

        # 3. Worker claim MUST remain blocked
        claimed = gate.claim_execution_ownership(cmd.execution_id, 1)
        assert claimed is False

        # 4. Resource ownership must not be released
        active, term = store._reconstruct_reservations_locked()
        key = ("cam1", 1, 1, "205", "1")
        assert key in active
        assert key not in term

    def test_quarantined_blocks_subsequent_claims(self, tmp_path):
        """After quarantine, worker claims MUST be rejected."""
        store, manager, gate, device, ep, cmd = setup_manager_with_real_gate(tmp_path, 206)

        def io_fail(*args, **kwargs):
            raise OSError("Network loss")
        manager._capacity_releaser = io_fail

        with pytest.raises(OSError):
            manager.preempt_execution("session_1", "cam1", ep.endpoint_id, cmd.execution_id, 1)

        claimed = gate.claim_execution_ownership(cmd.execution_id, 1)
        assert claimed is False

    def test_quarantined_blocks_subsequent_cancellation_retry(self, tmp_path):
        """After quarantine, a retry of route_stop_request should not re-enter PRE_CLAIM_CANCELLING."""
        store, manager, gate, device, ep, cmd = setup_manager_with_real_gate(tmp_path, 207)

        def io_fail(*args, **kwargs):
            raise OSError("Connection reset")
        manager._capacity_releaser = io_fail

        with pytest.raises(OSError):
            manager.preempt_execution("session_1", "cam1", ep.endpoint_id, cmd.execution_id, 1)

        # Gate is quarantined; route_stop_request should not return PRE_CLAIM_CANCELLING
        # (it's not terminal, not claimed, but stop_route_state is PRE_CLAIM_CANCEL_QUARANTINED)
        result = gate.route_stop_request(cmd.execution_id, 1)
        # Should not be PRE_CLAIM_CANCELLING since we are already in QUARANTINED state
        assert result != StopRouteState.PRE_CLAIM_CANCELLING


# ===========================================================================
# Requirement 2: Conditional durable transition
# ===========================================================================


class TestConditionalDurableTransition:
    """Prove the durable write requires canonical identity match and ADMITTED state."""

    def test_durable_write_requires_admitted_precondition(self, tmp_path):
        """Durable write to non-admitted canonical identity raises PersistenceTerminationConflictError."""
        store = create_store(tmp_path)

        with pytest.raises(PersistenceTerminationConflictError):
            store.record_operation_terminated(
                session_id="session_1",
                device_id="cam1",
                device_epoch=1,
                controller_epoch=1,
                physical_operation_id="999",
                command_nonce="1",
                resolution="PREEMPTED"
            )

    def test_durable_write_rejects_double_terminal(self, tmp_path):
        """Durable write to already-terminated identity raises PersistenceLifecycleError if conflicting."""
        store = create_store(tmp_path)
        # Admit then terminate
        store.record_operation_admitted(
            session_id="session_1", endpoint_id="USB:1",
            device_id="cam1", device_epoch=1, controller_epoch=1,
            physical_operation_id="300", command_nonce="1", correlation_id="1", execution_id="exec-300", command_name="test"
        )
        store.record_operation_terminated(
            session_id="session_1", device_id="cam1",
            device_epoch=1, controller_epoch=1,
            physical_operation_id="300", command_nonce="1",
            resolution="COMPLETED"
        )
        # Second terminal with different resolution → conflict
        with pytest.raises(PersistenceLifecycleError):
            store.record_operation_terminated(
                session_id="session_1", device_id="cam1",
                device_epoch=1, controller_epoch=1,
                physical_operation_id="300", command_nonce="1",
                resolution="PREEMPTED"
            )

    def test_durable_write_idempotent_same_resolution(self, tmp_path):
        """Durable write with identical resolution is idempotent (no error)."""
        store = create_store(tmp_path)
        store.record_operation_admitted(
            session_id="session_1", endpoint_id="USB:1",
            device_id="cam1", device_epoch=1, controller_epoch=1,
            physical_operation_id="301", command_nonce="1", correlation_id="1", execution_id="exec-301", command_name="test"
        )
        store.record_operation_terminated(
            session_id="session_1", device_id="cam1",
            device_epoch=1, controller_epoch=1,
            physical_operation_id="301", command_nonce="1",
            resolution="PREEMPTED"
        )
        # Same resolution again → idempotent
        store.record_operation_terminated(
            session_id="session_1", device_id="cam1",
            device_epoch=1, controller_epoch=1,
            physical_operation_id="301", command_nonce="1",
            resolution="PREEMPTED"
        )

    def test_cancel_vs_complete_race_exactly_one_winner(self, tmp_path):
        """Force deterministic race: cancel vs complete → exactly one terminal winner."""
        store = create_store(tmp_path)
        store.record_operation_admitted(
            session_id="session_1", endpoint_id="USB:1",
            device_id="cam1", device_epoch=1, controller_epoch=1,
            physical_operation_id="302", command_nonce="1", correlation_id="1", execution_id="exec-302", command_name="test"
        )

        results = {"cancel": None, "complete": None}
        barrier = threading.Barrier(2)

        def cancel_task():
            barrier.wait()
            try:
                store.record_operation_terminated(
                    session_id="session_1", device_id="cam1",
                    device_epoch=1, controller_epoch=1,
                    physical_operation_id="302", command_nonce="1",
                    resolution="PREEMPTED"
                )
                results["cancel"] = "success"
            except Exception as e:
                results["cancel"] = e

        def complete_task():
            barrier.wait()
            try:
                store.record_operation_terminated(
                    session_id="session_1", device_id="cam1",
                    device_epoch=1, controller_epoch=1,
                    physical_operation_id="302", command_nonce="1",
                    resolution="COMPLETED"
                )
                results["complete"] = "success"
            except Exception as e:
                results["complete"] = e

        t1 = threading.Thread(target=cancel_task)
        t2 = threading.Thread(target=complete_task)
        t1.start(); t2.start()
        t1.join(); t2.join()

        successes = [k for k, v in results.items() if v == "success"]
        failures = [k for k, v in results.items() if isinstance(v, (PersistenceLifecycleError,))]
        assert len(successes) == 1
        assert len(failures) == 1


# ===========================================================================
# Requirement 3: Crash cutpoint tests
# ===========================================================================


class TestCrashCutpoints:
    """Deterministic tests for restart recovery at every cancellation stage."""

    def test_cutpoint_a_crash_before_durable_write(self, tmp_path):
        """Crash after PRE_CLAIM_CANCELLING but before durable write.
        Restart: durable cancellation absent → operation is still ADMITTED."""
        store = create_store(tmp_path)
        gate = ExecutionResolutionGate()
        admit_test_command(store, "session_1", "USB:1", 400, 1)

        # Simulate: gate records PRE_CLAIM_CANCELLING
        gate.route_stop_request("exec-400", 1)
        rec = gate._records["exec-400"]
        assert rec.stop_route_state == StopRouteState.PRE_CLAIM_CANCELLING

        # CRASH — gate state is lost, no durable write happened

        # Restart: reconstruct from durable truth
        new_store = DurableSessionStore(tmp_path, epoch_id=1)
        new_store.start_session("session_1", 1)
        active_snapshot = new_store.get_active_operations_snapshot()
        key = ("cam1", 1, 1, "400", "1")
        # Operation MUST still be active since no durable PREEMPTED was written
        assert key in active_snapshot

    def test_cutpoint_b_crash_after_durable_preempted_before_gate_commit(self, tmp_path):
        """Crash after durable PREEMPTED committed but before commit_pre_claim_cancel.
        Restart: durable PREEMPTED MUST dominate lost in-memory gate state."""
        store = create_store(tmp_path)
        gate = ExecutionResolutionGate()
        cmd = admit_test_command(store, "session_1", "USB:1", 401, 1)

        # Simulate: gate records PRE_CLAIM_CANCELLING
        gate.route_stop_request("exec-401", 1)

        # Durable PREEMPTED committed successfully
        store.record_operation_terminated(
            session_id="session_1", device_id="cam1",
            device_epoch=1, controller_epoch=1,
            physical_operation_id="401", command_nonce="1",
            resolution="PREEMPTED"
        )

        # CRASH before commit_pre_claim_cancel — gate state is lost

        # Restart: reconstruct from durable truth
        new_store = DurableSessionStore(tmp_path, epoch_id=1)
        new_store.start_session("session_1", 1)
        active_snapshot = new_store.get_active_operations_snapshot()
        key = ("cam1", 1, 1, "401", "1")
        # Operation MUST NOT be in active (PREEMPTED is durable truth)
        assert key not in active_snapshot

        # Durable truth shows PREEMPTED
        _, term = new_store._reconstruct_reservations_locked()
        assert key in term
        assert term[key]["resolution"] == "PREEMPTED"

    def test_cutpoint_c_crash_persistence_unknown(self, tmp_path):
        """Crash after persistence outcome UNKNOWN.
        Restart: system MUST NOT assume either success or failure — reconcile from durable truth."""
        store = create_store(tmp_path)
        gate = ExecutionResolutionGate()
        cmd = admit_test_command(store, "session_1", "USB:1", 402, 1)

        gate.route_stop_request("exec-402", 1)

        # Simulate UNKNOWN outcome: we don't know if the write succeeded
        # Gate is quarantined
        gate.quarantine_pre_claim_cancel("exec-402", 1)
        rec = gate._records["exec-402"]
        assert rec.stop_route_state == StopRouteState.PRE_CLAIM_CANCEL_QUARANTINED

        # CRASH — gate state is lost

        # Restart scenario 1: durable write DID succeed (we didn't know)
        store.record_operation_terminated(
            session_id="session_1", device_id="cam1",
            device_epoch=1, controller_epoch=1,
            physical_operation_id="402", command_nonce="1",
            resolution="PREEMPTED"
        )

        new_store_1 = DurableSessionStore(tmp_path, epoch_id=1)
        new_store_1.restore_session_from_disk("session_1")
        active = new_store_1.get_active_operations_snapshot()
        key = ("cam1", 1, 1, "402", "1")
        # Durable truth: PREEMPTED → not active
        assert key not in active

    def test_cutpoint_c_crash_persistence_not_committed(self, tmp_path):
        """Crash after persistence outcome NOT_COMMITTED where write actually failed.
        Restart: operation is still ADMITTED, can be re-cancelled."""
        store = create_store(tmp_path)
        gate = ExecutionResolutionGate()
        cmd = admit_test_command(store, "session_1", "USB:1", 403, 1)

        gate.route_stop_request("exec-403", 1)
        gate.quarantine_pre_claim_cancel("exec-403", 1)

        # CRASH — gate state is lost, durable write never succeeded

        # Restart: reconstruct
        new_store = DurableSessionStore(tmp_path, epoch_id=1)
        new_store.restore_session_from_disk("session_1")
        active = new_store.get_active_operations_snapshot()
        key = ("cam1", 1, 1, "403", "1")
        # Operation MUST still be active
        assert key in active

        # Can be re-cancelled
        new_store.record_operation_terminated(
            session_id="session_1", device_id="cam1",
            device_epoch=1, controller_epoch=1,
            physical_operation_id="403", command_nonce="1",
            resolution="PREEMPTED"
        )
        active2, term2 = new_store._reconstruct_reservations_locked()
        assert key not in active2
        assert key in term2
        assert term2[key]["resolution"] == "PREEMPTED"


# ===========================================================================
# Requirement 4: Claim barrier
# ===========================================================================


class TestClaimBarrier:
    """Prove claim_execution_ownership under every stop_route_state."""

    def test_claim_allowed_not_requested(self):
        """NOT_REQUESTED → claim allowed."""
        gate = ExecutionResolutionGate()
        claimed = gate.claim_execution_ownership("exec-claim-1", 1)
        assert claimed is True
        assert gate._records["exec-claim-1"].execution_claimed is True

    def test_claim_rejected_pre_claim_cancelling(self):
        """PRE_CLAIM_CANCELLING → claim rejected."""
        gate = ExecutionResolutionGate()
        gate.route_stop_request("exec-claim-2", 1)
        rec = gate._records["exec-claim-2"]
        assert rec.stop_route_state == StopRouteState.PRE_CLAIM_CANCELLING

        claimed = gate.claim_execution_ownership("exec-claim-2", 1)
        assert claimed is False
        assert gate._records["exec-claim-2"].execution_claimed is False

    def test_claim_rejected_pre_claim_cancelled(self):
        """PRE_CLAIM_CANCELLED → claim rejected (terminal)."""
        gate = ExecutionResolutionGate()
        gate.route_stop_request("exec-claim-3", 1)
        gate.commit_pre_claim_cancel("exec-claim-3", 1)
        rec = gate._records["exec-claim-3"]
        assert rec.stop_route_state == StopRouteState.PRE_CLAIM_CANCELLED
        assert rec.terminal_resolution_status is True

        claimed = gate.claim_execution_ownership("exec-claim-3", 1)
        assert claimed is False

    def test_claim_rejected_quarantined(self):
        """PRE_CLAIM_CANCEL_QUARANTINED → claim rejected."""
        gate = ExecutionResolutionGate()
        gate.route_stop_request("exec-claim-4", 1)
        gate.quarantine_pre_claim_cancel("exec-claim-4", 1)
        rec = gate._records["exec-claim-4"]
        assert rec.stop_route_state == StopRouteState.PRE_CLAIM_CANCEL_QUARANTINED

        claimed = gate.claim_execution_ownership("exec-claim-4", 1)
        assert claimed is False

    def test_claim_then_cancel_intent_rejected(self):
        """Once claimed, route_stop_request returns PHYSICAL_ROUTING_ACCEPTED (not PRE_CLAIM_CANCELLING)."""
        gate = ExecutionResolutionGate()
        gate.claim_execution_ownership("exec-claim-5", 1)
        result = gate.route_stop_request("exec-claim-5", 1)
        assert result == StopRouteState.PHYSICAL_ROUTING_ACCEPTED

    def test_concurrent_claim_vs_cancel_exactly_one_wins(self):
        """Concurrent claim and cancel: exactly one succeeds under lock."""
        gate = ExecutionResolutionGate()

        barrier = threading.Barrier(2)
        results = {"claim": None, "cancel": None}

        def claim_task():
            barrier.wait()
            results["claim"] = gate.claim_execution_ownership("exec-claim-6", 1)

        def cancel_task():
            barrier.wait()
            results["cancel"] = gate.route_stop_request("exec-claim-6", 1)

        t1 = threading.Thread(target=claim_task)
        t2 = threading.Thread(target=cancel_task)
        t1.start(); t2.start()
        t1.join(); t2.join()

        rec = gate._records["exec-claim-6"]

        if results["claim"] is True:
            # Claim won first → cancel returns PHYSICAL_ROUTING_ACCEPTED
            assert results["cancel"] in (
                StopRouteState.PHYSICAL_ROUTING_ACCEPTED,
                StopRouteState.ALREADY_ROUTED,
            )
            assert rec.execution_claimed is True
        else:
            # Cancel won first → claim rejected
            assert results["claim"] is False
            assert results["cancel"] == StopRouteState.PRE_CLAIM_CANCELLING
            assert rec.execution_claimed is False


# ===========================================================================
# Requirement 5: Lease/capacity invariant during PRE_CLAIM_CANCELLING
# ===========================================================================


class TestLeaseCapacityInvariant:
    """Prove resources are retained during PRE_CLAIM_CANCELLING and released only after COMMITTED."""

    def test_resources_retained_during_cancelling(self, tmp_path):
        """During PRE_CLAIM_CANCELLING: lease owned, capacity occupied."""
        store, manager, gate, device, ep, cmd = setup_manager_with_real_gate(tmp_path, 500)

        # Inject a blocking durable write that we control
        write_barrier = threading.Event()
        original_releaser = manager._capacity_releaser

        def blocking_releaser(*args, **kwargs):
            write_barrier.wait(timeout=5.0)
            return original_releaser(*args, **kwargs)

        manager._capacity_releaser = blocking_releaser

        cancel_result = [None]
        def cancel_task():
            try:
                manager.preempt_execution("session_1", "cam1", ep.endpoint_id, cmd.execution_id, 1)
                cancel_result[0] = "success"
            except Exception as e:
                cancel_result[0] = e

        t = threading.Thread(target=cancel_task)
        t.start()

        # Wait for the gate to transition to PRE_CLAIM_CANCELLING
        import time
        time.sleep(0.1)

        # While blocked: capacity MUST still be occupied
        active, _ = store._reconstruct_reservations_locked()
        key = ("cam1", 1, 1, "500", "1")
        assert key in active

        # Claims MUST be rejected
        claimed = gate.claim_execution_ownership(cmd.execution_id, 1)
        assert claimed is False

        # Release the write
        write_barrier.set()
        t.join(timeout=5.0)

        # After committed: resources released
        active2, term2 = store._reconstruct_reservations_locked()
        assert key not in active2
        assert key in term2

    def test_resources_retained_after_quarantine(self, tmp_path):
        """After COMMIT_OUTCOME_UNKNOWN → quarantine: resources retained."""
        store, manager, gate, device, ep, cmd = setup_manager_with_real_gate(tmp_path, 501)

        def io_fail(*args, **kwargs):
            raise OSError("Connection lost after partial write")
        manager._capacity_releaser = io_fail

        with pytest.raises(OSError):
            manager.preempt_execution("session_1", "cam1", ep.endpoint_id, cmd.execution_id, 1)

        # Resources MUST remain occupied
        active, _ = store._reconstruct_reservations_locked()
        key = ("cam1", 1, 1, "501", "1")
        assert key in active

    def test_resources_released_only_after_committed(self, tmp_path):
        """Resources released only after durable PREEMPTED is authoritatively committed."""
        store, manager, gate, device, ep, cmd = setup_manager_with_real_gate(tmp_path, 502)

        manager.preempt_execution("session_1", "cam1", ep.endpoint_id, cmd.execution_id, 1)

        # Post-commit: resources released
        active, term = store._reconstruct_reservations_locked()
        key = ("cam1", 1, 1, "502", "1")
        assert key not in active
        assert key in term
        assert term[key]["resolution"] == "PREEMPTED"

        # Gate record is terminal
        rec = gate._records[cmd.execution_id]
        assert rec.terminal_resolution_status is True
        assert rec.stop_route_state == StopRouteState.PRE_CLAIM_CANCELLED
