"""HoloMed AI - State Rehydration Engine for Phase 3 Recovery."""

from typing import Dict, Any, Tuple
from holomed.persistence.sessions import DurableSessionStore
from holomed.persistence.authority import ControllerAuthorityStore
from holomed.devices.interfaces import IExecutionResolutionGate

class StateRehydrationEngine:
    """Orchestrates recovery of physical capacity and state from durable journals.
    
    Guarantees:
    - Identifies active operations from previous epochs.
    - Transitions orphaned active operations to FAULTED_UNKNOWN to quarantine them while retaining capacity.
    - Integrates with DurableSessionStore using the current authoritative epoch.
    """

    def __init__(self, session_store: DurableSessionStore, resolution_gate: IExecutionResolutionGate, authority_store: ControllerAuthorityStore):
        self._session_store = session_store
        self._resolution_gate = resolution_gate
        self._authority = authority_store

    def rehydrate_controller_state(self, current_session_id: str) -> None:
        """
        Rehydrates controller state on boot.
        Identifies active operations from previous epochs and transitions them to FAULTED_UNKNOWN.
        This retains capacity while quarantining the operations, awaiting explicit operator recovery.
        """
        active_ops = self._session_store.get_active_operations_snapshot()
        
        # We need the current authoritative epoch to safely write terminations for old epochs
        auth_epoch = self._authority.read_current_epoch()
        if auth_epoch is None:
            # If there's no epoch, we can't be booting correctly, but let's be safe.
            return
            
        for canon, payload in active_ops.items():
            device_id, device_epoch, controller_epoch, physical_operation_id, command_nonce = canon
            
            resolution = payload.get("resolution")
            # If it's already quarantined or faulted unknown in a previous run, do nothing.
            if resolution in {"FAULTED_UNKNOWN", "QUARANTINED"}:
                continue
                
            original_session_id = payload.get("_original_session_id", current_session_id)
            
            # Ensure the original session is available in the store
            self._session_store.restore_session_from_disk(original_session_id)
            
            self._session_store.record_operation_terminated(
                session_id=original_session_id,
                device_id=device_id,
                device_epoch=device_epoch,
                controller_epoch=controller_epoch,
                physical_operation_id=physical_operation_id,
                command_nonce=command_nonce,
                resolution="FAULTED_UNKNOWN",
                authoritative_epoch=auth_epoch
            )

    def rehydrate_device_state(self, current_session_id: str, device_id: str, new_device_epoch: int) -> None:
        """
        Rehydrates and quarantines state when a device restarts.
        Any active operation for this device that belongs to an OLDER device_epoch must be quarantined.
        """
        active_ops = self._session_store.get_active_operations_snapshot()
        auth_epoch = self._authority.read_current_epoch()
        if auth_epoch is None:
            return
            
        for canon, payload in active_ops.items():
            op_device_id, op_device_epoch, op_controller_epoch, physical_operation_id, command_nonce = canon
            
            if op_device_id != device_id:
                continue
                
            # If the operation is from a strictly older device epoch, it's orphaned.
            if op_device_epoch < new_device_epoch:
                resolution = payload.get("resolution")
                if resolution in {"FAULTED_UNKNOWN", "QUARANTINED"}:
                    continue
                    
                original_session_id = payload.get("_original_session_id", current_session_id)
                self._session_store.restore_session_from_disk(original_session_id)
                
                self._session_store.record_operation_terminated(
                    session_id=original_session_id,
                    device_id=op_device_id,
                    device_epoch=op_device_epoch,
                    controller_epoch=op_controller_epoch,
                    physical_operation_id=physical_operation_id,
                    command_nonce=command_nonce,
                    resolution="FAULTED_UNKNOWN",
                    authoritative_epoch=auth_epoch
                )
