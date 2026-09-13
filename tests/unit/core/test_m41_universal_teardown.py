# -*- coding: utf-8 -*-
"""M41 Universal Stateful-Service Session Teardown Tests.

Validates that platform.session.stopped and platform.session.evicted lifecycle
events propagate teardown to all stateful services that own session-transient state.
"""

import pytest

from holomed.configuration.models import AppConfig, EnvironmentProfile, LogLevel
from holomed.core.dispatcher import MessageDispatcher
from holomed.protocol.builders import create_event
from holomed.runtime.context import RuntimeContext
from holomed.runtime.service import ServiceState


# ---------------------------------------------------------------------------
# Shared Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def test_runtime_context() -> RuntimeContext:
    app_cfg = AppConfig(
        app_name="HoloMed AI",
        environment=EnvironmentProfile.TESTING,
        host="localhost",
        port=8080,
        log_level=LogLevel.DEBUG,
        gemini_api_key="test_key",
        protocol_version="1.0",
    )
    return RuntimeContext(app_config=app_cfg, epoch_id=1)


@pytest.fixture
def dispatcher(test_runtime_context: RuntimeContext) -> MessageDispatcher:
    d = MessageDispatcher()
    d.initialize(test_runtime_context)
    # DO NOT start the dispatcher here. Services must register handlers during their initialize() first.
    yield d
    if getattr(d, "_state", None) == ServiceState.STARTED:
        d.stop()


def _emit_stopped(dispatcher: MessageDispatcher, session_id: str) -> None:
    """Emit a platform.session.stopped event through the dispatcher."""
    from holomed.platform._capability import _PlatformCapability, _INTERNAL_PLATFORM_KEY
    evt = create_event(
        "platform.session.stopped",
        "platform.session_manager",
        payload={"session_id": session_id, "epoch_id": 1},
    )
    cap = _PlatformCapability(
        internal_key=_INTERNAL_PLATFORM_KEY,
        service_instance_id=0,
        session_id=session_id,
        action="STOP",
    )
    dispatcher.dispatch_internal(evt, cap)


def _emit_evicted(dispatcher: MessageDispatcher, session_id: str) -> None:
    """Emit a platform.session.evicted event through the dispatcher."""
    from holomed.platform._capability import _PlatformCapability, _INTERNAL_PLATFORM_KEY
    evt = create_event(
        "platform.session.evicted",
        "platform.session_manager",
        payload={"session_id": session_id, "epoch_id": 1},
    )
    cap = _PlatformCapability(
        internal_key=_INTERNAL_PLATFORM_KEY,
        service_instance_id=0,
        session_id=session_id,
        action="EVICT",
    )
    dispatcher.dispatch_internal(evt, cap)


# ===========================================================================
# Drift Service
# ===========================================================================

@pytest.fixture
def drift_service(test_runtime_context, dispatcher):
    from holomed.drift.service import DriftService
    from holomed.registration.service import RegistrationService
    from holomed.planning.service import PlanningService

    planning = PlanningService(dispatcher=dispatcher)
    planning.initialize(test_runtime_context)

    reg = RegistrationService(dispatcher=dispatcher, planning_service=planning)
    reg.initialize(test_runtime_context)

    svc = DriftService(dispatcher=dispatcher, registration_service=reg)
    svc.initialize(test_runtime_context)

    dispatcher.start()

    planning.start()
    reg.start()
    svc.start()
    yield svc
    svc.stop()
    reg.stop()
    planning.stop()


def _seed_drift_session(drift_service, session_id: str) -> None:
    """Manually inject session-transient state into drift service for testing."""
    from holomed.drift.models import DriftState
    drift_service._session_states[session_id] = DriftState.READY
    drift_service._landmarks[session_id] = {}
    drift_service._latest_sequences[session_id] = 0
    drift_service._verified_landmarks[session_id] = set()


class TestDriftLifecycleTeardown:
    def test_stopped_clears_transient_state(self, drift_service, dispatcher):
        _seed_drift_session(drift_service, "sess_A")
        assert "sess_A" in drift_service._session_states
        _emit_stopped(dispatcher, "sess_A")
        assert "sess_A" not in drift_service._session_states
        assert "sess_A" not in drift_service._landmarks

    def test_evicted_clears_transient_state(self, drift_service, dispatcher):
        _seed_drift_session(drift_service, "sess_A")
        _emit_evicted(dispatcher, "sess_A")
        assert "sess_A" not in drift_service._session_states

    def test_idempotent_teardown(self, drift_service, dispatcher):
        _seed_drift_session(drift_service, "sess_A")
        _emit_stopped(dispatcher, "sess_A")
        _emit_evicted(dispatcher, "sess_A")  # second teardown — safe
        assert "sess_A" not in drift_service._session_states

    def test_teardown_preserves_other_session(self, drift_service, dispatcher):
        _seed_drift_session(drift_service, "sess_A")
        _seed_drift_session(drift_service, "sess_B")
        _emit_stopped(dispatcher, "sess_A")
        assert "sess_A" not in drift_service._session_states
        assert "sess_B" in drift_service._session_states


# ===========================================================================
# Execution Service
# ===========================================================================

@pytest.fixture
def execution_service(test_runtime_context, dispatcher):
    from holomed.execution.service import ClinicalExecutionGatewayService
    svc = ClinicalExecutionGatewayService(dispatcher=dispatcher)
    svc.initialize(test_runtime_context)
    dispatcher.start()
    svc.start()
    yield svc
    svc.stop()


def _seed_execution_session(execution_service, session_id: str) -> None:
    """Inject minimal session-transient state into execution service."""
    from holomed.execution.models import ExecutionStatus, NavigationExecutionResult
    from holomed.safety_gate.models import GateDecision, GateReasonCode, SafetyGateAction
    result = NavigationExecutionResult(
        session_id=session_id,
        execution_status=ExecutionStatus.EXECUTED_CLEAR,
        gate_decision=GateDecision.PERMITTED_CLEAR,
        gate_reason_code=GateReasonCode.NONE,
        action=SafetyGateAction.TOOL_NAVIGATION,
        sequence_number=1,
        instrument_id="test_instrument",
        target_trajectory_id="traj_1",
        executed_at_utc="2026-01-01T00:00:00Z",
    )
    execution_service._latest_results[session_id] = result
    execution_service._persisted_states[session_id] = (
        ExecutionStatus.EXECUTED_CLEAR,
        GateReasonCode.NONE,
    )


class TestExecutionLifecycleTeardown:
    def test_stopped_clears_transient_state(self, execution_service, dispatcher):
        _seed_execution_session(execution_service, "sess_A")
        assert "sess_A" in execution_service._latest_results
        _emit_stopped(dispatcher, "sess_A")
        assert "sess_A" not in execution_service._latest_results
        assert "sess_A" not in execution_service._persisted_states

    def test_evicted_clears_transient_state(self, execution_service, dispatcher):
        _seed_execution_session(execution_service, "sess_A")
        _emit_evicted(dispatcher, "sess_A")
        assert "sess_A" not in execution_service._latest_results

    def test_teardown_preserves_other_session(self, execution_service, dispatcher):
        _seed_execution_session(execution_service, "sess_A")
        _seed_execution_session(execution_service, "sess_B")
        _emit_stopped(dispatcher, "sess_A")
        assert "sess_A" not in execution_service._latest_results
        assert "sess_B" in execution_service._latest_results


# ===========================================================================
# Navigation Service
# ===========================================================================

@pytest.fixture
def navigation_service(test_runtime_context, dispatcher):
    from holomed.navigation.service import NavigationService
    from holomed.planning.service import PlanningService
    planning = PlanningService(dispatcher=dispatcher)
    planning.initialize(test_runtime_context)
    svc = NavigationService(dispatcher=dispatcher, planning_service=planning)
    svc.initialize(test_runtime_context)
    dispatcher.start()
    planning.start()
    svc.start()
    yield svc
    svc.stop()
    planning.stop()


def _seed_navigation_session(navigation_service, session_id: str) -> None:
    from holomed.navigation.models import NavigationState
    navigation_service._session_states[session_id] = NavigationState.IDLE


class TestNavigationLifecycleTeardown:
    def test_stopped_clears_transient_state(self, navigation_service, dispatcher):
        _seed_navigation_session(navigation_service, "sess_A")
        assert "sess_A" in navigation_service._session_states
        _emit_stopped(dispatcher, "sess_A")
        assert "sess_A" not in navigation_service._session_states

    def test_evicted_clears_transient_state(self, navigation_service, dispatcher):
        _seed_navigation_session(navigation_service, "sess_A")
        _emit_evicted(dispatcher, "sess_A")
        assert "sess_A" not in navigation_service._session_states

    def test_teardown_preserves_other_session(self, navigation_service, dispatcher):
        _seed_navigation_session(navigation_service, "sess_A")
        _seed_navigation_session(navigation_service, "sess_B")
        _emit_stopped(dispatcher, "sess_A")
        assert "sess_A" not in navigation_service._session_states
        assert "sess_B" in navigation_service._session_states


# ===========================================================================
# Planning Service
# ===========================================================================

@pytest.fixture
def planning_service(test_runtime_context, dispatcher):
    from holomed.planning.service import PlanningService
    svc = PlanningService(dispatcher=dispatcher)
    svc.initialize(test_runtime_context)
    dispatcher.start()
    svc.start()
    yield svc
    svc.stop()


def _seed_planning_session(planning_service, session_id: str) -> None:
    from unittest.mock import MagicMock
    planning_service._session_plan_bindings[session_id] = f"plan_{session_id}"
    planning_service._plans[f"plan_{session_id}"] = MagicMock()


class TestPlanningLifecycleTeardown:
    def test_stopped_clears_session_bindings(self, planning_service, dispatcher):
        _seed_planning_session(planning_service, "sess_A")
        assert "sess_A" in planning_service._session_plan_bindings
        assert "plan_sess_A" in planning_service._plans
        _emit_stopped(dispatcher, "sess_A")
        assert "sess_A" not in planning_service._session_plan_bindings
        assert "plan_sess_A" not in planning_service._plans

    def test_evicted_clears_session_bindings(self, planning_service, dispatcher):
        _seed_planning_session(planning_service, "sess_A")
        _emit_evicted(dispatcher, "sess_A")
        assert "sess_A" not in planning_service._session_plan_bindings

    def test_teardown_preserves_other_session(self, planning_service, dispatcher):
        _seed_planning_session(planning_service, "sess_A")
        _seed_planning_session(planning_service, "sess_B")
        _emit_stopped(dispatcher, "sess_A")
        assert "sess_A" not in planning_service._session_plan_bindings
        assert "sess_B" in planning_service._session_plan_bindings
        assert "plan_sess_B" in planning_service._plans


# ===========================================================================
# Proximity Service
# ===========================================================================

@pytest.fixture
def proximity_service(test_runtime_context, dispatcher):
    from holomed.proximity.service import ProximityService
    from holomed.registration.service import RegistrationService
    from holomed.planning.service import PlanningService
    planning = PlanningService(dispatcher=dispatcher)
    planning.initialize(test_runtime_context)
    reg = RegistrationService(dispatcher=dispatcher, planning_service=planning)
    reg.initialize(test_runtime_context)
    svc = ProximityService(dispatcher=dispatcher, registration_service=reg)
    svc.initialize(test_runtime_context)
    dispatcher.start()
    planning.start()
    reg.start()
    svc.start()
    yield svc
    svc.stop()
    reg.stop()
    planning.stop()


def _seed_proximity_session(proximity_service, session_id: str) -> None:
    from holomed.proximity.models import ProximityState
    proximity_service._session_states[session_id] = ProximityState.CLEAR


class TestProximityLifecycleTeardown:
    def test_stopped_clears_transient_state(self, proximity_service, dispatcher):
        _seed_proximity_session(proximity_service, "sess_A")
        assert "sess_A" in proximity_service._session_states
        _emit_stopped(dispatcher, "sess_A")
        assert "sess_A" not in proximity_service._session_states

    def test_evicted_clears_transient_state(self, proximity_service, dispatcher):
        _seed_proximity_session(proximity_service, "sess_A")
        _emit_evicted(dispatcher, "sess_A")
        assert "sess_A" not in proximity_service._session_states

    def test_teardown_preserves_other_session(self, proximity_service, dispatcher):
        _seed_proximity_session(proximity_service, "sess_A")
        _seed_proximity_session(proximity_service, "sess_B")
        _emit_stopped(dispatcher, "sess_A")
        assert "sess_A" not in proximity_service._session_states
        assert "sess_B" in proximity_service._session_states


# ===========================================================================
# Recovery Service
# ===========================================================================

@pytest.fixture
def recovery_service(test_runtime_context, dispatcher):
    from holomed.recovery.service import RecoveryService
    from holomed.planning.service import PlanningService
    from holomed.registration.service import RegistrationService
    from holomed.navigation.service import NavigationService
    planning = PlanningService(dispatcher=dispatcher)
    planning.initialize(test_runtime_context)
    reg = RegistrationService(dispatcher=dispatcher, planning_service=planning)
    reg.initialize(test_runtime_context)
    nav = NavigationService(dispatcher=dispatcher, planning_service=planning)
    nav.initialize(test_runtime_context)
    svc = RecoveryService(dispatcher=dispatcher, planning_service=planning,
                          registration_service=reg, navigation_service=nav)
    svc.initialize(test_runtime_context)
    dispatcher.start()
    planning.start()
    reg.start()
    nav.start()
    svc.start()
    yield svc
    svc.stop()
    nav.stop()
    reg.stop()
    planning.stop()


def _seed_recovery_session(recovery_service, session_id: str) -> None:
    from holomed.recovery.models import RecoveryState
    recovery_service._session_states[session_id] = RecoveryState.IDLE
    recovery_service._revisions[session_id] = 0


class TestRecoveryLifecycleTeardown:
    def test_stopped_clears_transient_state(self, recovery_service, dispatcher):
        _seed_recovery_session(recovery_service, "sess_A")
        assert "sess_A" in recovery_service._session_states
        _emit_stopped(dispatcher, "sess_A")
        assert "sess_A" not in recovery_service._session_states

    def test_evicted_clears_transient_state(self, recovery_service, dispatcher):
        _seed_recovery_session(recovery_service, "sess_A")
        _emit_evicted(dispatcher, "sess_A")
        assert "sess_A" not in recovery_service._session_states

    def test_teardown_preserves_other_session(self, recovery_service, dispatcher):
        _seed_recovery_session(recovery_service, "sess_A")
        _seed_recovery_session(recovery_service, "sess_B")
        _emit_stopped(dispatcher, "sess_A")
        assert "sess_A" not in recovery_service._session_states
        assert "sess_B" in recovery_service._session_states


# ===========================================================================
# Registration Service
# ===========================================================================

@pytest.fixture
def registration_service(test_runtime_context, dispatcher):
    from holomed.registration.service import RegistrationService
    from holomed.planning.service import PlanningService
    planning = PlanningService(dispatcher=dispatcher)
    planning.initialize(test_runtime_context)
    svc = RegistrationService(dispatcher=dispatcher, planning_service=planning)
    svc.initialize(test_runtime_context)
    dispatcher.start()
    planning.start()
    svc.start()
    yield svc
    svc.stop()
    planning.stop()


def _seed_registration_session(registration_service, session_id: str) -> None:
    from unittest.mock import MagicMock
    registration_service._registrations[session_id] = MagicMock()


class TestRegistrationLifecycleTeardown:
    def test_stopped_clears_transient_state(self, registration_service, dispatcher):
        _seed_registration_session(registration_service, "sess_A")
        assert "sess_A" in registration_service._registrations
        _emit_stopped(dispatcher, "sess_A")
        assert "sess_A" not in registration_service._registrations

    def test_evicted_clears_transient_state(self, registration_service, dispatcher):
        _seed_registration_session(registration_service, "sess_A")
        _emit_evicted(dispatcher, "sess_A")
        assert "sess_A" not in registration_service._registrations

    def test_teardown_preserves_other_session(self, registration_service, dispatcher):
        _seed_registration_session(registration_service, "sess_A")
        _seed_registration_session(registration_service, "sess_B")
        _emit_stopped(dispatcher, "sess_A")
        assert "sess_A" not in registration_service._registrations
        assert "sess_B" in registration_service._registrations


# ===========================================================================
# Safety Gate Service
# ===========================================================================

@pytest.fixture
def safety_gate_service(test_runtime_context, dispatcher):
    from holomed.safety_gate.service import SafetyGateService
    svc = SafetyGateService(dispatcher=dispatcher)
    svc.initialize(test_runtime_context)
    dispatcher.start()
    svc.start()
    yield svc
    svc.stop()


def _seed_safety_gate_session(safety_gate_service, session_id: str) -> None:
    from unittest.mock import MagicMock
    safety_gate_service._latest_decisions[session_id] = MagicMock()


class TestSafetyGateLifecycleTeardown:
    def test_stopped_clears_transient_state(self, safety_gate_service, dispatcher):
        _seed_safety_gate_session(safety_gate_service, "sess_A")
        assert "sess_A" in safety_gate_service._latest_decisions
        _emit_stopped(dispatcher, "sess_A")
        assert "sess_A" not in safety_gate_service._latest_decisions

    def test_evicted_clears_transient_state(self, safety_gate_service, dispatcher):
        _seed_safety_gate_session(safety_gate_service, "sess_A")
        _emit_evicted(dispatcher, "sess_A")
        assert "sess_A" not in safety_gate_service._latest_decisions

    def test_teardown_preserves_other_session(self, safety_gate_service, dispatcher):
        _seed_safety_gate_session(safety_gate_service, "sess_A")
        _seed_safety_gate_session(safety_gate_service, "sess_B")
        _emit_stopped(dispatcher, "sess_A")
        assert "sess_A" not in safety_gate_service._latest_decisions
        assert "sess_B" in safety_gate_service._latest_decisions


# ===========================================================================
# Ultron Service
# ===========================================================================

@pytest.fixture
def ultron_service(test_runtime_context, dispatcher):
    from holomed.ultron.service import UltronService
    svc = UltronService(dispatcher=dispatcher)
    svc.initialize(test_runtime_context)
    dispatcher.start()
    svc.start()
    yield svc
    svc.stop()


def _seed_ultron_session(ultron_service, session_id: str) -> None:
    from unittest.mock import MagicMock
    ultron_service._session_stores[session_id] = MagicMock()
    ultron_service._session_fusion_engines[session_id] = MagicMock()
    ultron_service._session_rule_engines[session_id] = MagicMock()
    ultron_service._session_sequence_trackers[session_id] = MagicMock()


class TestUltronLifecycleTeardown:
    def test_stopped_clears_transient_state(self, ultron_service, dispatcher):
        _seed_ultron_session(ultron_service, "sess_A")
        assert "sess_A" in ultron_service._session_stores
        _emit_stopped(dispatcher, "sess_A")
        assert "sess_A" not in ultron_service._session_stores
        assert "sess_A" not in ultron_service._session_fusion_engines

    def test_evicted_clears_transient_state(self, ultron_service, dispatcher):
        _seed_ultron_session(ultron_service, "sess_A")
        _emit_evicted(dispatcher, "sess_A")
        assert "sess_A" not in ultron_service._session_stores

    def test_teardown_preserves_other_session(self, ultron_service, dispatcher):
        _seed_ultron_session(ultron_service, "sess_A")
        _seed_ultron_session(ultron_service, "sess_B")
        _emit_stopped(dispatcher, "sess_A")
        assert "sess_A" not in ultron_service._session_stores
        assert "sess_B" in ultron_service._session_stores


# ===========================================================================
# Workflow Service
# ===========================================================================

@pytest.fixture
def workflow_service(test_runtime_context, dispatcher):
    from holomed.workflow.service import WorkflowService
    svc = WorkflowService(dispatcher=dispatcher)
    svc.initialize(test_runtime_context)
    dispatcher.start()
    svc.start()
    yield svc
    svc.stop()


def _seed_workflow_session(workflow_service, session_id: str) -> None:
    from holomed.workflow.models import WorkflowPhase
    from holomed.workflow.state_machine import WorkflowStateMachine
    from unittest.mock import MagicMock
    
    workflow_service._workflows[session_id] = WorkflowStateMachine(
        workflow_id=f"wf_{session_id}",
        session_id=session_id,
        epoch_id=1,
        procedure=MagicMock(),
        initial_phase=WorkflowPhase.PATIENT_CONTEXT
    )


class TestWorkflowLifecycleTeardown:
    def test_stopped_clears_transient_state(self, workflow_service, dispatcher):
        _seed_workflow_session(workflow_service, "sess_A")
        assert "sess_A" in workflow_service._workflows
        _emit_stopped(dispatcher, "sess_A")
        assert "sess_A" not in workflow_service._workflows

    def test_evicted_clears_transient_state(self, workflow_service, dispatcher):
        _seed_workflow_session(workflow_service, "sess_A")
        _emit_evicted(dispatcher, "sess_A")
        assert "sess_A" not in workflow_service._workflows

    def test_teardown_preserves_other_session(self, workflow_service, dispatcher):
        _seed_workflow_session(workflow_service, "sess_A")
        _seed_workflow_session(workflow_service, "sess_B")
        _emit_stopped(dispatcher, "sess_A")
        assert "sess_A" not in workflow_service._workflows
        assert "sess_B" in workflow_service._workflows


# ===========================================================================
# Universal Lifecycle Propagation
# ===========================================================================

class TestUniversalLifecyclePropagation:
    """Verify that a single platform.session.stopped event propagates teardown
    across ALL stateful services simultaneously."""

    def test_stopped_propagates_to_all_services(
        self, test_runtime_context, dispatcher
    ):
        from holomed.drift.service import DriftService
        from holomed.execution.service import ClinicalExecutionGatewayService
        from holomed.navigation.service import NavigationService
        from holomed.planning.service import PlanningService
        from holomed.proximity.service import ProximityService
        from holomed.recovery.service import RecoveryService
        from holomed.registration.service import RegistrationService
        from holomed.safety_gate.service import SafetyGateService
        from holomed.ultron.service import UltronService
        from holomed.workflow.service import WorkflowService

        planning = PlanningService(dispatcher=dispatcher)
        planning.initialize(test_runtime_context)

        reg = RegistrationService(dispatcher=dispatcher, planning_service=planning)
        reg.initialize(test_runtime_context)

        nav = NavigationService(dispatcher=dispatcher, planning_service=planning)
        nav.initialize(test_runtime_context)

        drift = DriftService(dispatcher=dispatcher, registration_service=reg)
        drift.initialize(test_runtime_context)

        execution = ClinicalExecutionGatewayService(dispatcher=dispatcher)
        execution.initialize(test_runtime_context)

        proximity = ProximityService(dispatcher=dispatcher, registration_service=reg)
        proximity.initialize(test_runtime_context)

        recovery = RecoveryService(
            dispatcher=dispatcher, planning_service=planning,
            registration_service=reg, navigation_service=nav,
        )
        recovery.initialize(test_runtime_context)

        safety_gate = SafetyGateService(dispatcher=dispatcher)
        safety_gate.initialize(test_runtime_context)

        ultron = UltronService(dispatcher=dispatcher)
        ultron.initialize(test_runtime_context)

        workflow = WorkflowService(dispatcher=dispatcher)
        workflow.initialize(test_runtime_context)
        
        # Now start dispatcher!
        dispatcher.start()

        planning.start()
        reg.start()
        nav.start()
        drift.start()
        execution.start()
        proximity.start()
        recovery.start()
        safety_gate.start()
        ultron.start()
        workflow.start()

        # Seed session-transient state in each service
        session_id = "universal_sess"
        _seed_drift_session(drift, session_id)
        _seed_execution_session(execution, session_id)
        _seed_navigation_session(nav, session_id)
        _seed_planning_session(planning, session_id)
        _seed_proximity_session(proximity, session_id)
        _seed_recovery_session(recovery, session_id)
        _seed_registration_session(reg, session_id)
        _seed_safety_gate_session(safety_gate, session_id)
        _seed_ultron_session(ultron, session_id)
        _seed_workflow_session(workflow, session_id)

        # Also seed session B to verify isolation
        _seed_drift_session(drift, "sess_B")
        _seed_execution_session(execution, "sess_B")

        # Verify state exists
        assert session_id in drift._session_states
        assert session_id in execution._latest_results
        assert session_id in nav._session_states
        assert session_id in planning._session_plan_bindings
        assert session_id in proximity._session_states
        assert session_id in recovery._session_states
        assert session_id in reg._registrations
        assert session_id in safety_gate._latest_decisions
        assert session_id in ultron._session_stores
        assert session_id in workflow._workflows

        # Emit a single stopped event
        _emit_stopped(dispatcher, session_id)

        # Verify session-transient state is gone
        assert session_id not in drift._session_states
        assert session_id not in execution._latest_results
        assert session_id not in nav._session_states
        assert session_id not in planning._session_plan_bindings
        assert session_id not in proximity._session_states
        assert session_id not in recovery._session_states
        assert session_id not in reg._registrations
        assert session_id not in safety_gate._latest_decisions
        assert session_id not in ultron._session_stores
        assert session_id not in workflow._workflows

        # Verify session B survives
        assert "sess_B" in drift._session_states
        assert "sess_B" in execution._latest_results

        # Cleanup
        for svc in [workflow, ultron, safety_gate, recovery, proximity,
                     execution, drift, nav, reg, planning]:
            svc.stop()

    def test_new_activation_produces_fresh_state(
        self, test_runtime_context, dispatcher
    ):
        """After teardown, re-seeding state produces fresh state with no
        stale remnants."""
        from holomed.drift.service import DriftService
        from holomed.drift.models import DriftState
        from holomed.registration.service import RegistrationService
        from holomed.planning.service import PlanningService

        planning = PlanningService(dispatcher=dispatcher)
        planning.initialize(test_runtime_context)

        reg = RegistrationService(dispatcher=dispatcher, planning_service=planning)
        reg.initialize(test_runtime_context)

        drift = DriftService(dispatcher=dispatcher, registration_service=reg)
        drift.initialize(test_runtime_context)

        dispatcher.start()
        
        planning.start()
        reg.start()
        drift.start()

        # Seed → teardown → re-seed
        _seed_drift_session(drift, "sess_A")
        drift._latest_sequences["sess_A"] = 99
        _emit_stopped(dispatcher, "sess_A")
        assert "sess_A" not in drift._latest_sequences

        # Re-seed: should get clean state
        _seed_drift_session(drift, "sess_A")
        assert drift._session_states["sess_A"] == DriftState.READY
        assert drift._latest_sequences.get("sess_A", 0) == 0

        drift.stop()
        reg.stop()
        planning.stop()
