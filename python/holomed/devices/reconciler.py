"""HoloMed AI - Telemetry Reconciler"""

from typing import List, Dict

from holomed.devices.interfaces import IExecutionResolutionGate
from holomed.devices.transport import TelemetryTransport
from holomed.devices.models import (
    ExecutionTelemetryEvent,
    EventSourceAuthority,
    AuthoritativeExecutionRecord
)


class TelemetryReconciler:
    """Authoritative processor for all transported physical execution events.
    
    Responsibilities:
    1. Safely drain critical and normal queues from transport.
    2. Partition events by execution_id.
    3. Apply event_sequence as the physical ordering mechanism.
    4. Validate schema and SourceAuthority.
    5. Pass validated, strictly ordered events to ExecutionResolutionGate.
    """

    def __init__(self, transport: TelemetryTransport, resolution_gate: IExecutionResolutionGate):
        self._transport = transport
        self._resolution_gate = resolution_gate
        # Reconciler is strictly stateless regarding duplicate authority.
        # Deduplication and sequence collision logic is fully delegated to ExecutionResolutionGate.

    def process_pending_events(self) -> List[AuthoritativeExecutionRecord]:
        """Drain, order, and validate pending telemetry events."""
        
        # 1. Drain queues. Drain order does not matter for physical sequence sorting.
        # Queue priority is only transport scheduling.
        events: List[ExecutionTelemetryEvent] = []
        events.extend(self._transport.drain_critical())
        events.extend(self._transport.drain_normal())
        
        # 2. Partition by execution_id
        partitioned: Dict[str, List[ExecutionTelemetryEvent]] = {}
        for event in events:
            # 5. Schema validation (prevent malformed envelopes before gate mutation)
            if not isinstance(event, ExecutionTelemetryEvent):
                continue
            if not event.execution_id or event.event_sequence is None:
                continue
                
            # 6. Source-authority validation
            # CONTROL_PLANE_TIMEOUT must not be processed via physical telemetry
            if event.source_authority not in (EventSourceAuthority.HARDWARE_DRIVER, EventSourceAuthority.ENDPOINT_ADAPTER):
                continue
                
            partitioned.setdefault(event.execution_id, []).append(event)
            
        updated_records: List[AuthoritativeExecutionRecord] = []
        
        # Process each execution bucket independently
        for _, exec_events in partitioned.items():
            # 3. Sort by event_sequence (ascending).
            # This is the physical ordering mechanism, not transport priority.
            exec_events.sort(key=lambda e: e.event_sequence)
            
            for event in exec_events:
                # 9. Pass strictly validated events to the gate.
                # The gate handles idempotency/duplication/sequence-collisions.
                record = self._resolution_gate.resolve_terminal_event(event)
                if record:
                    updated_records.append(record)
                
        return updated_records
