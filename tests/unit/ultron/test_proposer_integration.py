"""Integration tests for Ultron Proposal Boundary with DeviceControlManager."""

import time
import pytest
from typing import Mapping, Any

from holomed.input.models import UltronIntent
from holomed.input.intent import ACTION_GRASP
from holomed.ultron.proposer import UltronProposer, create_envelope_from_proposal
from holomed.devices.control.manager import DeviceControlManager
from holomed.devices.registry import DeviceRegistry
from holomed.devices.models import DeviceDescriptor, DeviceCapability
from holomed.core.models import MessageEnvelope
from holomed.protocol.models import MessageType
from holomed.devices.control.manager import AdmissionState
from holomed.runtime.context import RuntimeContext

# --- Mocks for Control Manager ---

def _mock_command_handler(device: Any, payload: Mapping[str, Any]) -> Any:
    # A dummy response, DeviceControlManager just returns whatever it generates internally
    pass


@pytest.fixture
def device_registry():
    from unittest.mock import MagicMock
    from holomed.devices.models import DeviceType, DeviceState, CapabilityCategory, DeviceCapability
    from holomed.devices.interfaces import IDevice, IPhysicalEndpoint

    device = MagicMock(spec=IDevice)
    device.device_id = "dev_xr_headset_01"
    device.physical_id = "phys_01"
    device.device_type = DeviceType.SIMULATED_GENERIC
    device.state = DeviceState.UNREGISTERED
    device.current_epoch = 0

    cap = DeviceCapability(
        capability_id="sys.interaction",
        category=CapabilityCategory.CONTROL,
        parameters={},
        requires_physical_endpoint=True,
        target_endpoint_id="ep_hand_tracking",
    )
    device.capabilities = (cap,)

    ep = MagicMock(spec=IPhysicalEndpoint)
    ep.endpoint_id = "ep_hand_tracking"
    ep.device_id = "dev_xr_headset_01"
    ep.protocol = "internal"
    ep.capabilities = frozenset(["sys.interaction"])

    from holomed.devices.models import SubmissionStatus
    res_mock = MagicMock()
    res_mock.status = SubmissionStatus.ACCEPTED
    res_mock.details = {"status": "SUCCESS"}
    ep.submit_command.return_value = res_mock

    device.endpoints = (ep,)

    # Needs valid token to register
    tok = MagicMock()
    registry = DeviceRegistry(token=tok)
    registry.register(device, token=tok)
    device.state = DeviceState.ACTIVE

    return registry


@pytest.fixture
def control_manager(device_registry):
    manager = DeviceControlManager(
        registry=device_registry,
        secret_filter=None,
        authoritative_epoch_provider=lambda: 100
    )
    import uuid
    manager._capacity_admitter = lambda *args, **kwargs: (str(uuid.uuid4()), False, None)

    # We need to stub start dependencies
    class DummyRehydration:
        def rehydrate_controller_state(self, current_session_id): pass
        def wait_until_ready(self, timeout): return True

    manager._rehydration_engine = DummyRehydration()  # type: ignore
    from unittest.mock import MagicMock
    manager.initialize(RuntimeContext(epoch_id=100, app_config=MagicMock()))
    manager.start()

    # Needs a handler for our generated sys.input.interact command
    from holomed.devices.control.models import DeviceCommandDefinition
    manager._commands["sys.input.interact"] = DeviceCommandDefinition(
        command_name="sys.input.interact",
        handler=_mock_command_handler,
        required_capability_id="sys.interaction",
    )

    return manager

# --- Tests ---

def test_manager_admission_valid_command(control_manager):
    # Setup session validator to allow it
    control_manager._session_validator = lambda sid, gen: True

    # Allocate a lease
    device = control_manager._registry.get("dev_xr_headset_01")
    ep = device.endpoints[0]
    control_manager._lease_registry.issue_lease(ep, "00000000-0000-0000-0000-000000000003", 1, "00000000-0000-0000-0000-000000000002", frozenset(["sys.interaction"]))

    intent = UltronIntent("00000000-0000-0000-0000-000000000001", 0, ACTION_GRASP, 1.0, 1.0)
    proposal = UltronProposer().process_intent(intent, "dev_xr_headset_01")
    assert proposal is not None
    env = create_envelope_from_proposal(proposal, "00000000-0000-0000-0000-000000000003", 1, "00000000-0000-0000-0000-000000000002", "00000000-0000-0000-0000-000000000004")

    response = control_manager.handle_command(env)
    assert response.message_type == MessageType.RESPONSE
    assert response.payload.get("status") == "SUCCESS"
    assert response.correlation_id == "00000000-0000-0000-0000-000000000001"

def test_manager_admission_rejects_lease_conflict(control_manager):
    control_manager._session_validator = lambda sid, gen: True

    device = control_manager._registry.get("dev_xr_headset_01")
    ep = device.endpoints[0]

    # Issue a lease to a different session to create a conflict
    control_manager._lease_registry.issue_lease(ep, "different-session", 1, "exec-different", frozenset(["sys.interaction"]))

    intent = UltronIntent("00000000-0000-0000-0000-000000000001", 0, ACTION_GRASP, 1.0, 1.0)
    proposal = UltronProposer().process_intent(intent, "dev_xr_headset_01")
    assert proposal is not None
    env = create_envelope_from_proposal(proposal, "00000000-0000-0000-0000-000000000003", 1, "00000000-0000-0000-0000-000000000002", "00000000-0000-0000-0000-000000000004")

    response = control_manager.handle_command(env)
    assert response.message_type == MessageType.ERROR
    assert "ERR_CONTROLCAPACITYERROR" in response.payload["error_code"]

def test_manager_admission_rejects_unauthorized_session(control_manager):
    # Session validator returns False (stale/revoked session)
    control_manager._session_validator = lambda sid, gen: False

    # Has a lease but session is unauthorized
    device = control_manager._registry.get("dev_xr_headset_01")
    ep = device.endpoints[0]
    control_manager._lease_registry.issue_lease(ep, "00000000-0000-0000-0000-000000000003", 1, "00000000-0000-0000-0000-000000000002", frozenset(["sys.interaction"]))

    intent = UltronIntent("00000000-0000-0000-0000-000000000001", 0, ACTION_GRASP, 1.0, 1.0)
    proposal = UltronProposer().process_intent(intent, "dev_xr_headset_01")
    assert proposal is not None
    env = create_envelope_from_proposal(proposal, "00000000-0000-0000-0000-000000000003", 1, "00000000-0000-0000-0000-000000000002", "00000000-0000-0000-0000-000000000004")

    response = control_manager.handle_command(env)
    assert response.message_type == MessageType.ERROR
    assert "ERR_CAPABILITYUNAUTHORIZEDERROR" in response.payload["error_code"]

def test_manager_admission_rejects_unavailable_endpoint(control_manager):
    control_manager._session_validator = lambda sid, gen: True

    # Target invalid device
    intent = UltronIntent("00000000-0000-0000-0000-000000000001", 0, ACTION_GRASP, 1.0, 1.0)
    proposal = UltronProposer().process_intent(intent, "dev_invalid")
    assert proposal is not None
    env = create_envelope_from_proposal(proposal, "00000000-0000-0000-0000-000000000003", 1, "00000000-0000-0000-0000-000000000002", "00000000-0000-0000-0000-000000000004")

    response = control_manager.handle_command(env)
    assert response.message_type == MessageType.ERROR
    assert "ERR_DEVICE_NOT_FOUND" in response.payload["error_code"]

def test_manager_admission_idempotency_duplicate_proposal(control_manager):
    control_manager._session_validator = lambda sid, gen: True
    device = control_manager._registry.get("dev_xr_headset_01")
    control_manager._lease_registry.issue_lease(device.endpoints[0], "00000000-0000-0000-0000-000000000003", 1, "00000000-0000-0000-0000-000000000002", frozenset(["sys.interaction"]))

    intent = UltronIntent("00000000-0000-0000-0000-000000000001", 0, ACTION_GRASP, 1.0, 1.0)
    proposal = UltronProposer().process_intent(intent, "dev_xr_headset_01")
    assert proposal is not None
    env = create_envelope_from_proposal(proposal, "00000000-0000-0000-0000-000000000003", 1, "00000000-0000-0000-0000-000000000002", "00000000-0000-0000-0000-000000000004")

    response1 = control_manager.handle_command(env)
    assert response1.payload.get("status") == "SUCCESS"

    # Second duplicate admission (same envelope message_id)
    response2 = control_manager.handle_command(env)
    assert response2.payload.get("status") == "SUCCESS"
    assert response2.correlation_id == response1.correlation_id

def test_manager_rejects_capacity_exhausted(control_manager):
    # Simulate capacity exhaustion
    control_manager._session_validator = lambda sid, gen: True
    device = control_manager._registry.get("dev_xr_headset_01")
    control_manager._lease_registry.issue_lease(device.endpoints[0], "00000000-0000-0000-0000-000000000003", 1, "00000000-0000-0000-0000-000000000002", frozenset(["sys.interaction"]))

    # Override capacity_admitter to raise ControlCapacityError
    def mock_capacity_admitter(*args, **kwargs):
        from holomed.devices.control.exceptions import ControlCapacityError
        raise ControlCapacityError("Capacity exhausted")
    control_manager._capacity_admitter = mock_capacity_admitter

    intent = UltronIntent("00000000-0000-0000-0000-000000000001", 0, ACTION_GRASP, 1.0, 1.0)
    proposal = UltronProposer().process_intent(intent, "dev_xr_headset_01")
    assert proposal is not None
    env = create_envelope_from_proposal(proposal, "00000000-0000-0000-0000-000000000003", 1, "00000000-0000-0000-0000-000000000002", "00000000-0000-0000-0000-000000000004")

    response = control_manager.handle_command(env)
    assert response.message_type == MessageType.ERROR
    assert "ERR_CONTROLCAPACITYERROR" in response.payload["error_code"]
