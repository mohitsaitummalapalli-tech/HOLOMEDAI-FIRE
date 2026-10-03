"""M49 Correlation Enforcement Tests.

Tests the durable admission invariants and preemption routing for correlation_id:
A. Same Correlation -> Different Execution -> Rejected
B. Terminated Correlation -> Any Execution -> Rejected
C. Same Correlation -> Same Execution -> Idempotent accepted
D. >1 matches for preempt_by_correlation -> Error/Fail closed
E. 0 matches for preempt_by_correlation -> No-op
F. 1 match for preempt_by_correlation -> Resolves correctly
"""

import pytest
import uuid
from pathlib import Path
from unittest.mock import Mock

from holomed.persistence.sessions import DurableSessionStore
from holomed.persistence.exceptions import PersistenceIdentityReuseError
from holomed.persistence.authority import ControllerAuthorityStore
from holomed.devices.control.manager import DeviceControlManager
from holomed.devices.control.exceptions import DeviceControlError

def init_device_auth(store, device_id: str = "dev1"):
    from holomed.persistence.authority import DeviceEpochAuthority
    if hasattr(store, "_storage_root"):
        root = store._storage_root / "devices"
    else:
        root = store / "devices"
    DeviceEpochAuthority(root).allocate_next_device_epoch(device_id)


def test_correlation_different_execution_rejected(temp_storage_root: Path) -> None:
    """Test A: Same Correlation -> Different Execution -> Rejected."""
    auth = ControllerAuthorityStore(temp_storage_root)
    auth.allocate_next_epoch()
    epoch = auth.read_current_epoch()
    store = DurableSessionStore(temp_storage_root, epoch_id=epoch)
    store.start_session("sess_01", epoch_id=epoch)
    init_device_auth(store, "dev1")
    correlation_id = "corr_01"
    
    # 1. Admit C1 -> E1
    store.record_operation_admitted(
        session_id="sess_01",
        endpoint_id="ep1",
        device_id="dev1",
        device_epoch=1,
        controller_epoch=1,
        physical_operation_id=None,
        command_nonce="nonce1",
        correlation_id=correlation_id,
        execution_id="E1",
        command_name="grasp",
        request_fingerprint="fp1"
    )
    
    # 2. Attempt to admit C1 -> E2 (malicious/replayed correlation_id with new execution/nonce)
    with pytest.raises(PersistenceIdentityReuseError, match=f"Correlation ID {correlation_id} is already bound to active execution E1"):
        store.record_operation_admitted(
            session_id="sess_01",
            endpoint_id="ep1",
            device_id="dev1",
            device_epoch=1,
            controller_epoch=1,
            physical_operation_id=None,
            command_nonce="nonce2",
            correlation_id=correlation_id,
            execution_id="E2",
            command_name="grasp",
            request_fingerprint="fp2"
        )


def test_terminated_correlation_rejected(temp_storage_root: Path) -> None:
    """Test B: Terminated Correlation -> Any Execution -> Rejected."""
    auth = ControllerAuthorityStore(temp_storage_root)
    auth.allocate_next_epoch()
    epoch = auth.read_current_epoch()
    store = DurableSessionStore(temp_storage_root, epoch_id=epoch)
    store.start_session("sess_01", epoch_id=epoch)
    init_device_auth(store, "dev1")
    correlation_id = "corr_01"
    
    # 1. Admit C1 -> E1
    phys_op, is_replay, res = store.record_operation_admitted(
        session_id="sess_01",
        endpoint_id="ep1",
        device_id="dev1",
        device_epoch=1,
        controller_epoch=1,
        physical_operation_id=None,
        command_nonce="nonce1",
        correlation_id=correlation_id,
        execution_id="E1",
        command_name="grasp",
        request_fingerprint="fp1"
    )
    
    # 2. Terminate E1
    assert phys_op is not None
    store.record_operation_terminated(
        session_id="sess_01",
        device_id="dev1",
        device_epoch=1,
        controller_epoch=1,
        physical_operation_id=phys_op,
        command_nonce="nonce1",
        resolution="PREEMPTED"
    )
    
    # 3. Attempt to reuse C1
    with pytest.raises(PersistenceIdentityReuseError, match=f"Correlation ID {correlation_id} was bound to a terminated execution and cannot be reused"):
        store.record_operation_admitted(
            session_id="sess_01",
            endpoint_id="ep1",
            device_id="dev1",
            device_epoch=1,
            controller_epoch=1,
            physical_operation_id=None,
            command_nonce="nonce2",
            correlation_id=correlation_id,
            execution_id="E2",
            command_name="grasp",
            request_fingerprint="fp2"
        )


def test_same_correlation_same_execution_accepted(temp_storage_root: Path) -> None:
    """Test C: Same Correlation -> Same Execution -> Idempotent accepted."""
    auth = ControllerAuthorityStore(temp_storage_root)
    auth.allocate_next_epoch()
    epoch = auth.read_current_epoch()
    store = DurableSessionStore(temp_storage_root, epoch_id=epoch)
    store.start_session("sess_01", epoch_id=epoch)
    init_device_auth(store, "dev1")
    correlation_id = "corr_01"
    
    # 1. Admit C1 -> E1
    phys_op1, replay1, res1 = store.record_operation_admitted(
        session_id="sess_01",
        endpoint_id="ep1",
        device_id="dev1",
        device_epoch=1,
        controller_epoch=1,
        physical_operation_id=None,
        command_nonce="nonce1",
        correlation_id=correlation_id,
        execution_id="E1",
        command_name="grasp",
        request_fingerprint="fp1"
    )
    
    # 2. Replay exactly C1 -> E1 (same canonical identity)
    phys_op2, replay2, res2 = store.record_operation_admitted(
        session_id="sess_01",
        endpoint_id="ep1",
        device_id="dev1",
        device_epoch=1,
        controller_epoch=1,
        physical_operation_id=phys_op1,
        command_nonce="nonce1",
        correlation_id=correlation_id,
        execution_id="E1",
        command_name="grasp",
        request_fingerprint="fp1"
    )
    
    assert replay2 is True
    assert phys_op1 == phys_op2


def test_dcm_preempt_by_correlation_zero_matches() -> None:
    """Test E: 0 matches for preempt_by_correlation -> No-op."""
    dcm = DeviceControlManager(registry=Mock(), capacity_snapshot_provider=lambda: {})
    dcm._state = Mock() # mock STARTED state
    from holomed.runtime.service import ServiceState
    dcm._state = ServiceState.STARTED
    
    # Should not raise
    dcm.preempt_by_correlation("corr_01")


def test_dcm_preempt_by_correlation_one_match() -> None:
    """Test F: 1 match for preempt_by_correlation -> Resolves correctly."""
    active_ops = {
        ("dev1", 1, 1, "op1", "nonce1"): {
            "correlation_id": "corr_01",
            "execution_id": "E1",
            "_original_session_id": "sess_01",
            "endpoint_id": "ep1",
            "lifecycle_generation": 1,
        }
    }
    dcm = DeviceControlManager(registry=Mock(), capacity_snapshot_provider=lambda: active_ops)
    from holomed.runtime.service import ServiceState
    dcm._state = ServiceState.STARTED
    
    dcm.preempt_execution = Mock()
    
    dcm.preempt_by_correlation("corr_01")
    
    dcm.preempt_execution.assert_called_once_with(
        session_id="sess_01",
        device_id="dev1",
        endpoint_id="ep1",
        execution_id="E1",
        lifecycle_generation=1
    )


def test_dcm_preempt_by_correlation_multiple_matches() -> None:
    """Test D: >1 matches for preempt_by_correlation -> Error/Fail closed."""
    active_ops = {
        ("dev1", 1, 1, "op1", "nonce1"): {
            "correlation_id": "corr_01",
            "execution_id": "E1",
            "_original_session_id": "sess_01",
            "endpoint_id": "ep1",
        },
        ("dev1", 1, 1, "op2", "nonce2"): {
            "correlation_id": "corr_01",
            "execution_id": "E2",
            "_original_session_id": "sess_01",
            "endpoint_id": "ep1",
        }
    }
    dcm = DeviceControlManager(registry=Mock(), capacity_snapshot_provider=lambda: active_ops)
    from holomed.runtime.service import ServiceState
    dcm._state = ServiceState.STARTED
    
    with pytest.raises(DeviceControlError, match="FATAL: Durable state corruption. Multiple active executions found for correlation_id corr_01"):
        dcm.preempt_by_correlation("corr_01")


def test_dcm_preempt_by_correlation_corruption_missing_lifecycle_generation() -> None:
    active_ops = {("dev1", 1, 1, "op1", "nonce1"): {"correlation_id": "C1", "execution_id": "E1", "_original_session_id": "sess_1", "endpoint_id": "ep1"}}
    dcm = DeviceControlManager(registry=Mock(), capacity_snapshot_provider=lambda: active_ops)
    from holomed.runtime.service import ServiceState
    dcm._state = ServiceState.STARTED
    with pytest.raises(DeviceControlError, match="FATAL: Durable state corruption. Invalid lifecycle_generation"):
        dcm.preempt_by_correlation("C1")

def test_dcm_preempt_by_correlation_corruption_invalid_lifecycle_generation() -> None:
    active_ops = {("dev1", 1, 1, "op1", "nonce1"): {"correlation_id": "C1", "execution_id": "E1", "_original_session_id": "sess_1", "endpoint_id": "ep1", "lifecycle_generation": "not-an-int"}}
    dcm = DeviceControlManager(registry=Mock(), capacity_snapshot_provider=lambda: active_ops)
    from holomed.runtime.service import ServiceState
    dcm._state = ServiceState.STARTED
    with pytest.raises(DeviceControlError, match="FATAL: Durable state corruption. Invalid lifecycle_generation"):
        dcm.preempt_by_correlation("C1")

def test_dcm_preempt_by_correlation_corruption_bool_lifecycle_generation() -> None:
    active_ops = {("dev1", 1, 1, "op1", "nonce1"): {"correlation_id": "C1", "execution_id": "E1", "_original_session_id": "sess_1", "endpoint_id": "ep1", "lifecycle_generation": True}}
    dcm = DeviceControlManager(registry=Mock(), capacity_snapshot_provider=lambda: active_ops)
    from holomed.runtime.service import ServiceState
    dcm._state = ServiceState.STARTED
    with pytest.raises(DeviceControlError, match="FATAL: Durable state corruption. Invalid lifecycle_generation"):
        dcm.preempt_by_correlation("C1")

    active_ops[("dev1", 1, 1, "op1", "nonce1")]["lifecycle_generation"] = False
    with pytest.raises(DeviceControlError, match="FATAL: Durable state corruption. Invalid lifecycle_generation"):
        dcm.preempt_by_correlation("C1")

def test_dcm_preempt_by_correlation_corruption_missing_device_id() -> None:
    # device_id is part of canonical tuple, but we should test if it's somehow None
    active_ops = {(None, 1, 1, "op1", "nonce1"): {"correlation_id": "C1", "execution_id": "E1", "_original_session_id": "sess_1", "endpoint_id": "ep1", "lifecycle_generation": 1}}
    dcm = DeviceControlManager(registry=Mock(), capacity_snapshot_provider=lambda: active_ops)
    from holomed.runtime.service import ServiceState
    dcm._state = ServiceState.STARTED
    with pytest.raises(DeviceControlError, match="FATAL: Durable state corruption. Incomplete record"):
        dcm.preempt_by_correlation("C1")

def test_dcm_preempt_by_correlation_corruption_missing_endpoint_id() -> None:
    active_ops = {("dev1", 1, 1, "op1", "nonce1"): {"correlation_id": "C1", "execution_id": "E1", "_original_session_id": "sess_1", "lifecycle_generation": 1}}
    dcm = DeviceControlManager(registry=Mock(), capacity_snapshot_provider=lambda: active_ops)
    from holomed.runtime.service import ServiceState
    dcm._state = ServiceState.STARTED
    with pytest.raises(DeviceControlError, match="FATAL: Durable state corruption. Incomplete record"):
        dcm.preempt_by_correlation("C1")

def test_dcm_preempt_by_correlation_corruption_missing_execution_id() -> None:
    active_ops = {("dev1", 1, 1, "op1", "nonce1"): {"correlation_id": "C1", "_original_session_id": "sess_1", "endpoint_id": "ep1", "lifecycle_generation": 1}}
    dcm = DeviceControlManager(registry=Mock(), capacity_snapshot_provider=lambda: active_ops)
    from holomed.runtime.service import ServiceState
    dcm._state = ServiceState.STARTED
    with pytest.raises(DeviceControlError, match="FATAL: Durable state corruption. Incomplete record"):
        dcm.preempt_by_correlation("C1")

def test_dcm_preempt_by_correlation_corruption_missing_session_id() -> None:
    active_ops = {("dev1", 1, 1, "op1", "nonce1"): {"correlation_id": "C1", "execution_id": "E1", "endpoint_id": "ep1", "lifecycle_generation": 1}}
    dcm = DeviceControlManager(registry=Mock(), capacity_snapshot_provider=lambda: active_ops)
    from holomed.runtime.service import ServiceState
    dcm._state = ServiceState.STARTED
    with pytest.raises(DeviceControlError, match="FATAL: Durable state corruption. Incomplete record"):
        dcm.preempt_by_correlation("C1")

def test_idempotency_bypasses_rejected(temp_storage_root: Path) -> None:
    """Tests scenarios B, C, D, G related to idempotency and correlation bypass."""
    auth = ControllerAuthorityStore(temp_storage_root)
    auth.allocate_next_epoch()
    epoch = auth.read_current_epoch()
    store = DurableSessionStore(temp_storage_root, epoch_id=epoch)
    store.start_session("sess_01", epoch_id=epoch)
    init_device_auth(store, "dev1")
    
    # 1. Admit C1 -> E1 (Scenario A)
    phys_op1, replay1, res1 = store.record_operation_admitted(
        session_id="sess_01",
        endpoint_id="ep1",
        device_id="dev1",
        device_epoch=1,
        controller_epoch=1,
        physical_operation_id=None,
        command_nonce="nonce1",
        correlation_id="corr_01",
        execution_id="E1",
        command_name="grasp",
        request_fingerprint="fp1"
    )
    
    # 2. Exact replay (Scenario B)
    phys_op2, replay2, res2 = store.record_operation_admitted(
        session_id="sess_01",
        endpoint_id="ep1",
        device_id="dev1",
        device_epoch=1,
        controller_epoch=1,
        physical_operation_id=phys_op1,
        command_nonce="nonce1",
        correlation_id="corr_01",
        execution_id="E1",
        command_name="grasp",
        request_fingerprint="fp1"
    )
    assert replay2 is True
    assert phys_op1 == phys_op2

    # 3. Same physical identity (sess+nonce) but DIFFERENT correlation C2 (Scenario C, G)
    from holomed.persistence.exceptions import PersistenceIdentityReuseError
    with pytest.raises(PersistenceIdentityReuseError, match="Idempotency bypass rejected: existing execution is bound to correlation corr_01 not corr_02"):
        store.record_operation_admitted(
            session_id="sess_01",
            endpoint_id="ep1",
            device_id="dev1",
            device_epoch=1,
            controller_epoch=1,
            physical_operation_id=phys_op1,
            command_nonce="nonce1",
            correlation_id="corr_02",
            execution_id="E1",
            command_name="grasp",
            request_fingerprint="fp1"
        )
        
    # 4. Same physical identity but DIFFERENT execution E2 (Scenario D)
    with pytest.raises(PersistenceIdentityReuseError, match="Idempotency bypass rejected: existing execution is bound to execution E1 not E2"):
        store.record_operation_admitted(
            session_id="sess_01",
            endpoint_id="ep1",
            device_id="dev1",
            device_epoch=1,
            controller_epoch=1,
            physical_operation_id=phys_op1,
            command_nonce="nonce1",
            correlation_id="corr_01",
            execution_id="E2",
            command_name="grasp",
            request_fingerprint="fp1"
        )

def test_dcm_handle_command_correlation_conflict() -> None:
    from unittest.mock import Mock
    from holomed.devices.control.manager import DeviceControlManager
    from holomed.runtime.service import ServiceState
    from holomed.protocol.models import MessageEnvelope, MessageType

    dcm = DeviceControlManager(registry=Mock(), capacity_snapshot_provider=lambda: {})
    dcm._state = ServiceState.STARTED

    from datetime import datetime, timezone
    envelope = MessageEnvelope(
        message_id="00000000-0000-0000-0000-000000000001",
        correlation_id="00000000-0000-0000-0000-000000000002",
        target="tgt",
        source="src",
        message_type=MessageType.COMMAND,
        protocol_version="1.0",
        causation_id="00000000-0000-0000-0000-000000000000",
        message_name="some_command",
        timestamp_utc=datetime.now(timezone.utc),
        metadata={},
        payload={
            "session_id": "sess1",
            "device_id": "dev1",
            "command": "some_command",
            "session_lifecycle_generation": 1,
            "execution_id": "exec1",
            "command_nonce": "nonce1",
            "correlation_id": "00000000-0000-0000-0000-000000000003"  # Conflicting correlation ID
        }
    )

    response = dcm.handle_command(envelope)
    
    assert response.message_type == MessageType.ERROR
    assert response.payload["error_code"] == "ERR_VALIDATION_ERROR"
    assert "Envelope correlation_id and payload correlation_id conflict" in response.payload["error_message"]
