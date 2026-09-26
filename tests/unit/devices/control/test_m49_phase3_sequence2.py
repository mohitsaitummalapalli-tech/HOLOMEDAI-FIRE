import pytest
import uuid
import threading
import time
from typing import Optional

from holomed.devices.control.manager import DeviceControlManager, AdmissionState
from holomed.configuration.models import AppConfig, EnvironmentProfile, LogLevel
from holomed.devices.registry import DeviceRegistry, RegistryAuthorityToken
from holomed.runtime.context import RuntimeContext
from holomed.runtime.service import ServiceState
from holomed.runtime.exceptions import ServiceLifecycleError
from holomed.devices.control.recovery import StateRehydrationEngine
from holomed.devices.interfaces import IDevice, IPhysicalEndpoint
from holomed.devices.models import DeviceType
from holomed.protocol.models import MessageEnvelope, MessageType
from holomed.devices.control.models import DeviceCommandDefinition
from holomed.devices.simulated import SimulatedDevice
from holomed.devices.models import DeviceCapability, CapabilityCategory, DeviceState

class MockRehydrationEngine:
    def __init__(self, should_fail: bool = False, entry_event: Optional[threading.Event] = None, exit_event: Optional[threading.Event] = None):
        self.should_fail = should_fail
        self.entry_event = entry_event
        self.exit_event = exit_event
        self.rehydrate_controller_called = False
        self.rehydrate_device_called = False
        
    def rehydrate_controller_state(self, current_session_id: str) -> None:
        if self.entry_event:
            self.entry_event.set()
        if self.exit_event:
            self.exit_event.wait(timeout=5.0)
            
        if self.should_fail:
            raise Exception("Mock rehydration failure")
        self.rehydrate_controller_called = True
        
    def rehydrate_device_state(self, current_session_id: str, device_id: str, new_device_epoch: int) -> None:
        if self.should_fail:
            raise Exception("Mock device rehydration failure")
        self.rehydrate_device_called = True

def test_startup_rehydration_success():
    """Verify DeviceControlManager properly establishes readiness gates on successful start."""
    registry = DeviceRegistry(RegistryAuthorityToken())
    engine = MockRehydrationEngine()
    
    manager = DeviceControlManager(
        registry=registry,
        authoritative_epoch_provider=lambda: 42,
        rehydration_engine=engine  # type: ignore
    )
    
    config = AppConfig(
        app_name="Test",
        environment=EnvironmentProfile.TESTING,
        host="127.0.0.1",
        port=8090,
        log_level=LogLevel.DEBUG,
        gemini_api_key=None,
        protocol_version="1.0"
    )
    context = RuntimeContext(app_config=config, epoch_id=42)
    manager.initialize(context)
    assert manager._state == ServiceState.INITIALIZED
    
    manager.start()
    
    assert engine.rehydrate_controller_called
    assert manager._state == ServiceState.STARTED
    assert manager._admission_state == AdmissionState.READY

def test_startup_rehydration_failure():
    """Verify DeviceControlManager fails closed if rehydration fails."""
    registry = DeviceRegistry(RegistryAuthorityToken())
    engine = MockRehydrationEngine(should_fail=True)
    
    manager = DeviceControlManager(
        registry=registry,
        authoritative_epoch_provider=lambda: 42,
        rehydration_engine=engine  # type: ignore
    )
    
    config = AppConfig(
        app_name="Test",
        environment=EnvironmentProfile.TESTING,
        host="127.0.0.1",
        port=8090,
        log_level=LogLevel.DEBUG,
        gemini_api_key=None,
        protocol_version="1.0"
    )
    context = RuntimeContext(app_config=config, epoch_id=42)
    manager.initialize(context)
    
    with pytest.raises(ServiceLifecycleError, match="Startup rehydration failed"):
        manager.start()
        
    assert manager._state == ServiceState.FAILED
    assert manager._admission_state == AdmissionState.FAILED

def test_startup_rehydration_missing_epoch():
    """Verify DeviceControlManager fails closed if authoritative epoch is missing."""
    registry = DeviceRegistry(RegistryAuthorityToken())
    engine = MockRehydrationEngine()
    
    manager = DeviceControlManager(
        registry=registry,
        authoritative_epoch_provider=lambda: None, # type: ignore
        rehydration_engine=engine  # type: ignore
    )
    
    config = AppConfig(
        app_name="Test",
        environment=EnvironmentProfile.TESTING,
        host="127.0.0.1",
        port=8090,
        log_level=LogLevel.DEBUG,
        gemini_api_key=None,
        protocol_version="1.0"
    )
    context = RuntimeContext(app_config=config, epoch_id=42)
    manager.initialize(context)
    
    with pytest.raises(ServiceLifecycleError, match="Authoritative epoch missing"):
        manager.start()
        
    assert manager._state == ServiceState.FAILED
    assert manager._admission_state == AdmissionState.FAILED

def test_concurrent_admission_race():
    """Verify physical commands are rejected while rehydration is in progress."""
    registry = DeviceRegistry(RegistryAuthorityToken())
    entry_event = threading.Event()
    exit_event = threading.Event()
    engine = MockRehydrationEngine(entry_event=entry_event, exit_event=exit_event)
    
    manager = DeviceControlManager(
        registry=registry,
        authoritative_epoch_provider=lambda: 42,
        rehydration_engine=engine  # type: ignore
    )
    
    config = AppConfig(
        app_name="Test",
        environment=EnvironmentProfile.TESTING,
        host="127.0.0.1",
        port=8090,
        log_level=LogLevel.DEBUG,
        gemini_api_key=None,
        protocol_version="1.0"
    )
    context = RuntimeContext(app_config=config, epoch_id=42)
    manager.initialize(context)
    
    # We will start the manager in a background thread and attempt to admit a command while it is rehydrating
    manager_start_thread = threading.Thread(target=manager.start)
    manager_start_thread.start()
    
    # Wait for the manager to enter REHYDRATING state
    assert entry_event.wait(timeout=5.0), "Timeout waiting for rehydration to start"
    
    assert manager._admission_state == AdmissionState.REHYDRATING
    
    # Register device with capability
    device = SimulatedDevice("dev1", "phys1")
    device._state = DeviceState.ACTIVE
    
    from holomed.devices.simulated import SimulatedPhysicalEndpoint
    device._endpoints = (SimulatedPhysicalEndpoint("phys1", "dev1"),)
    device._capabilities = tuple(list(device._capabilities) + [
        DeviceCapability(
            capability_id="cap1",
            category=CapabilityCategory.CONTROL,
            parameters={},
            requires_physical_endpoint=True,
            target_endpoint_id="phys1"
        )
    ])
    registry._devices["dev1"] = device

    # Register a command definition requiring physical endpoint
    manager.register_command(
        command_name="test_cmd",
        handler=lambda d, p: None,
        required_capability_id="cap1",
    )
    
    envelope = MessageEnvelope(
        protocol_version="1.0",
        message_id=str(uuid.uuid4()),
        correlation_id=str(uuid.uuid4()),
        causation_id=None,
        message_type=MessageType.COMMAND,
        message_name="device.command",
        source="client",
        target="manager",
        timestamp_utc="2026-01-01T00:00:00Z",
        payload={
            "device_id": "dev1",
            "command": "test_cmd",
            "parameters": {},
            "session_id": "sess1",
            "execution_id": "exec1", "command_nonce": "nonce_1", "command_nonce": "nonce_1",
            "session_lifecycle_generation": 1
        },
        metadata={}
    )
    
    # The command should fail admission because the gate is not READY
    response = manager.handle_command(envelope)
    
    assert response.message_type == "ERROR"
    assert response.payload["error_code"] == "ERR_CONTROL_NOT_READY"
    assert "Physical admission forbidden: control plane is REHYDRATING" in response.payload["error_message"]
    
    # Allow rehydration to finish
    exit_event.set()
    
    # Wait for rehydration to finish
    manager_start_thread.join(timeout=5.0)
    assert manager._admission_state == AdmissionState.READY

def test_device_restart_quarantine():
    """Verify handle_device_restart invokes the rehydration engine for the device."""
    registry = DeviceRegistry(RegistryAuthorityToken())
    engine = MockRehydrationEngine()
    
    manager = DeviceControlManager(
        registry=registry,
        authoritative_epoch_provider=lambda: 42,
        rehydration_engine=engine  # type: ignore
    )
    
    manager.handle_device_restart("dev1", 5)
    
    assert engine.rehydrate_device_called
