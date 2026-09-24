"""HoloMed AI - Telemetry Reconciliation Daemon"""

import threading
import time
from typing import Optional

from holomed.devices.reconciler import TelemetryReconciler
from holomed.persistence.sessions import DurableSessionStore
from holomed.devices.models import AuthoritativeExecutionRecord
import logging

logger = logging.getLogger(__name__)

class ReconciliationDaemon:
    """Daemon that continuously drives TelemetryReconciler and bridges terminal states to DurableSessionStore.
    
    Responsibilities:
    - Drain transport via TelemetryReconciler.
    - Write terminal physical resolutions to DurableSessionStore.
    """

    def __init__(self, reconciler: TelemetryReconciler, session_store: DurableSessionStore, polling_interval: float = 0.05):
        self._reconciler = reconciler
        self._session_store = session_store
        self._polling_interval = polling_interval
        self._shutdown_event = threading.Event()
        self._thread: Optional[threading.Thread] = None

    def start(self) -> None:
        """Start the reconciliation daemon."""
        if self._thread is not None:
            return
        self._shutdown_event.clear()
        self._thread = threading.Thread(target=self._run_loop, name="ReconciliationDaemon", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        """Stop the reconciliation daemon."""
        if self._thread is None:
            return
        self._shutdown_event.set()
        self._thread.join(timeout=2.0)
        self._thread = None

    def _run_loop(self) -> None:
        while not self._shutdown_event.is_set():
            try:
                self.run_reconciliation_cycle()
            except Exception as e:
                logger.error(f"Reconciliation loop error: {e}")
            time.sleep(self._polling_interval)

    def run_reconciliation_cycle(self) -> None:
        """Drain transport, process via reconciler, and durably resolve terminal executions."""
        updated_records = self._reconciler.process_pending_events()
        
        for record in updated_records:
            if record.terminal_resolution_status:
                self._resolve_terminal_record(record)

    def _resolve_terminal_record(self, record: AuthoritativeExecutionRecord) -> None:
        """Write terminal resolution to DurableSessionStore."""
        active_snapshot = self._session_store.get_active_operations_snapshot()
        
        for canon, payload in active_snapshot.items():
            if payload.get("execution_id") == record.execution_id:
                # We found the canonical identity and session info
                session_id = payload.get("_original_session_id")
                if not session_id:
                    logger.error(f"Cannot terminate {record.execution_id}: missing _original_session_id")
                    return
                
                device_id = canon[0]
                device_epoch = canon[1]
                controller_epoch = canon[2]
                physical_operation_id = canon[3]
                command_nonce = canon[4]
                resolution = record.current_state.value
                
                # Quarantined / Faulted Unknown remain in the active set but are marked.
                # However, they are still considered "terminal" for the ExecutionResolutionGate.
                # In sessions.py, they are accepted as valid terminal resolutions if not invalid.
                # Wait, record_operation_terminated invalid resolutions:
                # "QUARANTINED", "RECOVERY_REQUIRED", "UNKNOWN", "ISOLATION_PREPARE", "DISPATCHING", "RUNNING"
                invalid_terminations = {"QUARANTINED", "RECOVERY_REQUIRED", "UNKNOWN", "ISOLATION_PREPARE", "DISPATCHING", "RUNNING"}
                
                if resolution in invalid_terminations:
                    # Not a valid termination resolution for the session store, skip.
                    return
                    
                self._session_store.record_operation_terminated(
                    session_id=session_id,
                    device_id=device_id,
                    device_epoch=device_epoch,
                    controller_epoch=controller_epoch,
                    physical_operation_id=physical_operation_id,
                    command_nonce=command_nonce,
                    resolution=resolution,
                    authoritative_epoch=None
                )
                return
        
        # If not found in active snapshot, it might already be terminated or the record is for an invalid execution.
        logger.debug(f"Execution {record.execution_id} not found in active snapshot during terminal resolution.")
