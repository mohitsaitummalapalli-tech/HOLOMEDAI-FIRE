import os
import time
import json
import uuid
import pytest
import multiprocessing
import threading
from pathlib import Path
from unittest.mock import patch, MagicMock
import sys

_PYTHON_SRC = Path(__file__).resolve().parents[4] / "python"
if str(_PYTHON_SRC) not in sys.path:
    sys.path.insert(0, str(_PYTHON_SRC))

from holomed.persistence.sessions import DurableSessionStore, SessionStatus
from holomed.persistence.authority import ControllerAuthorityStore
from holomed.devices.control.exceptions import CapabilityUnauthorizedError
from holomed.devices.models import DeviceState
from holomed.devices.control.manager import DeviceControlManager

def lock_storage(tmp_path: Path) -> Path:
    storage = tmp_path / "matrix_cross_process_store"
    storage.mkdir(parents=True, exist_ok=True)
    return storage

def _setup_store(storage_path: Path) -> tuple[ControllerAuthorityStore, int, str]:
    authority = ControllerAuthorityStore(storage_path)
    epoch = authority.allocate_next_epoch()
    session = "matrix_sess_1"
    store = DurableSessionStore(storage_path, epoch_id=epoch)
    store.start_session(session, epoch)
    return authority, epoch, session


def _worker_d_crash_after_epoch(storage_str: str, epoch: int, session: str, q: multiprocessing.Queue):
    try:
        sys.path.insert(0, str(_PYTHON_SRC))
        from holomed.persistence.sessions import DurableSessionStore
        
        store = DurableSessionStore(Path(storage_str), epoch_id=epoch)
        store.restore_session_from_disk(session)
        
        original_acquire = store._acquire_global_lock
        def _mock_acquire(*args, **kwargs):
            q.put(("VALIDATED", os.getpid())); time.sleep(0.1)
            os._exit(0)
            
        with patch.object(store, '_acquire_global_lock', _mock_acquire):
            store.record_operation_admitted(session, "ep1", "dev1", 1, epoch, "op1", "nonce1", "exec1", "cmd1")
            
    except Exception as e:
        q.put(("ERROR", str(type(e)) + ": " + str(e)))


def _worker_e_crash_before_fsync(storage_str: str, epoch: int, session: str, q: multiprocessing.Queue):
    try:
        sys.path.insert(0, str(_PYTHON_SRC))
        from holomed.persistence.sessions import DurableSessionStore
        import holomed.persistence.journal
        import os
        
        store = DurableSessionStore(Path(storage_str), epoch_id=epoch)
        store.restore_session_from_disk(session)
        
        original_fsync = os.fsync
        def _mock_fsync(fd):
            q.put(("FLUSHED_BEFORE_FSYNC", os.getpid())); time.sleep(0.1)
            os._exit(0)
            
        with patch('holomed.persistence.journal.os.fsync', _mock_fsync):
            store.record_operation_admitted(session, "ep1", "dev1", 1, epoch, "op1", "nonce1", "exec1", "cmd1")
            
    except Exception as e:
        q.put(("ERROR", str(type(e)) + ": " + str(e)))


def _worker_f_crash_after_fsync(storage_str: str, epoch: int, session: str, q: multiprocessing.Queue):
    try:
        sys.path.insert(0, str(_PYTHON_SRC))
        from holomed.persistence.sessions import DurableSessionStore
        import holomed.persistence.journal
        import os
        
        store = DurableSessionStore(Path(storage_str), epoch_id=epoch)
        store.restore_session_from_disk(session)
        
        original_fsync = os.fsync
        def _mock_fsync(fd):
            original_fsync(fd)
            q.put(("FSYNC_COMPLETED", os.getpid())); time.sleep(0.1)
            os._exit(0)
            
        with patch('holomed.persistence.journal.os.fsync', _mock_fsync):
            store.record_operation_admitted(session, "ep1", "dev1", 1, epoch, "op1", "nonce1", "exec1", "cmd1")
            
    except Exception as e:
        q.put(("ERROR", str(type(e)) + ": " + str(e)))


class TestM49Phase63FailureMatrixCrossProcess:
    
    def test_d_crash_after_epoch_validation(self, tmp_path: Path):
        storage = lock_storage(tmp_path)
        authority, epoch, session = _setup_store(storage)
        store = DurableSessionStore(storage, epoch_id=epoch)
        store.start_session(session, epoch)
        
        ctx = multiprocessing.get_context("spawn")
        q = ctx.Queue()
        p = ctx.Process(target=_worker_d_crash_after_epoch, args=(str(storage), epoch, session, q))
        p.start()
        p.join(timeout=10)
        
        msgs = []
        while not q.empty():
            msgs.append(q.get())
            
        assert any(m[0] == "VALIDATED" for m in msgs), f"Did not reach validation, got {msgs}"
        
        store = DurableSessionStore(storage, epoch_id=epoch)
        store.restore_session_from_disk(session)
        
        assert store.get_active_physical_operations() == 0
        
    def test_e_crash_after_append_before_fsync(self, tmp_path: Path):
        storage = lock_storage(tmp_path)
        authority, epoch, session = _setup_store(storage)
        store = DurableSessionStore(storage, epoch_id=epoch)
        store.start_session(session, epoch)
        
        ctx = multiprocessing.get_context("spawn")
        q = ctx.Queue()
        p = ctx.Process(target=_worker_e_crash_before_fsync, args=(str(storage), epoch, session, q))
        p.start()
        p.join(timeout=10)
        
        msgs = []
        while not q.empty():
            msgs.append(q.get())
            
        assert any(m[0] == "FLUSHED_BEFORE_FSYNC" for m in msgs), f"Did not hook fsync, got {msgs}"
        
        store = DurableSessionStore(storage, epoch_id=epoch)
        store.restore_session_from_disk(session)
        count = store.get_active_physical_operations()
        assert count in (0, 1)

    def test_f_crash_after_fsync(self, tmp_path: Path):
        storage = lock_storage(tmp_path)
        authority, epoch, session = _setup_store(storage)
        store = DurableSessionStore(storage, epoch_id=epoch)
        store.start_session(session, epoch)
        
        ctx = multiprocessing.get_context("spawn")
        q = ctx.Queue()
        p = ctx.Process(target=_worker_f_crash_after_fsync, args=(str(storage), epoch, session, q))
        p.start()
        p.join(timeout=10)
        
        msgs = []
        while not q.empty():
            msgs.append(q.get())
            
        assert any(m[0] == "FSYNC_COMPLETED" for m in msgs), f"Did not complete fsync, got {msgs}"
        
        store = DurableSessionStore(storage, epoch_id=epoch)
        store.restore_session_from_disk(session)
        
        assert store.get_active_physical_operations() == 1
        
        op_id, is_replay, resolution = store.record_operation_admitted(
            session, "ep1", "dev1", 1, epoch, "op1", "nonce1", "exec1", "cmd1"
        )
        assert is_replay is True
        assert resolution is None

    def test_s_isolation_racing_admission(self, tmp_path: Path):
        from holomed.devices.registry import DeviceRegistry, RegistryAuthorityToken
        from holomed.devices.simulated import SimulatedDevice, SimulatedPhysicalEndpoint
        from holomed.devices.models import DeviceCapability, CapabilityCategory
        from holomed.protocol.models import MessageEnvelope, MessageType

        storage = lock_storage(tmp_path)
        authority, epoch, session = _setup_store(storage)
        store = DurableSessionStore(storage, epoch_id=epoch)
        store.start_session(session, epoch)

        cap = DeviceCapability(capability_id="test.cap", category=CapabilityCategory.CONTROL, parameters={}, requires_physical_endpoint=True, target_endpoint_id="ep_s")
        device = SimulatedDevice("dev_s", "phys_s", capabilities=(cap,))
        endpoint = SimulatedPhysicalEndpoint("ep_s", "dev_s")
        device.set_endpoints((endpoint,))
        
        token = RegistryAuthorityToken()
        registry = DeviceRegistry(token)
        registry.register(device, token)
        device._state = DeviceState.ACTIVE

        manager = DeviceControlManager(
            registry=registry,
            session_validator=lambda s, l: True,
            capacity_admitter=store.record_operation_admitted,
            authoritative_epoch_provider=lambda: epoch,
            rehydration_engine=MagicMock()
        )
        manager.register_command("cmd_s", handler=lambda cmd, p: None, required_capability_id="test.cap")
        ctx = MagicMock()
        manager.initialize(ctx)
        manager.start()

        manager._lease_registry.issue_lease(endpoint, session, 1, "exec_s", frozenset(["test.cap"]))

        envelope = MessageEnvelope(
            protocol_version="1.0", message_id=str(uuid.uuid4()), correlation_id=str(uuid.uuid4()), causation_id=None,
            message_type=MessageType.COMMAND, message_name="device.command", source="client", target="control", timestamp_utc="2026-01-01T00:00:00Z",
            payload={"session_id": session, "execution_id": "exec_s", "command_nonce": "nonce_s", "session_lifecycle_generation": 1, "command": "cmd_s", "parameters": {}, "device_id": "dev_s"},
            metadata={}
        )

        barrier1 = threading.Barrier(2)
        barrier2 = threading.Barrier(2)

        original_verify = manager._verifier.verify_command_authorization
        def mock_verify(*args, **kwargs):
            barrier1.wait() 
            barrier2.wait() 
            return original_verify(*args, **kwargs)

        res_a = []
        def process_a():
            with patch.object(manager._verifier, 'verify_command_authorization', mock_verify):
                res = manager.handle_command(envelope)
                res_a.append(res)

        t_a = threading.Thread(target=process_a)
        t_a.start()

        barrier1.wait() 
        manager.quarantine_device("dev_s") 
        barrier2.wait() 

        t_a.join(timeout=5)
        assert res_a[0].payload["error_code"] == "ERR_CONTROL_NOT_READY", res_a[0].payload
        

    def test_t_reinitialization_race_stale_controller(self, tmp_path: Path):
        from holomed.devices.registry import DeviceRegistry, RegistryAuthorityToken
        from holomed.devices.simulated import SimulatedDevice, SimulatedPhysicalEndpoint
        from holomed.devices.models import DeviceCapability, CapabilityCategory
        from holomed.protocol.models import MessageEnvelope, MessageType

        storage = lock_storage(tmp_path)
        authority, epoch, session = _setup_store(storage)
        store = DurableSessionStore(storage, epoch_id=epoch)
        store.start_session(session, epoch)

        cap = DeviceCapability(capability_id="test.cap", category=CapabilityCategory.CONTROL, parameters={}, requires_physical_endpoint=True, target_endpoint_id="ep_t")
        device = SimulatedDevice("dev_t", "phys_t", capabilities=(cap,))
        endpoint = SimulatedPhysicalEndpoint("ep_t", "dev_t")
        device.set_endpoints((endpoint,))
        
        token = RegistryAuthorityToken()
        registry = DeviceRegistry(token)
        registry.register(device, token)
        device._state = DeviceState.ACTIVE

        manager = DeviceControlManager(
            registry=registry,
            session_validator=lambda s, l: True,
            capacity_admitter=store.record_operation_admitted,
            authoritative_epoch_provider=lambda: epoch,
            rehydration_engine=MagicMock()
        )
        manager.register_command("cmd_t", handler=lambda cmd, p: None, required_capability_id="test.cap")
        ctx = MagicMock()
        manager.initialize(ctx)
        manager.start()

        manager._lease_registry.issue_lease(endpoint, session, 1, "exec_t", frozenset(["test.cap"]))

        envelope = MessageEnvelope(
            protocol_version="1.0", message_id=str(uuid.uuid4()), correlation_id=str(uuid.uuid4()), causation_id=None,
            message_type=MessageType.COMMAND, message_name="device.command", source="client", target="control", timestamp_utc="2026-01-01T00:00:00Z",
            payload={"session_id": session, "execution_id": "exec_t", "command_nonce": "nonce_t", "session_lifecycle_generation": 1, "command": "cmd_t", "parameters": {}, "device_id": "dev_t"},
            metadata={}
        )

        barrier1 = threading.Barrier(2)
        barrier2 = threading.Barrier(2)

        original_verify = manager._verifier.verify_command_authorization
        def mock_verify(*args, **kwargs):
            barrier1.wait() 
            barrier2.wait() 
            return original_verify(*args, **kwargs)

        res_a = []
        def process_a():
            with patch.object(manager._verifier, 'verify_command_authorization', mock_verify):
                res = manager.handle_command(envelope)
                res_a.append(res)

        t_a = threading.Thread(target=process_a)
        t_a.start()

        barrier1.wait() 
        SimulatedDevice.current_epoch = property(lambda self: getattr(self, '_epoch', 1), lambda self, v: setattr(self, '_epoch', v)) # type: ignore
        manager.quarantine_device("dev_t")
        manager.recover_device("dev_t", MagicMock(commit_device_ready=MagicMock(return_value=epoch+1)), {})
        barrier2.wait() 

        t_a.join(timeout=5)
        assert res_a[0].payload["error_code"] == "ERR_CAPABILITYUNAUTHORIZEDERROR", res_a[0].payload

