"""HoloMed AI - Telemetry transport and non-blocking event buffers."""

import queue
from typing import List

from holomed.devices.interfaces import IExecutionTelemetryPublisher
from holomed.devices.models import CommandState, ExecutionTelemetryEvent


class TelemetryPublisher(IExecutionTelemetryPublisher):
    """Non-blocking publisher tied to a specific TelemetryTransport."""

    # Set of CommandState that classify as critical terminal events
    _CRITICAL_STATES = frozenset({
        CommandState.COMPLETED,
        CommandState.FAILED,
        CommandState.PREEMPTED,
        CommandState.INTERLOCKED,
        CommandState.FAULTED_UNKNOWN,
    })

    def __init__(self, transport: "TelemetryTransport") -> None:
        self._transport = transport

    def publish(self, event: ExecutionTelemetryEvent) -> None:
        """Publish an event to the transport without blocking.

        Guarantees O(1) non-blocking behavior.
        Will silently drop or set critical loss flag on queue-full or errors,
        preventing physical worker starvation.
        """
        if self._transport.is_shutdown:
            return

        try:
            if not isinstance(event, ExecutionTelemetryEvent):
                # Malformed event - fail deterministically without crashing producer
                return

            is_critical = event.observed_state in self._CRITICAL_STATES

            if is_critical:
                self._transport._put_critical(event)
            else:
                self._transport._put_normal(event)

        except Exception:
            # Total failure isolation: Publisher exception must not crash execution worker
            pass


class TelemetryTransport:
    """Bounded, non-blocking telemetry transport buffer.

    Owns isolated normal and critical queues. Exposes publisher interface
    to producers and drain operations to consumers.
    """

    def __init__(self, normal_capacity: int = 10000, critical_capacity: int = 100) -> None:
        self._normal_queue: queue.Queue[ExecutionTelemetryEvent] = queue.Queue(maxsize=normal_capacity)
        self._critical_queue: queue.Queue[ExecutionTelemetryEvent] = queue.Queue(maxsize=critical_capacity)
        self._is_shutdown = False
        self._critical_loss_occurred = False
        self._publisher = TelemetryPublisher(self)

    @property
    def publisher(self) -> IExecutionTelemetryPublisher:
        """Returns the isolated non-blocking publisher interface."""
        return self._publisher

    @property
    def is_shutdown(self) -> bool:
        return self._is_shutdown

    @property
    def has_critical_loss(self) -> bool:
        """Indicates if one or more critical terminal events were dropped due to saturation."""
        return self._critical_loss_occurred

    def shutdown(self) -> None:
        """Atomically halts new publications. Existing queued items remain."""
        self._is_shutdown = True

    def _put_normal(self, event: ExecutionTelemetryEvent) -> None:
        if self._is_shutdown:
            return
        try:
            self._normal_queue.put_nowait(event)
        except queue.Full:
            # Normal queue full: silently drop and return immediately
            pass

    def _put_critical(self, event: ExecutionTelemetryEvent) -> None:
        if self._is_shutdown:
            return
        try:
            self._critical_queue.put_nowait(event)
        except queue.Full:
            # Critical queue full: deterministic rejection, mark transport as having lost critical truth
            self._critical_loss_occurred = True

    def drain_normal(self) -> List[ExecutionTelemetryEvent]:
        """Drain currently available normal events."""
        events = []
        while not self._normal_queue.empty():
            try:
                events.append(self._normal_queue.get_nowait())
            except queue.Empty:
                break
        return events

    def drain_critical(self) -> List[ExecutionTelemetryEvent]:
        """Drain currently available critical events."""
        events = []
        while not self._critical_queue.empty():
            try:
                events.append(self._critical_queue.get_nowait())
            except queue.Empty:
                break
        return events
