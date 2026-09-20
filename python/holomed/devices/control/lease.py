"""HoloMed AI - Physical endpoint lease registry."""

from __future__ import annotations

from typing import Dict, Iterable, List, Optional, Tuple

from holomed.devices.interfaces import IPhysicalEndpoint
from holomed.devices.models import EndpointLease


class EndpointLeaseRegistry:
    """Tracks active physical endpoint leases and enforces strict lifecycle generations."""

    def __init__(self) -> None:
        # endpoint_id -> EndpointLease
        self._active_leases: Dict[str, EndpointLease] = {}
        # endpoint_id -> current monotonic command sequence
        self._command_sequences: Dict[str, int] = {}
        # endpoint_id -> generation counter
        self._lease_generations: Dict[str, int] = {}

    def get_lease(self, endpoint_id: str) -> Optional[EndpointLease]:
        """Get current active lease for an endpoint, if any."""
        return self._active_leases.get(endpoint_id)

    def issue_lease(
        self,
        endpoint: IPhysicalEndpoint,
        session_id: str,
        lifecycle_generation: int,
        execution_id: str,
        capability_scope: frozenset[str],
    ) -> EndpointLease:
        """Issue or recycle a lease for a physical endpoint."""
        existing_lease = self._active_leases.get(endpoint.endpoint_id)
        
        # If the same execution/session requests a lease, return it (idempotency)
        if existing_lease is not None:
            if (existing_lease.session_id == session_id and
                existing_lease.execution_id == execution_id and
                existing_lease.lifecycle_generation == lifecycle_generation):
                # In this zero-trust model, lease is tied to the physical actuation.
                return existing_lease
            
            # If different session/execution, reject.
            from holomed.devices.control.exceptions import ControlCapacityError
            raise ControlCapacityError(
                f"Endpoint {endpoint.endpoint_id} is currently leased to session {existing_lease.session_id} "
                f"(execution {existing_lease.execution_id}). Cannot issue new lease until gracefully released."
            )

        # Generate new lease generation
        gen = self._lease_generations.get(endpoint.endpoint_id, 0) + 1
        self._lease_generations[endpoint.endpoint_id] = gen
        
        lease = EndpointLease(
            device_epoch=0,
            controller_epoch=0,
        endpoint_id=endpoint.endpoint_id,
            device_id=endpoint.device_id,
            session_id=session_id,
            lifecycle_generation=lifecycle_generation,
            endpoint_lease_generation=gen,
            execution_id=execution_id,
            capability_scope=capability_scope,
        )
        self._active_leases[endpoint.endpoint_id] = lease
        self._command_sequences[endpoint.endpoint_id] = 0
        
        # Notify the physical driver to acquire the lease.
        endpoint.acquire_lease(lease)
        
        return lease

    def next_command_sequence(self, endpoint_id: str) -> int:
        """Increment and return the next command sequence for an endpoint."""
        seq = self._command_sequences.get(endpoint_id, 0) + 1
        self._command_sequences[endpoint_id] = seq
        return seq

    def release_lease(self, endpoint: IPhysicalEndpoint, session_id: str) -> None:
        """Explicitly release a lease, typically on session revocation."""
        lease = self._active_leases.get(endpoint.endpoint_id)
        if lease is not None and lease.session_id == session_id:
            del self._active_leases[endpoint.endpoint_id]
            endpoint.release_lease(session_id)

    def clear(self) -> None:
        """Reset the entire registry (used during stop)."""
        self._active_leases.clear()
        self._command_sequences.clear()
        self._lease_generations.clear()
