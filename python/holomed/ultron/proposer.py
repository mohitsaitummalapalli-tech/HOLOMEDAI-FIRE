"""Phase E: Ultron Proposal Layer.

Consumes validated UltronIntent and generates untrusted CommandRequest proposals.
Does NOT execute commands. Does NOT supply authoritative metadata.
"""

import logging
import time
from typing import Optional, Any, Mapping

from holomed.input.models import UltronIntent
from holomed.input.intent import ACTION_GRASP, ACTION_RELEASE, ACTION_CANCEL
from holomed.xr.unity_bridge_models import CommandRequest
from holomed.core.models import MessageEnvelope

logger = logging.getLogger(__name__)


class UltronProposer:
    """Converts validated UltronIntent into untrusted CommandRequest proposals.

    Responsible for deduplication within a correlation lineage and mapping
    intent semantics to valid command actions.
    """

    def __init__(self):
        self._last_correlation_id: Optional[str] = None
        self._last_proposed_action: Optional[str] = None

    def process_intent(self, intent: UltronIntent, target_device_id: str) -> Optional[CommandRequest]:
        """Convert a validated intent into a command proposal if actionable and novel.

        Args:
            intent: The validated intent from the perception layer.
            target_device_id: The device to target with the proposal.

        Returns:
            An untrusted CommandRequest proposal, or None if the intent
            is not actionable or is a duplicate.
        """
        # Validate intent
        if not intent or not isinstance(intent, UltronIntent):
            return None

        if not target_device_id:
            return None

        # Check lineage reset
        if intent.correlation_id != self._last_correlation_id:
            self._last_correlation_id = intent.correlation_id
            self._last_proposed_action = None

        # Deduplication
        if intent.action == self._last_proposed_action:
            # We already proposed this state for this interaction lineage
            return None

        # Map intent action to command payload
        action_name = None
        payload: Mapping[str, Any] = {}

        if intent.action == ACTION_GRASP:
            # Supported: grasping / holding
            action_name = "sys.input.interact"
            payload = {"state": "pressed", "intent_value": intent.value}
        elif intent.action == ACTION_RELEASE:
            # Supported: releasing
            action_name = "sys.input.interact"
            payload = {"state": "released", "intent_value": intent.value}
        elif intent.action == ACTION_CANCEL:
            # Supported: tracking lost -> release/cancel interaction safely
            action_name = "sys.input.interact"
            payload = {"state": "cancelled", "reason": "tracking_loss"}
        else:
            # Unsupported interactions (e.g. rotate, pinch, swipe, voice)
            return None

        # Generate the untrusted proposal.
        # Notice we DO NOT generate device_epoch, controller_epoch,
        # physical_operation_id, command_nonce, or EndpointLease here.
        proposal = CommandRequest(
            correlation_id=intent.correlation_id,
            command_timestamp_ns=time.monotonic_ns(),
            device_id=target_device_id,
            action=action_name,
            payload=payload,
        )

        self._last_proposed_action = intent.action
        return proposal

def create_envelope_from_proposal(proposal: CommandRequest, session_id: str, lifecycle_gen: int, execution_id: str, command_nonce: str) -> 'MessageEnvelope':
    """Wraps an untrusted proposal into a MessageEnvelope for the DeviceControlManager.

    This function bridges the Ultron Proposer to the authoritative DeviceControlManager.
    The caller must provide session context for admission validation.
    """
    from datetime import datetime, timezone
    from holomed.core.models import MessageEnvelope
    from holomed.protocol.models import MessageType
    import uuid

    # The payload combines the untrusted proposal data with the caller's session context
    payload = dict(proposal.payload)
    payload["device_id"] = proposal.device_id
    payload["command"] = proposal.action
    # Add session context for admission
    payload["session_id"] = session_id
    payload["session_lifecycle_generation"] = lifecycle_gen
    payload["execution_id"] = execution_id
    payload["command_nonce"] = command_nonce

    return MessageEnvelope(
        protocol_version="1.0",
        message_id=str(uuid.uuid4()),
        correlation_id=proposal.correlation_id,
        causation_id=None,
        message_type=MessageType.COMMAND,
        message_name="device.command",
        source="ultron.proposer",
        target="device_control_manager",
        timestamp_utc=datetime.now(timezone.utc).isoformat(),
        payload=payload,
        metadata={"command_timestamp_ns": proposal.command_timestamp_ns},
    )
