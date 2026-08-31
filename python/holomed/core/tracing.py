"""M00.4 Causal tracing — derive reply and event envelopes.

``derive_reply``:
  * Preserves ``correlation_id`` from the request.
  * Sets ``causation_id = request.message_id``.
  * Sets ``target = request.source``.

``derive_event``:
  * With cause: inherits ``correlation_id``; ``causation_id = cause.message_id``.
  * Root event: new UUIDv4 ``correlation_id``; ``causation_id = None``.

Never mutates input envelopes.
All generated envelopes are validated against M00.2 requirements.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from holomed.protocol.models import (
    CURRENT_PROTOCOL_VERSION,
    MessageEnvelope,
    MessageType,
)
from holomed.protocol.validation import validate_envelope


def _uuid4() -> str:
    return str(uuid.uuid4())


def _now_utc(now: Optional[datetime] = None) -> str:
    ts = now or datetime.now(timezone.utc)
    return ts.strftime("%Y-%m-%dT%H:%M:%S.%fZ")


class CausalTracer:
    """Derives causally-linked message envelopes without mutating inputs."""

    @staticmethod
    def derive_reply(
        request: MessageEnvelope,
        responder_source: str,
        payload: Optional[Dict[str, Any]] = None,
        *,
        message_type: MessageType = MessageType.RESPONSE,
        message_name: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
        now_utc: Optional[datetime] = None,
    ) -> MessageEnvelope:
        """Create a reply (RESPONSE or ERROR) envelope causally linked to *request*.

        Invariants (caller cannot override):
          * ``correlation_id = request.correlation_id``
          * ``causation_id  = request.message_id``
          * ``target        = request.source``
        """
        name = message_name or f"{request.message_name}.response"
        envelope = MessageEnvelope(
            protocol_version=request.protocol_version,
            message_id=_uuid4(),
            correlation_id=request.correlation_id,
            causation_id=request.message_id,
            message_type=message_type,
            message_name=name,
            source=responder_source,
            target=request.source,
            timestamp_utc=_now_utc(now_utc),
            payload=dict(payload) if payload else {},
            metadata=dict(metadata) if metadata else {},
        )
        validate_envelope(envelope)
        return envelope

    @staticmethod
    def derive_event(
        source: str,
        message_name: str,
        *,
        cause: Optional[MessageEnvelope] = None,
        payload: Optional[Dict[str, Any]] = None,
        metadata: Optional[Dict[str, Any]] = None,
        now_utc: Optional[datetime] = None,
    ) -> MessageEnvelope:
        """Create an EVENT envelope, optionally derived from *cause*.

        * With *cause*: inherits ``correlation_id``; ``causation_id = cause.message_id``.
        * Root event (no cause): new UUIDv4 ``correlation_id``; ``causation_id = None``.
        """
        msg_id = _uuid4()
        if cause is not None:
            correlation_id = cause.correlation_id
            causation_id = cause.message_id
        else:
            correlation_id = msg_id
            causation_id = None

        envelope = MessageEnvelope(
            protocol_version=CURRENT_PROTOCOL_VERSION,
            message_id=msg_id,
            correlation_id=correlation_id,
            causation_id=causation_id,
            message_type=MessageType.EVENT,
            message_name=message_name,
            source=source,
            target=None,
            timestamp_utc=_now_utc(now_utc),
            payload=dict(payload) if payload else {},
            metadata=dict(metadata) if metadata else {},
        )
        validate_envelope(envelope)
        return envelope
