# -*- coding: utf-8 -*-
"""Client Connection Management and Bounded Egress Backpressure for M11."""

from __future__ import annotations

import collections
from datetime import datetime, timezone
from typing import List, Optional, Tuple

from holomed.gateway.exceptions import (
    GatewayLifecycleError,
    GatewayQueueOverflowError,
    GatewayTransportError,
)
from holomed.gateway.framing import FrameParser, encode_frame
from holomed.gateway.models import (
    MAX_CLIENT_QUEUE,
    ClientQueueStats,
    ClientRole,
    ClientSession,
    ConnectionState,
    FramePriority,
)
from holomed.gateway.transports import ITransport
from holomed.protocol.codec import serialize_envelope_bytes
from holomed.protocol.models import MessageEnvelope, MessageType

CRITICAL_TOPICS: frozenset[str] = frozenset(
    [
        "workflow.aborted",
        "workflow.confirmation.requested",
        "workflow.interlock.tripped",
        "gateway.error",
    ]
)


def classify_frame_priority(envelope: MessageEnvelope) -> FramePriority:
    """Classify message envelope priority for backpressure eviction."""
    if envelope.message_type == MessageType.ERROR or envelope.message_name in CRITICAL_TOPICS:
        return FramePriority.CRITICAL_RELIABLE
    return FramePriority.TRANSIENT_VISUAL


class GatewayConnection:
    """Manages an active external client transport stream with bounded queues."""

    def __init__(self, transport: ITransport) -> None:
        self._transport = transport
        self._state = ConnectionState.CONNECTING
        self._parser = FrameParser()
        self._egress_queue: collections.deque[Tuple[MessageEnvelope, FramePriority]] = collections.deque()
        self._session: Optional[ClientSession] = None
        self._last_activity_utc = datetime.now(timezone.utc).isoformat()
        self._last_sequence_number: int = -1

        # Metrics
        self._enqueued_total: int = 0
        self._dropped_frames_total: int = 0
        self._critical_messages_total: int = 0

    @property
    def transport(self) -> ITransport:
        return self._transport

    @property
    def state(self) -> ConnectionState:
        return self._state

    @property
    def session(self) -> Optional[ClientSession]:
        return self._session

    @property
    def client_id(self) -> Optional[str]:
        return self._session.client_id if self._session else None

    @property
    def client_role(self) -> Optional[ClientRole]:
        return self._session.client_role if self._session else None

    @property
    def queue_depth(self) -> int:
        return len(self._egress_queue)

    @property
    def stats(self) -> ClientQueueStats:
        return ClientQueueStats(
            client_id=self.client_id or "unauthenticated",
            queue_depth=len(self._egress_queue),
            enqueued_total=self._enqueued_total,
            dropped_frames_total=self._dropped_frames_total,
            critical_messages_total=self._critical_messages_total,
        )

    def attach_session(self, session: ClientSession) -> None:
        """Bind an authenticated client session to this connection."""
        if self._state != ConnectionState.CONNECTING:
            raise GatewayLifecycleError(f"Cannot attach session in state {self._state.value}")
        self._session = session
        self._state = ConnectionState.ACTIVE

    def enqueue_envelope(self, envelope: MessageEnvelope) -> None:
        """Enqueue an outgoing envelope with bounded queue and priority eviction."""
        if self._state in (ConnectionState.CLOSING, ConnectionState.CLOSED):
            return

        priority = classify_frame_priority(envelope)
        self._enqueued_total += 1
        if priority == FramePriority.CRITICAL_RELIABLE:
            self._critical_messages_total += 1

        if len(self._egress_queue) >= MAX_CLIENT_QUEUE:
            # Attempt to evict oldest TRANSIENT_VISUAL frame
            evicted = False
            for idx, (_, p) in enumerate(self._egress_queue):
                if p == FramePriority.TRANSIENT_VISUAL:
                    del self._egress_queue[idx]
                    self._dropped_frames_total += 1
                    evicted = True
                    break

            if not evicted:
                # Queue is saturated strictly with critical messages
                self._state = ConnectionState.CLOSING
                self.close()
                raise GatewayQueueOverflowError(
                    f"Egress queue for client {self.client_id!r} saturated with {MAX_CLIENT_QUEUE} critical messages"
                )

        self._egress_queue.append((envelope, priority))

    def flush_egress(self) -> int:
        """Serialize and transmit all enqueued egress frames over the transport."""
        if self._transport.is_closed:
            self._state = ConnectionState.CLOSED
            return 0

        flushed = 0
        while self._egress_queue:
            envelope, _ = self._egress_queue.popleft()
            payload_bytes = serialize_envelope_bytes(envelope)
            frame_bytes = encode_frame(payload_bytes)
            self._transport.send(frame_bytes)
            flushed += 1

        self._last_activity_utc = datetime.now(timezone.utc).isoformat()
        return flushed

    def read_ingress(self, max_bytes: int = 65536) -> List[bytes]:
        """Read bytes from transport and return reassembled frame payloads."""
        if self._transport.is_closed:
            self._state = ConnectionState.CLOSED
            return []

        chunk = self._transport.receive(max_bytes)
        if not chunk:
            if self._transport.is_closed:
                self._state = ConnectionState.CLOSED
            return []

        self._last_activity_utc = datetime.now(timezone.utc).isoformat()
        return self._parser.feed(chunk)

    def validate_sequence_number(self, sequence_number: int) -> None:
        """Enforce strict monotonic increasing sequence numbers from client."""
        if sequence_number <= self._last_sequence_number:
            raise GatewayLifecycleError(
                f"Non-monotonic sequence number: got {sequence_number}, last was {self._last_sequence_number}"
            )
        self._last_sequence_number = sequence_number

    def close(self) -> None:
        """Close connection and underlying transport."""
        if self._state != ConnectionState.CLOSED:
            self._state = ConnectionState.CLOSED
            self._egress_queue.clear()
            self._parser.reset()
            self._transport.close()
