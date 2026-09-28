"""Tests for Phase E: Ultron Proposal / Command Boundary."""

import time
from typing import Mapping, Any

import pytest
from unittest.mock import MagicMock

from holomed.input.models import UltronIntent
from holomed.input.intent import ACTION_GRASP, ACTION_RELEASE, ACTION_CANCEL
from holomed.ultron.proposer import UltronProposer, create_envelope_from_proposal
from holomed.xr.unity_bridge_models import CommandRequest

# --- Proposer Unit Tests ---

def test_proposer_consumes_validated_intent_only():
    proposer = UltronProposer()
    
    # Invalid input
    assert proposer.process_intent(None, "dev_xr_headset_01") is None  # type: ignore
    assert proposer.process_intent("not_an_intent", "dev_xr_headset_01") is None  # type: ignore
    assert proposer.process_intent(UltronIntent("c1", 0, ACTION_GRASP, 1.0, 1.0), "") is None

def test_proposer_generates_correct_command_request():
    proposer = UltronProposer()
    intent = UltronIntent(
        correlation_id="corr-99",
        intent_timestamp_ns=time.monotonic_ns(),
        action=ACTION_GRASP,
        value=0.8,
        confidence=0.9
    )
    
    t0 = time.monotonic_ns()
    proposal = proposer.process_intent(intent, "dev_xr_headset_01")
    t1 = time.monotonic_ns()
    
    assert isinstance(proposal, CommandRequest)
    assert proposal.correlation_id == "corr-99"
    assert proposal.device_id == "dev_xr_headset_01"
    assert proposal.action == "sys.input.interact"
    assert proposal.payload == {"state": "pressed", "intent_value": 0.8}
    
    # Timestamp is from the proposer, not the intent
    assert t0 <= proposal.command_timestamp_ns <= t1

def test_unsupported_intents_ignored():
    proposer = UltronProposer()
    intent = UltronIntent("c1", 0, "ROTATE", 1.0, 1.0)
    assert proposer.process_intent(intent, "dev_xr_headset_01") is None

def test_deduplication_and_lineage():
    proposer = UltronProposer()
    
    # First intent -> Proposal
    intent1 = UltronIntent("c1", 0, ACTION_GRASP, 0.8, 0.9)
    p1 = proposer.process_intent(intent1, "dev_xr_headset_01")
    assert p1 is not None
    
    # Second identical intent in same lineage -> None
    intent2 = UltronIntent("c1", 0, ACTION_GRASP, 0.9, 0.9)
    p2 = proposer.process_intent(intent2, "dev_xr_headset_01")
    assert p2 is None
    
    # State change in same lineage -> Proposal
    intent3 = UltronIntent("c1", 0, ACTION_RELEASE, 0.0, 0.9)
    p3 = proposer.process_intent(intent3, "dev_xr_headset_01")
    assert p3 is not None
    assert p3.payload["state"] == "released"
    
    # New lineage, same action -> Proposal
    intent4 = UltronIntent("c2", 0, ACTION_RELEASE, 0.0, 0.9)
    p4 = proposer.process_intent(intent4, "dev_xr_headset_01")
    assert p4 is not None

def test_latency_measurement():
    proposer = UltronProposer()
    intent = UltronIntent("c1", 0, ACTION_GRASP, 1.0, 1.0)
    
    t0 = time.perf_counter()
    p = proposer.process_intent(intent, "dev_xr_headset_01")
    t1 = time.perf_counter()
    
    # Latency should be microscopic
    latency_ms = (t1 - t0) * 1000.0
    assert latency_ms < 1.0
    assert p is not None


# --- Authoritative Admission Tests (Mocked DeviceControlManager Boundary) ---

def test_envelope_creation_cannot_inject_epochs():
    """Ultron generates CommandRequest which lacks authoritative fields.
    The adapter injects only caller session fields, not epochs/leases.
    """
    intent = UltronIntent("c1", 0, ACTION_GRASP, 1.0, 1.0)
    proposal = UltronProposer().process_intent(intent, "dev_xr_headset_01")
    assert proposal is not None
    
    env = create_envelope_from_proposal(
        proposal, 
        session_id="sess_123", 
        lifecycle_gen=1, 
        execution_id="exec_1",
        command_nonce="nonce_99"
    )
    
    assert env.correlation_id == "c1"
    assert env.payload["command"] == "sys.input.interact"
    assert env.payload["session_id"] == "sess_123"
    assert env.payload["command_nonce"] == "nonce_99"
    
    # Proof: Ultron did not supply epoch or physical_operation_id
    assert "device_epoch" not in env.payload
    assert "controller_epoch" not in env.payload
    assert "physical_operation_id" not in env.payload
