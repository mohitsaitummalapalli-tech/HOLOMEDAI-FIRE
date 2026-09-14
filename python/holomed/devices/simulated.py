"""HoloMed AI - Fully functional simulated device test double."""

from __future__ import annotations

import contextlib
import queue
import threading
import time
from datetime import datetime, timezone
from enum import Enum
from types import MappingProxyType
from typing import Optional, Tuple, Set

from holomed.devices.interfaces import DeviceResourceAccessor, IDevice
from holomed.devices.models import (
    DeviceCapability,
    DeviceHealth,
    DeviceState,
    DeviceType,
    EndpointLease,
    EndpointSafetyState,
    EndpointState,
    PhysicalCommand,
    PhysicalCommandResult,
    SubmissionStatus,
)
from holomed.runtime.models import HealthStatus
from holomed.devices.interfaces import IPhysicalEndpoint
from holomed.devices.control.exceptions import CapabilityUnauthorizedError


class WorkerState(Enum):
    UNAVAILABLE = "UNAVAILABLE"
    RUNNING = "RUNNING"
    SHUTDOWN_REQUESTED = "SHUTDOWN_REQUESTED"
    STOPPED = "STOPPED"


class SimulatedPhysicalEndpoint(IPhysicalEndpoint):
    """M49.3.2 Simulated physical endpoint for testing execution boundary."""

    def __init__(self, endpoint_id: str, device_id: str, queue_capacity: int = 10) -> None:
        self._endpoint_id = endpoint_id
        self._device_id = device_id
        self._safety_state = EndpointSafetyState.SAFE_STOPPED
        self._active_lease: Optional[EndpointLease] = None
        self._last_accepted_sequence = 0
        self._queue_capacity = queue_capacity

        # Concurrency and execution boundary
        self._submit_lock = threading.Lock()
        self._command_queue: queue.Queue[PhysicalCommand] = queue.Queue(maxsize=self._queue_capacity)
        
        # Worker lifecycle
        self._worker_state = WorkerState.UNAVAILABLE
        self._worker_thread: Optional[threading.Thread] = None
        self._shutdown_event = threading.Event()
        
        # Stop channel
        self._stop_requests: Set[str] = set()
        
        self._start_worker()

    def _start_worker(self) -> None:
        with self._submit_lock:
            self._worker_state = WorkerState.RUNNING
            self._shutdown_event.clear()
            self._worker_thread = threading.Thread(
                target=self._execution_loop, 
                name=f"sim_worker_{self._endpoint_id}",
                daemon=True
            )
            self._worker_thread.start()

    def stop_worker(self) -> None:
        """Testing seam to simulate worker dying or shutting down."""
        with self._submit_lock:
            self._worker_state = WorkerState.SHUTDOWN_REQUESTED
            self._shutdown_event.set()
        
        if self._worker_thread and self._worker_thread.is_alive():
            self._worker_thread.join(timeout=1.0)
            
        with self._submit_lock:
            self._worker_state = WorkerState.STOPPED

    @property
    def endpoint_id(self) -> str:
        return self._endpoint_id

    @property
    def device_id(self) -> str:
        return self._device_id

    @property
    def safety_state(self) -> EndpointSafetyState:
        return self._safety_state

    @property
    def endpoint_state(self) -> EndpointState:
        return EndpointState.READY

    def recover(self) -> None:
        raise NotImplementedError("Recovery semantics not yet implemented in M49.3")

    def request_stop(self, execution_id: str) -> None:
        """Independently request a stop for a specific active execution."""
        with self._submit_lock:
            self._stop_requests.add(execution_id)

    def emergency_stop(self) -> EndpointSafetyState:
        if self._safety_state == EndpointSafetyState.HARDWARE_INTERLOCKED:
            return self._safety_state
        self._safety_state = EndpointSafetyState.SAFE_STOPPED
        return self._safety_state

    def acquire_lease(self, lease: EndpointLease) -> None:
        with self._submit_lock:
            if self._safety_state == EndpointSafetyState.HARDWARE_INTERLOCKED:
                raise RuntimeError("Hardware is interlocked, cannot acquire lease")
            self._active_lease = lease
            self._safety_state = EndpointSafetyState.ACTIVE
            self._last_accepted_sequence = 0

    def release_lease(self, session_id: str) -> None:
        with self._submit_lock:
            if self._active_lease and self._active_lease.session_id == session_id:
                self._active_lease = None
                self._last_accepted_sequence = 0
                if self._safety_state != EndpointSafetyState.HARDWARE_INTERLOCKED:
                    self._safety_state = EndpointSafetyState.SAFE_STOPPED

    def submit_command(self, command: PhysicalCommand) -> PhysicalCommandResult:
        with self._submit_lock:
            # Lifecycle checks
            if self._worker_state != WorkerState.RUNNING:
                if self._worker_state == WorkerState.SHUTDOWN_REQUESTED:
                    return PhysicalCommandResult(status=SubmissionStatus.SHUTTING_DOWN, details={})
                return PhysicalCommandResult(status=SubmissionStatus.WORKER_UNAVAILABLE, details={})

            if not self._worker_thread or not self._worker_thread.is_alive():
                self._worker_state = WorkerState.UNAVAILABLE
                return PhysicalCommandResult(status=SubmissionStatus.WORKER_UNAVAILABLE, details={})

            # Validity checks
            if self._safety_state != EndpointSafetyState.ACTIVE:
                raise CapabilityUnauthorizedError(
                    f"Endpoint {self._endpoint_id} is not in ACTIVE state (current: {self._safety_state.name})"
                )
            if not self._active_lease:
                raise CapabilityUnauthorizedError(f"Endpoint {self._endpoint_id} has no active lease")
            if command.session_id != self._active_lease.session_id:
                raise CapabilityUnauthorizedError(f"Session ID mismatch: {command.session_id} != {self._active_lease.session_id}")
            if command.lifecycle_generation != self._active_lease.lifecycle_generation:
                raise CapabilityUnauthorizedError("Lifecycle generation mismatch")
            if command.endpoint_lease_generation != self._active_lease.endpoint_lease_generation:
                raise CapabilityUnauthorizedError("Lease generation mismatch")

            # Sequence checks
            if command.command_sequence <= self._last_accepted_sequence:
                return PhysicalCommandResult(
                    status=SubmissionStatus.DUPLICATE_REJECTED,
                    details={"actuated_sequence": self._last_accepted_sequence}
                )

            self._last_accepted_sequence = command.command_sequence

            # Bounded handoff
            try:
                self._command_queue.put_nowait(command)
            except queue.Full:
                return PhysicalCommandResult(status=SubmissionStatus.QUEUE_FULL, details={})

            return PhysicalCommandResult(
                status=SubmissionStatus.ACCEPTED,
                details={
                    "actuated_sequence": self._last_accepted_sequence,
                    "operation": command.operation
                }
            )

    def _execution_loop(self) -> None:
        """Exclusive execution plane worker."""
        while not self._shutdown_event.is_set():
            try:
                command = self._command_queue.get(timeout=0.1)
            except queue.Empty:
                continue

            # Stale-at-dequeue protection
            with self._submit_lock:
                is_stale = False
                if not self._active_lease:
                    is_stale = True
                elif command.session_id != self._active_lease.session_id:
                    is_stale = True
                elif command.lifecycle_generation != self._active_lease.lifecycle_generation:
                    is_stale = True
                elif command.endpoint_lease_generation != self._active_lease.endpoint_lease_generation:
                    is_stale = True
                elif self._safety_state != EndpointSafetyState.ACTIVE:
                    is_stale = True

                if is_stale:
                    self._command_queue.task_done()
                    continue
                    
                # Stop requested before execution started?
                if command.execution_id in self._stop_requests:
                    # We just skip physical execution
                    self._command_queue.task_done()
                    self._stop_requests.discard(command.execution_id)
                    continue
                    
            try:
                # Simulated blocking physical execution
                for _ in range(5):
                    if command.execution_id in self._stop_requests:
                        break
                    if self._shutdown_event.is_set():
                        break
                    time.sleep(0.01)  # Simulated blocking step
                    
            except Exception:
                # Expose failure according to rules
                pass
            finally:
                self._command_queue.task_done()
                with self._submit_lock:
                    self._stop_requests.discard(command.execution_id)

    # Test configuration hooks
    def inject_hardware_interlock(self) -> None:
        self._safety_state = EndpointSafetyState.HARDWARE_INTERLOCKED

    @property
    def active_lease(self) -> Optional[EndpointLease]:
        return self._active_lease


class SimulatedDevice(IDevice):
    """Concrete, hardware-independent simulated device for testing and integration."""

    def __init__(
        self,
        device_id: str,
        physical_id: str,
        device_type: DeviceType = DeviceType.SIMULATED_GENERIC,
        capabilities: Tuple[DeviceCapability, ...] = (),
    ) -> None:
        self._device_id = device_id
        self._physical_id = physical_id
        self._device_type = device_type
        self._capabilities = tuple(capabilities)
        self._state: DeviceState = DeviceState.UNREGISTERED
        self._acquired_resource_ids: list[str] = []
        self._fail_on_initialize: Optional[Exception] = None
        self._fail_on_start: Optional[Exception] = None
        self._fail_on_stop: Optional[Exception] = None
        self._fail_on_health: Optional[Exception] = None
        self._custom_health: Optional[DeviceHealth] = None
        self._endpoints: Tuple[IPhysicalEndpoint, ...] = ()

    @property
    def device_id(self) -> str:
        return self._device_id

    @property
    def physical_id(self) -> str:
        return self._physical_id

    @property
    def device_type(self) -> DeviceType:
        return self._device_type

    @property
    def state(self) -> DeviceState:
        return self._state

    @property
    def capabilities(self) -> Tuple[DeviceCapability, ...]:
        return self._capabilities

    @property
    def endpoints(self) -> Tuple[IPhysicalEndpoint, ...]:
        return self._endpoints

    def initialize(self, accessor: DeviceResourceAccessor) -> None:
        if self._fail_on_initialize is not None:
            raise self._fail_on_initialize
        handle = accessor.acquire("primary")
        self._acquired_resource_ids.append(handle.resource_id)

    def start(self) -> None:
        if self._fail_on_start is not None:
            raise self._fail_on_start

    def stop(self, accessor: DeviceResourceAccessor) -> None:
        if self._fail_on_stop is not None:
            # Optionally record failure
            for r_id in list(self._acquired_resource_ids):
                accessor.mark_release_failed(r_id, str(self._fail_on_stop))
            raise self._fail_on_stop

        for r_id in list(self._acquired_resource_ids):
            accessor.release(r_id)
        self._acquired_resource_ids.clear()

    def health(self) -> DeviceHealth:
        if self._fail_on_health is not None:
            raise self._fail_on_health
        if self._custom_health is not None:
            return self._custom_health
        return DeviceHealth(
            device_id=self._device_id,
            status=HealthStatus.HEALTHY,
            message="Simulated device operating normally",
            timestamp_utc=datetime.now(timezone.utc).isoformat(),
            diagnostics=MappingProxyType({}),
        )

    # Test configuration hooks
    def set_fail_on_initialize(self, exc: Optional[Exception]) -> None:
        self._fail_on_initialize = exc

    def set_fail_on_start(self, exc: Optional[Exception]) -> None:
        self._fail_on_start = exc

    def set_fail_on_stop(self, exc: Optional[Exception]) -> None:
        self._fail_on_stop = exc

    def set_fail_on_health(self, exc: Optional[Exception]) -> None:
        self._fail_on_health = exc

    def set_custom_health(self, health: Optional[DeviceHealth]) -> None:
        self._custom_health = health

    def set_endpoints(self, endpoints: Tuple[IPhysicalEndpoint, ...]) -> None:
        self._endpoints = endpoints
