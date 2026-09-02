# -*- coding: utf-8 -*-
"""Unit Tests for M18 Safety Gate Models, Invariants & Immutability."""

from __future__ import annotations

from dataclasses import FrozenInstanceError
import pytest

from holomed.safety_gate.exceptions import SafetyGateValidationError
from holomed.safety_gate.models import (
    GateDecision,
    GateReasonCode,
    GateRequest,
    GateSeverity,
    GateStatusRecord,
    SafetyGateAction,
    SubsystemSnapshot,
)
from tests.unit.safety_gate.conftest import make_gate_request


class TestGateModelValidation:
    """Tests for model parameter validation."""

    def test_gate_request_valid(self) -> None:
        req = make_gate_request()
        assert req.session_id == "session-01"
        assert req.action == SafetyGateAction.TOOL_NAVIGATION
        assert req.sequence_number == 1

    def test_gate_request_invalid_session_id(self) -> None:
        with pytest.raises(SafetyGateValidationError, match="Invalid session_id"):
            make_gate_request(session_id="invalid session!@#$")

    def test_gate_request_invalid_sequence_number(self) -> None:
        with pytest.raises(SafetyGateValidationError, match="sequence_number"):
            make_gate_request(sequence_number=0)

    def test_gate_request_empty_timestamp(self) -> None:
        with pytest.raises(SafetyGateValidationError, match="now_utc"):
            make_gate_request(now_utc="   ")

    def test_gate_request_invalid_instrument_id(self) -> None:
        with pytest.raises(SafetyGateValidationError, match="Invalid instrument_id"):
            make_gate_request(instrument_id="inst with space!")

    def test_gate_request_invalid_trajectory_id(self) -> None:
        with pytest.raises(SafetyGateValidationError, match="Invalid target_trajectory_id"):
            make_gate_request(target_trajectory_id="traj with space!")

    def test_status_record_invalid_session_id(self) -> None:
        with pytest.raises(SafetyGateValidationError, match="Invalid session_id"):
            GateStatusRecord(
                session_id="invalid session!@#",
                decision=GateDecision.PERMITTED_CLEAR,
                severity=GateSeverity.NONE,
                reason_code=GateReasonCode.NONE,
                action=SafetyGateAction.TOOL_NAVIGATION,
                sequence_number=1,
                subsystem_snapshots=(),
                evaluated_at_utc="2025-01-01T12:00:00Z",
            )


class TestGateModelImmutability:
    """Tests for frozen dataclass immutability."""

    def test_gate_request_is_immutable(self) -> None:
        req = make_gate_request()
        with pytest.raises(FrozenInstanceError):
            req.session_id = "other"  # type: ignore

    def test_subsystem_snapshot_is_immutable(self) -> None:
        snap = SubsystemSnapshot("drift_service", "STABLE", 1, True, {})
        with pytest.raises(FrozenInstanceError):
            snap.state = "DRIFT_EXCEEDED"  # type: ignore

    def test_gate_status_record_is_immutable(self) -> None:
        rec = GateStatusRecord(
            session_id="session-01",
            decision=GateDecision.PERMITTED_CLEAR,
            severity=GateSeverity.NONE,
            reason_code=GateReasonCode.NONE,
            action=SafetyGateAction.TOOL_NAVIGATION,
            sequence_number=1,
            subsystem_snapshots=(),
            evaluated_at_utc="2025-01-01T12:00:00Z",
        )
        with pytest.raises(FrozenInstanceError):
            rec.decision = GateDecision.DENIED_CRITICAL  # type: ignore


class TestGateEnumCompleteness:
    """Tests confirming all enum values."""

    def test_actions(self) -> None:
        expected = {
            "TOOL_NAVIGATION",
            "TRAJECTORY_ALIGNMENT",
            "RECOVERY_REORIENTATION",
            "WORKFLOW_RESUMPTION",
            "TOOL_INVOCATION",
        }
        assert {a.value for a in SafetyGateAction} == expected

    def test_decisions(self) -> None:
        expected = {"PERMITTED_CLEAR", "PERMITTED_WITH_CAUTION", "DENIED_INTERLOCKED", "DENIED_CRITICAL"}
        assert {d.value for d in GateDecision} == expected

    def test_severities(self) -> None:
        expected = {"NONE", "WARNING", "BLOCKING", "CRITICAL"}
        assert {s.value for s in GateSeverity} == expected
