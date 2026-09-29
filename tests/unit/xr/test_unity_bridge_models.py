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
)
from holomed.devices.control.admission import verify_admitted_capability
from holomed.devices.control.manager import DeviceControlManager
from holomed.devices.registry import DeviceRegistry
from holomed.devices.interfaces import RegistryAuthorityToken
from unittest.mock import MagicMock

# Create a test DeviceControlManager to issue capabilities authoritatively
_token = RegistryAuthorityToken()
_registry = DeviceRegistry(_token)
_dcm = DeviceControlManager(
    registry=_registry,
    rehydration_engine=MagicMock(),
    authoritative_epoch_provider=lambda: 1
)

from holomed.runtime.service import ServiceState
_dcm._state = ServiceState.STARTED
_dcm._capacity_admitter = lambda *args, **kwargs: ("phys1", False, None)

def _issue_capability(cmd, lease):
    # Mock fingerprint to match what the verification will compute
    fingerprint_dict = {
        "device_id": lease.device_id,
        "endpoint_id": cmd.endpoint_id,
        "command_name": cmd.operation,
        "parameters": cmd.parameters,
    }
    import hashlib
    from holomed.persistence.serialization import serialize_canonical_bytes
    fingerprint = hashlib.sha256(serialize_canonical_bytes(fingerprint_dict)).hexdigest()

    with _dcm.admit_physical_command(
        session_id=cmd.session_id,
        endpoint_id=cmd.endpoint_id,
        device_id=lease.device_id,
        d_epoch=cmd.device_epoch,
        c_epoch=cmd.controller_epoch,
        command_nonce=cmd.command_nonce,
        execution_id=cmd.execution_id,
        command_name=cmd.operation,
        fingerprint=fingerprint,
        endpoint_lease_generation=lease.endpoint_lease_generation
    ) as admission_ctx:
        # We need to construct a physical command that matches the generated op_id
        # Actually the capability is already generated and bound to that op_id
        # We must mutate the test's `cmd` to have the same op_id so the rest of the test works
        object.__setattr__(cmd, 'physical_operation_id', admission_ctx.physical_operation_id)

        return admission_ctx.capability

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
        verify_admitted_capability(forged_cap, physical_cmd, lease)

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
    cap = _issue_capability(physical_cmd, lease)

    # Simulate a man-in-the-middle altering the operation ID on the physical command
    tampered_cmd = PhysicalCommand(
        **{**physical_cmd.__dict__, "physical_operation_id": "forged_op_id"}
    )

    with pytest.raises(DeviceValidationError, match="Capability context mismatch"):
        verify_admitted_capability(cap, tampered_cmd, lease)

def test_F_capability_rejects_nonce_tampering():
    """Test F: Capability verification fails if command_nonce is altered."""
    physical_cmd = _create_mock_physical_command()
    lease = _create_mock_lease()
    cap = _issue_capability(physical_cmd, lease)

    tampered_cmd = PhysicalCommand(
        **{**physical_cmd.__dict__, "command_nonce": "forged_nonce"}
    )

    with pytest.raises(DeviceValidationError, match="Capability context mismatch"):
        verify_admitted_capability(cap, tampered_cmd, lease)

def test_G_capability_rejects_lease_generation_tampering():
    """Test G: Capability verification fails if endpoint_lease_generation is altered."""
    physical_cmd = _create_mock_physical_command()
    lease = _create_mock_lease()
    cap = _issue_capability(physical_cmd, lease)

    tampered_lease = EndpointLease(
        **{**lease.__dict__, "endpoint_lease_generation": 999}
    )

    with pytest.raises(DeviceValidationError, match="Capability context mismatch"):
        verify_admitted_capability(cap, physical_cmd, tampered_lease)

def test_H_create_authorized_unity_envelope_success_provenance():
    """Test H: E2E provenance. CommandRequest + Valid Capability = MessageEnvelope."""
    physical_cmd = _create_mock_physical_command()
    lease = _create_mock_lease()
    cap = _issue_capability(physical_cmd, lease)

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

def test_J_capability_issuer_not_globally_accessible():
    """Test J: The authoritative issuer cannot be imported or obtained globally."""
    import holomed.devices.control.admission as admission

    # 1. Ensure the legacy getter is completely removed
    assert not hasattr(admission, "get_issue_capability_function"), "Issuer getter must be removed"
    assert not hasattr(admission, "_create_admission_authority"), "Closure factory must be removed"

    # 2. Ensure DeviceControlManager encapsulates its secret

    # The secret should not be easily accessible from the outside (it is a private attribute)
    assert hasattr(_dcm, "_admission_secret")
    assert isinstance(_dcm._admission_secret, bytes)
    assert len(_dcm._admission_secret) == 32

    # The module-level variable only contains the verifier
    assert hasattr(admission, "_verifier_ref")



@pytest.fixture(autouse=True)
def ensure_dcm_verifier_registered():
    from holomed.devices.control.admission import register_authoritative_verifier
    try:
        register_authoritative_verifier(_dcm)
    except RuntimeError:
        pass

def test_K_adversarial_fake_verifier_registration():
    from holomed.devices.control.admission import register_authoritative_verifier
    import holomed.devices.control.admission as admission

    orig_ref = getattr(admission, '_verifier_ref', None)
    admission._verifier_ref = None

    try:
        fake_verifier = lambda cap, cmd, lease: None
        with pytest.raises(RuntimeError, match="must be tied to the legitimate DeviceControlManager lifecycle"):
            register_authoritative_verifier(fake_verifier)  # type: ignore
    finally:
        admission._verifier_ref = orig_ref

def test_L_adversarial_second_verifier_registration():
    from holomed.devices.control.admission import register_authoritative_verifier
    from holomed.devices.control.manager import DeviceControlManager
    from unittest.mock import MagicMock

    fake_dcm = DeviceControlManager(
        registry=_registry,
        rehydration_engine=MagicMock(),
        authoritative_epoch_provider=lambda: 1
    )
    # Merely existing does not throw, nor does it become authoritative.
    # Attempting to register it directly fails because it's UNINITIALIZED.
    with pytest.raises(RuntimeError, match="must be STARTED"):
        register_authoritative_verifier(fake_dcm)

def test_M_adversarial_verifier_replacement():
    from holomed.devices.control.admission import register_authoritative_verifier
    from holomed.devices.control.manager import DeviceControlManager
    from unittest.mock import MagicMock
    from holomed.runtime.service import ServiceState

    fake_dcm = DeviceControlManager(
        registry=_registry,
        rehydration_engine=MagicMock(),
        authoritative_epoch_provider=lambda: 1
    )
    # Even if an attacker manually forces the state to STARTED
    fake_dcm._state = ServiceState.STARTED
    # It still fails because the legitimate one is ALREADY registered
    with pytest.raises(RuntimeError, match="is already registered and active"):
        register_authoritative_verifier(fake_dcm)

def test_N_adversarial_verifier_clearing():
    from holomed.devices.control.admission import unregister_authoritative_verifier
    import holomed.devices.control.admission as admission

    fake_verifier = lambda cap, cmd, lease: None
    unregister_authoritative_verifier(fake_verifier)  # type: ignore

    assert admission._verifier_ref is not None
    assert admission._verifier_ref() is _dcm

def test_O_adversarial_forged_capability_with_malicious_verifier():
    import holomed.devices.control.admission as admission
    from holomed.devices.control.admission import register_authoritative_verifier

    orig_ref = getattr(admission, '_verifier_ref', None)
    admission._verifier_ref = None

    try:
        fake_verifier = lambda cap, cmd, lease: None
        with pytest.raises(RuntimeError):
            register_authoritative_verifier(fake_verifier)  # type: ignore
    finally:
        admission._verifier_ref = orig_ref

def test_P_authorized_unity_command_no_verifier():
    import holomed.devices.control.admission as admission

    orig_ref = getattr(admission, '_verifier_ref', None)
    admission._verifier_ref = None

    try:
        physical_cmd = _create_mock_physical_command()
        lease = _create_mock_lease()
        cap = _issue_capability(physical_cmd, lease)

        with pytest.raises(RuntimeError, match="No active authoritative admission verifier registered."):
            AuthorizedUnityCommand(
                correlation_id=generate_correlation_id(),
                command_timestamp_ns=time.time_ns(),
                _physical_command=physical_cmd,
                _lease=lease,
                _capability=cap
            )
    finally:
        admission._verifier_ref = orig_ref
