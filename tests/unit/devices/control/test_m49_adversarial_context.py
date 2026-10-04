import pytest
import time
import uuid
import hashlib

from holomed.devices.control.manager import DeviceControlManager
from holomed.devices.registry import DeviceRegistry
from holomed.devices.interfaces import RegistryAuthorityToken
from holomed.runtime.service import ServiceState
from holomed.devices.models import PhysicalCommand, EndpointLease, AdmittedCommandCapability
from holomed.devices.control.exceptions import DeviceControlError, ControlCapacityError
from holomed.devices.models import DeviceValidationError
from holomed.persistence.serialization import serialize_canonical_bytes

@pytest.fixture
def manager():
    token = RegistryAuthorityToken()
    registry = DeviceRegistry(token)
    dcm = DeviceControlManager(
        registry=registry,
        rehydration_engine=None,
        authoritative_epoch_provider=lambda: 1
    )
    dcm._state = ServiceState.STARTED
    dcm._capacity_admitter = lambda *args, **kwargs: (str(uuid.uuid4()), False, None)
    from holomed.devices.control.admission import register_authoritative_verifier
    register_authoritative_verifier(dcm)
    return dcm

def _create_mock_physical_command(op_id="phys1", nonce="nonce1", endpoint_id="unity_heart_sim", execution_id="exec1", operation="GRASP", params={"force": 0.5}) -> PhysicalCommand:
    return PhysicalCommand(
        endpoint_id=endpoint_id,
        session_id="session1",
        lifecycle_generation=1,
        endpoint_lease_generation=2,
        execution_id=execution_id,
        capability_scope=frozenset(("*",)),
        device_epoch=1,
        controller_epoch=1,
        physical_operation_id=op_id,
        command_nonce=nonce,
        command_sequence=1,
        operation=operation,
        parameters=params
    )

def _create_mock_lease(device_id="dev1") -> EndpointLease:
    return EndpointLease(
        endpoint_id="unity_heart_sim",
        device_id=device_id,
        session_id="session1",
        lifecycle_generation=1,
        endpoint_lease_generation=2,
        execution_id="exec1",
        capability_scope=frozenset(("*",)),
        device_epoch=1,
        controller_epoch=1
    )

def _get_fingerprint(cmd, lease):
    fingerprint_dict = {
        "device_id": lease.device_id,
        "endpoint_id": cmd.endpoint_id,
        "command_name": cmd.operation,
        "parameters": cmd.parameters,
    }
    return hashlib.sha256(serialize_canonical_bytes(fingerprint_dict)).hexdigest()

def test_A_same_op_id_forged_nonce(manager):
    cmd = _create_mock_physical_command()
    lease = _create_mock_lease()
    fp = _get_fingerprint(cmd, lease)
    with manager.admit_physical_command(
        cmd.session_id, cmd.endpoint_id, lease.device_id, 1, 1, cmd.command_nonce, cmd.command_nonce, cmd.execution_id, cmd.operation, fp, lease.endpoint_lease_generation
    ) as ctx:
        cap = ctx.capability
        
    from holomed.devices.control.admission import verify_admitted_capability
    # Forge nonce
    forged_cmd = PhysicalCommand(**{**cmd.__dict__, "command_nonce": "forged_nonce", "physical_operation_id": ctx.physical_operation_id})
    with pytest.raises(DeviceValidationError, match="Capability context mismatch"):
        verify_admitted_capability(cap, forged_cmd, lease)

def test_B_same_op_id_different_lease(manager):
    cmd = _create_mock_physical_command()
    lease = _create_mock_lease()
    fp = _get_fingerprint(cmd, lease)
    with manager.admit_physical_command(
        cmd.session_id, cmd.endpoint_id, lease.device_id, 1, 1, cmd.command_nonce, cmd.command_nonce, cmd.execution_id, cmd.operation, fp, lease.endpoint_lease_generation
    ) as ctx:
        cap = ctx.capability
        
    from holomed.devices.control.admission import verify_admitted_capability
    cmd_with_op = PhysicalCommand(**{**cmd.__dict__, "physical_operation_id": ctx.physical_operation_id})
    # Different lease
    diff_lease = EndpointLease(**{**lease.__dict__, "device_id": "different_dev"})
    with pytest.raises(DeviceValidationError, match="Invalid admission signature"):
        verify_admitted_capability(cap, cmd_with_op, diff_lease)

def test_C_same_op_id_forged_generation(manager):
    cmd = _create_mock_physical_command()
    lease = _create_mock_lease()
    fp = _get_fingerprint(cmd, lease)
    with manager.admit_physical_command(
        cmd.session_id, cmd.endpoint_id, lease.device_id, 1, 1, cmd.command_nonce, cmd.command_nonce, cmd.execution_id, cmd.operation, fp, lease.endpoint_lease_generation
    ) as ctx:
        cap = ctx.capability
        
    from holomed.devices.control.admission import verify_admitted_capability
    cmd_with_op = PhysicalCommand(**{**cmd.__dict__, "physical_operation_id": ctx.physical_operation_id})
    forged_lease = EndpointLease(**{**lease.__dict__, "endpoint_lease_generation": 999})
    with pytest.raises(DeviceValidationError, match="Capability context mismatch"):
        verify_admitted_capability(cap, cmd_with_op, forged_lease)

def test_D_same_op_id_modified_payload(manager):
    cmd = _create_mock_physical_command()
    lease = _create_mock_lease()
    fp = _get_fingerprint(cmd, lease)
    with manager.admit_physical_command(
        cmd.session_id, cmd.endpoint_id, lease.device_id, 1, 1, cmd.command_nonce, cmd.command_nonce, cmd.execution_id, cmd.operation, fp, lease.endpoint_lease_generation
    ) as ctx:
        cap = ctx.capability
        
    from holomed.devices.control.admission import verify_admitted_capability
    cmd_with_op = PhysicalCommand(**{**cmd.__dict__, "physical_operation_id": ctx.physical_operation_id, "operation": "DROP"})
    with pytest.raises(DeviceValidationError, match="Invalid admission signature"):
        verify_admitted_capability(cap, cmd_with_op, lease)

def test_E_capability_from_A_command_from_B(manager):
    cmd1 = _create_mock_physical_command(nonce="nonce1")
    lease1 = _create_mock_lease()
    fp1 = _get_fingerprint(cmd1, lease1)
    with manager.admit_physical_command(
        cmd1.session_id, cmd1.endpoint_id, lease1.device_id, 1, 1, cmd1.command_nonce, cmd1.command_nonce, cmd1.execution_id, cmd1.operation, fp1, lease1.endpoint_lease_generation
    ) as ctx1:
        cap1 = ctx1.capability

    cmd2 = _create_mock_physical_command(nonce="nonce2")
    lease2 = _create_mock_lease()
    fp2 = _get_fingerprint(cmd2, lease2)
    with manager.admit_physical_command(
        cmd2.session_id, cmd2.endpoint_id, lease2.device_id, 1, 1, cmd2.command_nonce, cmd2.command_nonce, cmd2.execution_id, cmd2.operation, fp2, lease2.endpoint_lease_generation
    ) as ctx2:
        cap2 = ctx2.capability

    from holomed.devices.control.admission import verify_admitted_capability
    cmd2_with_op = PhysicalCommand(**{**cmd2.__dict__, "physical_operation_id": ctx2.physical_operation_id})
    with pytest.raises(DeviceValidationError):
        verify_admitted_capability(cap1, cmd2_with_op, lease1)

def test_F_capability_from_A_lease_from_B(manager):
    cmd1 = _create_mock_physical_command(nonce="nonce1")
    lease1 = _create_mock_lease(device_id="dev1")
    fp1 = _get_fingerprint(cmd1, lease1)
    with manager.admit_physical_command(
        cmd1.session_id, cmd1.endpoint_id, lease1.device_id, 1, 1, cmd1.command_nonce, cmd1.command_nonce, cmd1.execution_id, cmd1.operation, fp1, lease1.endpoint_lease_generation
    ) as ctx1:
        cap1 = ctx1.capability

    cmd2 = _create_mock_physical_command(nonce="nonce2")
    lease2 = _create_mock_lease(device_id="dev2")
    
    from holomed.devices.control.admission import verify_admitted_capability
    cmd1_with_op = PhysicalCommand(**{**cmd1.__dict__, "physical_operation_id": ctx1.physical_operation_id})
    with pytest.raises(DeviceValidationError):
        verify_admitted_capability(cap1, cmd1_with_op, lease2)

def test_G_no_reusable_signer(manager):
    cmd = _create_mock_physical_command()
    lease = _create_mock_lease()
    fp = _get_fingerprint(cmd, lease)
    with manager.admit_physical_command(
        cmd.session_id, cmd.endpoint_id, lease.device_id, 1, 1, cmd.command_nonce, cmd.command_nonce, cmd.execution_id, cmd.operation, fp, lease.endpoint_lease_generation
    ) as ctx:
        # Context must not have issue_capability method
        assert not hasattr(ctx, "issue_capability")
        assert not callable(getattr(ctx, "capability", None))
        assert not hasattr(ctx, "_admission_secret")
        assert not hasattr(ctx, "secret")

def test_H_no_admitter_fails_closed(manager):
    manager._capacity_admitter = None
    cmd = _create_mock_physical_command()
    lease = _create_mock_lease()
    fp = _get_fingerprint(cmd, lease)
    with pytest.raises(ControlCapacityError, match="No capacity admitter configured"):
        with manager.admit_physical_command(
            cmd.session_id, cmd.endpoint_id, lease.device_id, 1, 1, cmd.command_nonce, cmd.command_nonce, cmd.execution_id, cmd.operation, fp, lease.endpoint_lease_generation
        ) as ctx:
            pass

def test_I_admission_failure_before_yield(manager):
    def failing_admitter(*args, **kwargs):
        raise ControlCapacityError("Forced failure")
    manager._capacity_admitter = failing_admitter
    
    cmd = _create_mock_physical_command()
    lease = _create_mock_lease()
    fp = _get_fingerprint(cmd, lease)
    with pytest.raises(ControlCapacityError, match="Forced failure"):
        with manager.admit_physical_command(
            cmd.session_id, cmd.endpoint_id, lease.device_id, 1, 1, cmd.command_nonce, cmd.command_nonce, cmd.execution_id, cmd.operation, fp, lease.endpoint_lease_generation
        ) as ctx:
            pass
    # No capability produced

def test_J_exception_inside_context_doesnt_leave_signer(manager):
    cmd = _create_mock_physical_command()
    lease = _create_mock_lease()
    fp = _get_fingerprint(cmd, lease)
    ctx = None
    try:
        with manager.admit_physical_command(
            cmd.session_id, cmd.endpoint_id, lease.device_id, 1, 1, cmd.command_nonce, cmd.command_nonce, cmd.execution_id, cmd.operation, fp, lease.endpoint_lease_generation
        ) as ctx:
            raise ValueError("Inner failure")
    except ValueError:
        pass
    
    assert not hasattr(ctx, "issue_capability")
