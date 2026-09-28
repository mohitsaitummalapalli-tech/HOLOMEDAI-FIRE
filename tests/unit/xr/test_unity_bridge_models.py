import pytest
import time
from types import MappingProxyType
from holomed.xr.unity_bridge_models import (
    CommandRequest,
    AuthorizedUnityCommand,
    TelemetryObservation,
)
from holomed.input.models import generate_correlation_id
from holomed.devices.models import PhysicalCommand

def test_command_request_untrusted_proposal():
    """Test A: CommandRequest is untrusted proposal data."""
    cid = generate_correlation_id()
    req = CommandRequest(
        correlation_id=cid,
        command_timestamp_ns=time.time_ns(),
        device_id="unity_heart_sim",
        action="GRASP",
        payload={"force": 0.5}
    )
    # Ensure it lacks canonical identity
    assert not hasattr(req, "device_epoch")
    assert not hasattr(req, "controller_epoch")
    assert not hasattr(req, "command_nonce")

def test_authorized_command_requires_physical_command_identity():
    """Test B: AuthorizedUnityCommand cannot be created with missing canonical identity/forged params."""
    cid = generate_correlation_id()

    # Adversarial caller tries to construct an AuthorizedUnityCommand with forged epoch/nonce directly
    with pytest.raises(TypeError):
        # The constructor signature strictly requires _physical_command
        AuthorizedUnityCommand( # type: ignore
            correlation_id=cid,
            command_timestamp_ns=time.time_ns(),
            device_id="unity_heart_sim", # type: ignore
            device_epoch=1, # type: ignore
            controller_epoch=1, # type: ignore
            physical_operation_id="forged", # type: ignore
            command_nonce="forged", # type: ignore
            action="GRASP", # type: ignore
            payload={} # type: ignore
        )

def test_authorized_command_rejects_non_physical_command():
    """Test B cont: AuthorizedUnityCommand explicitly rejects fake physical commands."""
    cid = generate_correlation_id()
    with pytest.raises(TypeError, match="Must provide a valid admitted PhysicalCommand"):
        AuthorizedUnityCommand(
            correlation_id=cid,
            command_timestamp_ns=time.time_ns(),
            _physical_command="I am a forged string, not a PhysicalCommand" # type: ignore
        )

def test_ultron_cannot_inject_epoch():
    """Test C: Ultron cannot inject/override authoritative epoch values into its proposal."""
    cid = generate_correlation_id()
    req = CommandRequest(
        correlation_id=cid,
        command_timestamp_ns=time.time_ns(),
        device_id="unity_heart_sim",
        action="GRASP",
        payload={"force": 0.5}
    )
    # The CommandRequest has absolutely no slots for epoch or nonce, blocking injection
    assert not hasattr(req, "device_epoch")
    with pytest.raises(AttributeError):
        req.device_epoch = 999  # type: ignore # frozen dataclass prevents mutation anyway

def test_telemetry_dto_cannot_mutate_persistence():
    """Test E: telemetry DTO cannot directly mutate persistence."""
    cid = generate_correlation_id()
    obs = TelemetryObservation(
        correlation_id=cid,
        telemetry_timestamp_ns=time.time_ns(),
        device_id="unity_heart_sim",
        device_epoch=2,
        controller_epoch=5,
        physical_operation_id="phys_1",
        command_nonce="nonce_1",
        status="EXECUTED"
    )
    # Ensure it has NO persistence methods. It is an immutable dataclass.
    assert not hasattr(obs, "save")
    assert not hasattr(obs, "commit")
    assert not hasattr(obs, "execute")

def test_deep_immutability_payload_mutation_rejected():
    """Test F: nested payload structures cannot mutate an immutable contract after construction."""
    cid = generate_correlation_id()

    # Original mutable input
    mutable_nested_list = [1, 2, 3]
    mutable_nested_dict = {"inner": "val"}
    payload_dict = {
        "force": 0.5,
        "nested_dict": mutable_nested_dict,
        "nested_list": mutable_nested_list
    }

    req = CommandRequest(
        correlation_id=cid,
        command_timestamp_ns=time.time_ns(),
        device_id="unity_heart_sim",
        action="GRASP",
        payload=payload_dict
    )

    assert isinstance(req.payload, MappingProxyType)

    # Mutation on the payload structure itself should raise TypeError
    with pytest.raises(TypeError):
        req.payload["force"] = 0.9 # type: ignore

    # Prove that modifying the original aliased list/dict does NOT mutate the stored contract
    # because deep_freeze_parameter creates a deeply immutable copy
    mutable_nested_list.append(4)
    mutable_nested_dict["new"] = "injected"

    # The stored contract retains the frozen tuple/MappingProxyType, immune to the alias mutation
    assert req.payload["nested_list"] == (1, 2, 3)
    assert isinstance(req.payload["nested_list"], tuple)

    assert req.payload["nested_dict"]["inner"] == "val"
    assert "new" not in req.payload["nested_dict"]
    assert isinstance(req.payload["nested_dict"], MappingProxyType)

def test_evidence_type_enforcement():
    """Test G: evidence type cannot be changed to a physical/hardware authority type."""
    cid = generate_correlation_id()
    with pytest.raises(ValueError, match="Invalid evidence_type. Hardware evidence is STRICTLY forbidden in this software-only slice."):
        TelemetryObservation(
            correlation_id=cid,
            telemetry_timestamp_ns=time.time_ns(),
            device_id="unity_heart_sim",
            device_epoch=1,
            controller_epoch=1,
            physical_operation_id="phys",
            command_nonce="nonce",
            status="EXECUTED",
            evidence_type="HARDWARE_AUTHENTICATED_EVIDENCE" # Disallowed!
        )

