"""HoloMed AI - Unity Virtual Device implementing IDevice and IPhysicalEndpoint."""

import asyncio
import queue
import threading
import time
import uuid
import logging
from datetime import datetime, timezone
from typing import Tuple, Optional, Callable

from holomed.devices.interfaces import (
    IDevice,
    IPhysicalEndpoint,
    DeviceResourceAccessor,
    IExecutionResolutionGate,
    IExecutionTelemetryPublisher,
)
from holomed.devices.models import (
    CapabilityCategory,
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
    CommandState,
    ExecutionTelemetryEvent,
    EventSourceAuthority,
)
from holomed.runtime.models import HealthStatus
from holomed.devices.endpoints.unity_ipc import UnityIpcServer, AnatomyIpcEnvelope
from holomed.devices.control.exceptions import CapabilityUnauthorizedError

logger = logging.getLogger(__name__)

class UnityVirtualEndpoint(IPhysicalEndpoint):
    def __init__(
        self,
        endpoint_id: str,
        device_id: str,
        server: UnityIpcServer,
        gate: Optional[IExecutionResolutionGate] = None,
        publisher: Optional[IExecutionTelemetryPublisher] = None,
    ) -> None:
        self._endpoint_id = endpoint_id
        self._device_id = device_id
        self._server = server
        self._gate = gate
        self._publisher = publisher
        
        self._safety_state = EndpointSafetyState.SAFE_STOPPED
        self._endpoint_state = EndpointState.READY
        self._active_lease: Optional[EndpointLease] = None
        self._endpoint_epoch: Optional[int] = None
        
        self._submit_lock = threading.Lock()
        self._last_accepted_sequence = 0
        self._epoch_provider: Optional[Callable[[], int]] = None
        
        self._execution_event_seq = 0
        self._loop: Optional[asyncio.AbstractEventLoop] = None

    def set_event_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        self._loop = loop

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
        return self._endpoint_state

    @property
    def active_lease(self) -> Optional[EndpointLease]:
        return self._active_lease

    def request_stop(self, execution_id: str) -> None:
        if self._loop and not self._loop.is_closed():
            asyncio.run_coroutine_threadsafe(self._send_stop(execution_id), self._loop)

    async def _send_stop(self, execution_id: str) -> None:
        env = AnatomyIpcEnvelope(
            session_id=uuid.UUID(self._active_lease.session_id) if self._active_lease else uuid.uuid4(),
            correlation_id=uuid.uuid4(), # Cannot use execution_id as UUID if it's not a valid UUID format, so generate one
            sequence_number=self._last_accepted_sequence + 1,
            geometry_version=self._server._canonical_geometry_version,
            message_type="stop_command",
            payload={"execution_id": execution_id}
        )
        await self._broadcast(env)

    async def _broadcast(self, env: AnatomyIpcEnvelope) -> None:
        for conn in list(self._server._active_connections):
            try:
                await conn.send(env.model_dump_json())
            except Exception as e:
                logger.error(f"Error broadcasting to Unity: {e}")

    def emergency_stop(self) -> EndpointSafetyState:
        with self._submit_lock:
            self._safety_state = EndpointSafetyState.SAFE_STOPPED
            self._active_lease = None
        return self._safety_state

    def acquire_lease(self, lease: EndpointLease) -> None:
        with self._submit_lock:
            if self._safety_state == EndpointSafetyState.HARDWARE_INTERLOCKED:
                raise RuntimeError("Hardware is interlocked")
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

    def set_endpoint_epoch(self, epoch_id: int) -> None:
        with self._submit_lock:
            self._endpoint_epoch = epoch_id

    def set_epoch_fence(self, epoch_provider: Callable[[], int]) -> None:
        self._epoch_provider = epoch_provider

    def submit_command(self, command: PhysicalCommand) -> PhysicalCommandResult:
        with self._submit_lock:
            if self._endpoint_epoch is not None and command.device_epoch != self._endpoint_epoch:
                return PhysicalCommandResult(status=SubmissionStatus.REJECTED, details={"error": "Stale device epoch"})
                
            if self._endpoint_state == EndpointState.QUARANTINED:
                raise CapabilityUnauthorizedError("Endpoint QUARANTINED")
            if self._safety_state != EndpointSafetyState.ACTIVE:
                raise CapabilityUnauthorizedError("Endpoint not ACTIVE")
            if not self._active_lease:
                raise CapabilityUnauthorizedError("No active lease")
            if command.session_id != self._active_lease.session_id:
                raise CapabilityUnauthorizedError("Session ID mismatch")
            if command.lifecycle_generation != self._active_lease.lifecycle_generation:
                raise CapabilityUnauthorizedError("Lifecycle mismatch")
            if command.endpoint_lease_generation != self._active_lease.endpoint_lease_generation:
                raise CapabilityUnauthorizedError("Lease generation mismatch")
                
            if command.command_sequence <= self._last_accepted_sequence:
                return PhysicalCommandResult(status=SubmissionStatus.DUPLICATE_REJECTED, details={})
                
            if self._epoch_provider:
                current = self._epoch_provider()
                if current != command.controller_epoch:
                    return PhysicalCommandResult(status=SubmissionStatus.REJECTED, details={"error": "Stale controller epoch"})
                    
            self._last_accepted_sequence = command.command_sequence

            if self._gate:
                claimed = self._gate.claim_execution_ownership(command.execution_id, command.lifecycle_generation)
                if not claimed:
                    return PhysicalCommandResult(status=SubmissionStatus.REJECTED, details={"error": "Timeout claimed first"})

            if self._loop and not self._loop.is_closed():
                asyncio.run_coroutine_threadsafe(self._dispatch_command(command), self._loop)
            
            self._publish_telemetry(command, CommandState.RUNNING)
            return PhysicalCommandResult(status=SubmissionStatus.ACCEPTED, details={"actuated_sequence": self._last_accepted_sequence})

    async def _dispatch_command(self, command: PhysicalCommand) -> None:
        try:
            # We attempt to parse the execution_id as UUID, fallback if impossible.
            try:
                corr_id = uuid.UUID(command.execution_id)
            except ValueError:
                corr_id = uuid.uuid4()

            env = AnatomyIpcEnvelope(
                session_id=uuid.UUID(command.session_id),
                correlation_id=corr_id,
                sequence_number=command.command_sequence,
                geometry_version=self._server._canonical_geometry_version,
                message_type="physical_command",
                payload={"action": command.operation, "data": dict(command.parameters)}
            )
            await self._broadcast(env)
            # Fake immediate completion telemetry in the bridge
            self._publish_telemetry(command, CommandState.COMPLETED)
        except Exception:
            self._publish_telemetry(command, CommandState.FAULTED_UNKNOWN)

    def _publish_telemetry(self, command: PhysicalCommand, state: CommandState) -> None:
        if not self._publisher:
            return
        self._execution_event_seq += 1
        try:
            event = ExecutionTelemetryEvent(
                evidence_generation=1,
                cryptographic_signature=None,
                fencing_challenge=None,
                event_id=str(uuid.uuid4()),
                endpoint_id=self._endpoint_id,
                session_id=command.session_id,
                lifecycle_generation=command.lifecycle_generation,
                endpoint_lease_generation=command.endpoint_lease_generation,
                execution_id=command.execution_id,
                command_sequence=command.command_sequence,
                event_sequence=self._execution_event_seq,
                event_type="STATE_OBSERVATION",
                observed_state=state,
                source_authority=EventSourceAuthority.ENDPOINT_ADAPTER,
                source_origin="UnityVirtualDevice",
                timestamp_utc=datetime.now(timezone.utc).isoformat(),
                payload={"evidence_type": "DRIVER_ASSERTED_SOFTWARE_EVIDENCE"},
            )
            self._publisher.publish(event)
        except Exception:
            pass


class UnityVirtualDevice(IDevice):
    def __init__(
        self,
        device_id: str,
        host: str,
        port: int,
        gate: Optional[IExecutionResolutionGate] = None,
        publisher: Optional[IExecutionTelemetryPublisher] = None,
    ):
        self._device_id = device_id
        self._physical_id = f"unity_ipc://{host}:{port}"
        self._state = DeviceState.UNREGISTERED
        self._capabilities = (
            DeviceCapability(
                capability_id="grasp",
                category=CapabilityCategory.CONTROL,
                parameters={},
                requires_physical_endpoint=True,
                target_endpoint_id="unity_ep_1"
            ),
        )
        self._server = UnityIpcServer(host, port)
        self._endpoint = UnityVirtualEndpoint("unity_ep_1", device_id, self._server, gate, publisher)
        self._endpoints = (self._endpoint,)
        self._worker_thread: Optional[threading.Thread] = None
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._shutdown_event = threading.Event()
        self._current_epoch = 1

    @property
    def device_id(self) -> str:
        return self._device_id

    @property
    def physical_id(self) -> str:
        return self._physical_id

    @property
    def device_type(self) -> DeviceType:
        return DeviceType.SIMULATED_GENERIC

    @property
    def current_epoch(self) -> int:
        return self._current_epoch

    @property
    def state(self) -> DeviceState:
        return self._state

    @property
    def capabilities(self) -> Tuple[DeviceCapability, ...]:
        return self._capabilities

    @property
    def endpoints(self) -> Tuple[IPhysicalEndpoint, ...]:
        return self._endpoints

    def _run_server(self) -> None:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        self._loop = loop

        async def run_server_task() -> None:
            await self._server.start()
            while not self._shutdown_event.is_set():
                await asyncio.sleep(0.05)

        async def graceful_shutdown() -> None:
            """Perform full async teardown within the worker loop."""
            # 1. Close the websockets server listener (stops accepting new conns)
            if self._server.server is not None:
                self._server.server.close()
                await self._server.server.wait_closed()

            # 2. Close all active client connections
            for conn in list(self._server._active_connections):
                try:
                    await conn.close()
                except Exception:
                    pass
            self._server._active_connections.clear()
            self._server.server = None

            # 3. Cancel any remaining tasks on this loop
            pending = [t for t in asyncio.all_tasks(loop) if t is not asyncio.current_task()]
            for task in pending:
                task.cancel()
            if pending:
                await asyncio.gather(*pending, return_exceptions=True)

        try:
            loop.run_until_complete(run_server_task())
        except Exception as e:
            logger.error(f"UnityVirtualDevice server error: {e}")
        finally:
            try:
                loop.run_until_complete(graceful_shutdown())
            except Exception:
                pass
            loop.close()

    def initialize(self, accessor: DeviceResourceAccessor) -> None:
        self._acquired = accessor.acquire("unity_socket")
        self._state = DeviceState.INITIALIZING

    def start(self) -> None:
        self._shutdown_event.clear()
        self._worker_thread = threading.Thread(target=self._run_server, daemon=True)
        self._worker_thread.start()
        while self._loop is None:
            time.sleep(0.01)
        self._endpoint.set_event_loop(self._loop)
        self._state = DeviceState.READY

    def stop(self, accessor: DeviceResourceAccessor) -> None:
        # Signal the worker thread to exit the polling loop
        self._shutdown_event.set()

        # Wait for the worker thread to complete its graceful_shutdown sequence
        if self._worker_thread and self._worker_thread.is_alive():
            self._worker_thread.join(timeout=5.0)

        self._loop = None
        self._state = DeviceState.STOPPED
        accessor.release(self._acquired.resource_id)

    def health(self) -> DeviceHealth:
        return DeviceHealth(
            device_id=self._device_id,
            status=HealthStatus.HEALTHY if self._state == DeviceState.READY else HealthStatus.DEGRADED,
            message="Unity IPC Active",
            timestamp_utc=datetime.now(timezone.utc).isoformat(),
            diagnostics={}
        )
