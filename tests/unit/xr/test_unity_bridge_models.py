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

import pytest
import time
from types import MappingProxyType
import secrets
import hmac
import hashlib
from holomed.xr.unity_bridge_models import (
    CommandRequest,
    AuthorizedUnityCommand,
    TelemetryObservation,
    create_authorized_unity_envelope
)
from holomed.input.models import generate_correlation_id
from holomed.devices.models import (
    PhysicalCommand,
    AdmittedCommandCapability,
    EndpointLease,
    DeviceValidationError,
    _ADMISSION_SECRET_KEY
)

def _create_mock_physical_command() -> PhysicalCommand:
    return PhysicalCommand(
        endpoint_id="unity_heart_sim",
        session_id="session1",
        lifecycle_generation=1,
        endpoint_lease_generation=2,
        execution_id="exec1",
        capability_scope=frozenset(("*",)),
        device_epoch=1,
        controller_epoch=1,
        physical_operation_id="phys1",
        command_nonce="nonce1",
        command_sequence=1,
        operation="GRASP",
        parameters={"force": 0.5}
    )

def _create_mock_lease() -> EndpointLease:
    return EndpointLease(
        endpoint_id="unity_heart_sim",
        device_id="unity_heart_sim_device",
        session_id="session1",
        lifecycle_generation=1,
        endpoint_lease_generation=2,
        execution_id="exec1",
        capability_scope=frozenset(("*",)),
        device_epoch=1,
        controller_epoch=1
    )

def test_A_command_request_untrusted_proposal():
    """Test A: CommandRequest is an untrusted proposal lacking canonical identity."""
    cid = generate_correlation_id()
    req = CommandRequest(
        correlation_id=cid,
        command_timestamp_ns=time.time_ns(),
        device_id="unity_heart_sim",
        action="GRASP",
        payload={"force": 0.5}
    )
    assert not hasattr(req, "device_epoch")
    assert not hasattr(req, "controller_epoch")
    assert not hasattr(req, "command_nonce")

def test_B_authorized_command_requires_cryptographic_capability():
    """Test B: AuthorizedUnityCommand explicitly rejects fake/missing capability tokens."""
    cid = generate_correlation_id()
    with pytest.raises(TypeError, match="Must provide a cryptographically sealed AdmittedCommandCapability"):
        AuthorizedUnityCommand(
            correlation_id=cid,
            command_timestamp_ns=time.time_ns(),
            _physical_command=_create_mock_physical_command(),
            _lease=_create_mock_lease(),
            _capability="I am a forged string, not a capability" # type: ignore
        )

def test_C_ultron_cannot_inject_epoch():
    """Test C: Ultron cannot inject authoritative epoch/nonce into its proposal."""
    cid = generate_correlation_id()
    req = CommandRequest(
        correlation_id=cid,
        command_timestamp_ns=time.time_ns(),
        device_id="unity_heart_sim",
        action="GRASP",
        payload={"force": 0.5}
    )
    assert not hasattr(req, "device_epoch")
    with pytest.raises(AttributeError):
        req.device_epoch = 999  # type: ignore

def test_D_capability_rejects_forged_signature():
    """Test D: AuthorizedUnityCommand rejects a capability if the signature was forged."""
    physical_cmd = _create_mock_physical_command()
    lease = _create_mock_lease()
    
    # Create forged capability with arbitrary string
    forged_cap = AdmittedCommandCapability(
        physical_operation_id=physical_cmd.physical_operation_id,
        command_nonce=physical_cmd.command_nonce,
        endpoint_lease_generation=lease.endpoint_lease_generation,
        _signature="forged_signature_hash"
    )
    
    with pytest.raises(DeviceValidationError, match="Invalid admission signature"):
        # We manually bypass the type check to see the verification fail
        forged_cap.verify(physical_cmd, lease, _ADMISSION_SECRET_KEY)
        
    with pytest.raises(DeviceValidationError, match="Invalid admission signature"):
        AuthorizedUnityCommand(
            correlation_id=generate_correlation_id(),
            command_timestamp_ns=time.time_ns(),
            _physical_command=physical_cmd,
            _lease=lease,
            _capability=forged_cap
        )

def test_E_capability_rejects_physical_operation_id_tampering():
    """Test E: Capability verification fails if physical_operation_id is altered after issuance."""
    physical_cmd = _create_mock_physical_command()
    lease = _create_mock_lease()
    cap = AdmittedCommandCapability.issue(physical_cmd, lease, _ADMISSION_SECRET_KEY)
    
    # Simulate a man-in-the-middle altering the operation ID on the physical command
    tampered_cmd = PhysicalCommand(
        **{**physical_cmd.__dict__, "physical_operation_id": "forged_op_id"}
    )
    
    with pytest.raises(DeviceValidationError, match="Capability context mismatch"):
        cap.verify(tampered_cmd, lease, _ADMISSION_SECRET_KEY)

def test_F_capability_rejects_nonce_tampering():
    """Test F: Capability verification fails if command_nonce is altered."""
    physical_cmd = _create_mock_physical_command()
    lease = _create_mock_lease()
    cap = AdmittedCommandCapability.issue(physical_cmd, lease, _ADMISSION_SECRET_KEY)
    
    tampered_cmd = PhysicalCommand(
        **{**physical_cmd.__dict__, "command_nonce": "forged_nonce"}
    )
    
    with pytest.raises(DeviceValidationError, match="Capability context mismatch"):
        cap.verify(tampered_cmd, lease, _ADMISSION_SECRET_KEY)

def test_G_capability_rejects_lease_generation_tampering():
    """Test G: Capability verification fails if endpoint_lease_generation is altered."""
    physical_cmd = _create_mock_physical_command()
    lease = _create_mock_lease()
    cap = AdmittedCommandCapability.issue(physical_cmd, lease, _ADMISSION_SECRET_KEY)
    
    tampered_lease = EndpointLease(
        **{**lease.__dict__, "endpoint_lease_generation": 999}
    )
    
    with pytest.raises(DeviceValidationError, match="Capability context mismatch"):
        cap.verify(physical_cmd, tampered_lease, _ADMISSION_SECRET_KEY)

def test_H_create_authorized_unity_envelope_success_provenance():
    """Test H: E2E provenance. CommandRequest + Valid Capability = MessageEnvelope."""
    physical_cmd = _create_mock_physical_command()
    lease = _create_mock_lease()
    cap = AdmittedCommandCapability.issue(physical_cmd, lease, _ADMISSION_SECRET_KEY)
    
    req = CommandRequest(
        correlation_id=generate_correlation_id(),
        command_timestamp_ns=time.time_ns(),
        device_id="unity_heart_sim",
        action="GRASP",
        payload={"force": 0.5}
    )
    
    envelope = create_authorized_unity_envelope(physical_cmd, lease, req, cap)
    
    assert envelope.message_type.value == "COMMAND"
    assert envelope.payload["device_id"] == "unity_heart_sim"
    assert envelope.payload["command"] == "GRASP"
    assert envelope.payload["device_epoch"] == 1
    assert envelope.payload["physical_operation_id"] == "phys1"
    assert envelope.payload["command_nonce"] == "nonce1"

def test_I_evidence_type_enforcement_prevents_hardware_spoofing():
    """Test I: TelemetryObservation cannot be forged into hardware authenticated evidence."""
    cid = generate_correlation_id()
    with pytest.raises(ValueError, match="Invalid evidence_type. Hardware evidence is STRICTLY forbidden"):
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


