import re

path = "tests/unit/devices/control/test_m49_adversarial_proofs.py"
with open(path, "r", encoding="utf-8") as f:
    content = f.read()

new_test = """# ---------------------------------------------------------
# 15. EVIDENCE IDENTITY/GENERATION MISMATCH REJECTION
# ---------------------------------------------------------
def test_evidence_identity_generation_mismatch_rejection(shared_store_path):
    from holomed.persistence.store import DurableSessionStore
    from holomed.persistence.exceptions import PersistenceTerminationConflictError
    from holomed.devices.control.manager import DeviceControlManager
    from holomed.devices.transport import TelemetryTransport
    from holomed.devices.resolution import ExecutionResolutionGate
    from holomed.devices.reconciler import TelemetryReconciler
    from holomed.devices.models import ExecutionTelemetryEvent, EventSourceAuthority, CommandState, PhysicalCommand

    authority, epoch, session = setup_test_store(shared_store_path)
    store = DurableSessionStore(shared_store_path, epoch_id=epoch)
    store.restore_session_from_disk(session)
    store.record_operation_admitted(session, "ep_e", "dev_e", 1, 1, "op_e", "nonce_e", "exec_e", "test")
    
    # 1. Setup production path components
    manager = DeviceControlManager(capacity_releaser=store.record_operation_terminated)
    transport = TelemetryTransport()
    gate = ExecutionResolutionGate()
    reconciler = TelemetryReconciler(transport, gate)
    
    # Claim ownership for correct generation
    gate.claim_execution_ownership("exec_e", 1)

    def _run_pipeline(event: ExecutionTelemetryEvent, manager_command_override: PhysicalCommand = None):
        if manager_command_override:
            manager._active_commands[event.execution_id] = manager_command_override
        
        transport.publisher.publish(event)
        records = reconciler.process_pending_events()
        
        for record in records:
            if record.terminal_resolution_status and gate.is_capacity_release_terminal(record.current_state):
                manager.release_capacity_for_execution(record.execution_id, record.current_state)

    # Base event factory
    def _make_event(exec_id="exec_e", gen=1, state=CommandState.COMPLETED):
        return ExecutionTelemetryEvent(
            event_id="evt_1", endpoint_id="ep_e", session_id=session,
            lifecycle_generation=gen, endpoint_lease_generation=1, execution_id=exec_id,
            command_sequence=1, event_sequence=1, event_type="STATE", observed_state=state,
            source_authority=EventSourceAuthority.ENDPOINT_ADAPTER, source_origin="driver",
            timestamp_utc="2026-09-19T00:00:00Z", payload={}, evidence_generation=1,
            cryptographic_signature=None, fencing_challenge=None
        )

    # 2. Lifecycle generation mismatch -> Rejected at the Gate (never reaches store)
    _run_pipeline(_make_event(gen=999))
    record_at_gate = gate.resolve_timeout("exec_e", 1)
    assert record_at_gate.terminal_resolution_status is False  # Reconciler/Gate ignored it

    # Base command factory to inject into manager for store mismatch proofs
    def _make_cmd(d_epoch=1, c_epoch=1, op_id="op_e", exec_id="exec_e"):
        return PhysicalCommand(
            device_epoch=d_epoch, controller_epoch=c_epoch, physical_operation_id=op_id,
            command_nonce="nonce_e", endpoint_id="ep_e", session_id=session,
            lifecycle_generation=1, endpoint_lease_generation=1, execution_id=exec_id,
            capability_scope=frozenset(), command_sequence=1, operation="test", parameters={}
        )

    # 3. Controller epoch mismatch -> Rejected by Store
    with pytest.raises(PersistenceTerminationConflictError):
        _run_pipeline(_make_event(), manager_command_override=_make_cmd(c_epoch=2))
        
    # Reset gate for next attempt since we want it to process another terminal event
    gate._records.pop("exec_e", None)
    gate.claim_execution_ownership("exec_e", 1)

    # 4. Device epoch mismatch -> Rejected by Store
    with pytest.raises(PersistenceTerminationConflictError):
        _run_pipeline(_make_event(), manager_command_override=_make_cmd(d_epoch=2))

    gate._records.pop("exec_e", None)
    gate.claim_execution_ownership("exec_e", 1)

    # 5. Physical operation ID mismatch -> Rejected by Store
    with pytest.raises(PersistenceTerminationConflictError):
        _run_pipeline(_make_event(), manager_command_override=_make_cmd(op_id="op_WRONG"))

    # Verify operation is STILL active physically
    assert store.get_active_physical_operations() == 1
"""

pattern = r"# -+\n# 15\. EVIDENCE IDENTITY/GENERATION MISMATCH REJECTION\n# -+\ndef test_evidence_identity_generation_mismatch_rejection\(shared_store_path\):.*?(?=# -+\n# 16\. EPOCH AUTHORITY FAIL-CLOSED VALIDATION)"
new_content = re.sub(pattern, new_test + "\n", content, flags=re.DOTALL)

with open(path, "w", encoding="utf-8") as f:
    f.write(new_content)
print("Replaced test 15 successfully.")
