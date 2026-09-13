import pytest
from datetime import datetime, timezone
from holomed.configuration.models import AppConfig, EnvironmentProfile, LogLevel
from holomed.core.dispatcher import MessageDispatcher
from holomed.platform.service import PlatformService
from holomed.safety_gate.service import SafetyGateService
from holomed.workflow.service import WorkflowService
from holomed.runtime.context import RuntimeContext
from holomed.safety_gate.models import GateRequest, SafetyGateAction
from holomed.safety_gate.exceptions import SafetyGateLifecycleError
from holomed.workflow.exceptions import WorkflowLifecycleError

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
def integrated_stack(test_runtime_context):
    dispatcher = MessageDispatcher()
    dispatcher.initialize(test_runtime_context)
    
    platform = PlatformService(dispatcher=dispatcher)
    platform.initialize(test_runtime_context)
    
    safety_gate = SafetyGateService(dispatcher=dispatcher)
    safety_gate.initialize(test_runtime_context)
    
    workflow = WorkflowService(dispatcher=dispatcher)
    workflow.initialize(test_runtime_context)
    
    dispatcher.start()
    platform.start()
    safety_gate.start()
    workflow.start()
    
    yield platform, safety_gate, workflow
    
    workflow.stop()
    safety_gate.stop()
    platform.stop()
    dispatcher.stop()

class TestM42SafetyGateAdversarial:
    def test_safety_gate_active_session(self, integrated_stack):
        platform, safety, _ = integrated_stack
        session_id = "sess_active_1"
        platform._session_manager.start_session(session_id, 1)
        
        req = GateRequest(
            session_id=session_id,
            action=SafetyGateAction.TOOL_NAVIGATION,
            sequence_number=1,
            now_utc=datetime.now(timezone.utc).isoformat(),
            instrument_id="inst_01",
            target_trajectory_id="traj_01"
        )
        res = safety.evaluate(req)
        assert session_id in safety._latest_decisions
        
    def test_safety_gate_unknown_session(self, integrated_stack):
        _, safety, _ = integrated_stack
        req = GateRequest(
            session_id="sess_unknown",
            action=SafetyGateAction.TOOL_NAVIGATION,
            sequence_number=1,
            now_utc=datetime.now(timezone.utc).isoformat(),
            instrument_id="inst_01",
            target_trajectory_id="traj_01"
        )
        with pytest.raises(SafetyGateLifecycleError, match="is not ACTIVE"):
            safety.evaluate(req)
        assert "sess_unknown" not in safety._latest_decisions
        
    def test_safety_gate_stopped_session(self, integrated_stack):
        platform, safety, _ = integrated_stack
        session_id = "sess_stopped"
        platform._session_manager.start_session(session_id, 1)
        platform._session_manager.stop_session(session_id)
        
        req = GateRequest(
            session_id=session_id,
            action=SafetyGateAction.TOOL_NAVIGATION,
            sequence_number=1,
            now_utc=datetime.now(timezone.utc).isoformat(),
            instrument_id="inst_01",
            target_trajectory_id="traj_01"
        )
        with pytest.raises(SafetyGateLifecycleError, match="is not ACTIVE"):
            safety.evaluate(req)
        assert session_id not in safety._latest_decisions
        
    def test_safety_gate_evicted_session(self, integrated_stack):
        platform, safety, _ = integrated_stack
        session_id = "sess_evicted"
        platform._session_manager.start_session(session_id, 1)
        platform._session_manager.evict_session(session_id)
        
        req = GateRequest(
            session_id=session_id,
            action=SafetyGateAction.TOOL_NAVIGATION,
            sequence_number=1,
            now_utc=datetime.now(timezone.utc).isoformat(),
            instrument_id="inst_01",
            target_trajectory_id="traj_01"
        )
        with pytest.raises(SafetyGateLifecycleError, match="is not ACTIVE"):
            safety.evaluate(req)
        assert session_id not in safety._latest_decisions

    def test_safety_gate_capacity_attack(self, integrated_stack):
        platform, safety, _ = integrated_stack
        session_id = "sess_attack"
        platform._session_manager.start_session(session_id, 1)
        platform._session_manager.evict_session(session_id)
        
        for i in range(100):
            req = GateRequest(
                session_id=session_id,
                action=SafetyGateAction.TOOL_NAVIGATION,
                sequence_number=i + 1,
                now_utc=datetime.now(timezone.utc).isoformat(),
                instrument_id="inst_01",
                target_trajectory_id="traj_01"
            )
            with pytest.raises(SafetyGateLifecycleError, match="is not ACTIVE"):
                safety.evaluate(req)
        
        assert session_id not in safety._latest_decisions
        assert len(safety._latest_decisions) == 0
        
        # Verify legitimate session works
        legit = "sess_legit"
        platform._session_manager.start_session(legit, 1)
        req = GateRequest(
            session_id=legit,
            action=SafetyGateAction.TOOL_NAVIGATION,
            sequence_number=1,
            now_utc=datetime.now(timezone.utc).isoformat(),
            instrument_id="inst_01",
            target_trajectory_id="traj_01"
        )
        safety.evaluate(req)
        assert legit in safety._latest_decisions
        assert len(safety._latest_decisions) == 1
        
    def test_safety_gate_reactivation(self, integrated_stack):
        platform, safety, _ = integrated_stack
        session_id = "sess_reactivate"
        platform._session_manager.start_session(session_id, 1)
        platform._session_manager.stop_session(session_id)
        
        req = GateRequest(
            session_id=session_id,
            action=SafetyGateAction.TOOL_NAVIGATION,
            sequence_number=1,
            now_utc=datetime.now(timezone.utc).isoformat(),
            instrument_id="inst_01",
            target_trajectory_id="traj_01"
        )
        with pytest.raises(SafetyGateLifecycleError):
            safety.evaluate(req)
            
        platform._session_manager.start_session(session_id, 1)
        safety.evaluate(req)
        assert session_id in safety._latest_decisions
        
    def test_safety_gate_cross_session_isolation(self, integrated_stack):
        platform, safety, _ = integrated_stack
        a = "sess_A"
        b = "sess_B"
        platform._session_manager.start_session(a, 1)
        platform._session_manager.start_session(b, 1)
        
        platform._session_manager.stop_session(a)
        
        req_a = GateRequest(
            session_id=a,
            action=SafetyGateAction.TOOL_NAVIGATION,
            sequence_number=1,
            now_utc=datetime.now(timezone.utc).isoformat(),
            instrument_id="inst_01",
            target_trajectory_id="traj_01"
        )
        with pytest.raises(SafetyGateLifecycleError):
            safety.evaluate(req_a)
            
        req_b = GateRequest(
            session_id=b,
            action=SafetyGateAction.TOOL_NAVIGATION,
            sequence_number=1,
            now_utc=datetime.now(timezone.utc).isoformat(),
            instrument_id="inst_01",
            target_trajectory_id="traj_01"
        )
        safety.evaluate(req_b)
        assert a not in safety._latest_decisions
        assert b in safety._latest_decisions


class TestM42WorkflowAdversarial:
    def test_workflow_active_session(self, integrated_stack):
        platform, _, workflow = integrated_stack
        session_id = "PROC-1234-A"
        platform._session_manager.start_session(session_id, 1)
        
        workflow.start_workflow(session_id)
        assert session_id in workflow._workflows
        
    def test_workflow_unknown_session(self, integrated_stack):
        _, _, workflow = integrated_stack
        session_id = "PROC-UNKNOWN"
        with pytest.raises(WorkflowLifecycleError, match="is not ACTIVE"):
            workflow.start_workflow(session_id)
        assert session_id not in workflow._workflows
        
    def test_workflow_stopped_session(self, integrated_stack):
        platform, _, workflow = integrated_stack
        session_id = "PROC-STOPPED"
        platform._session_manager.start_session(session_id, 1)
        platform._session_manager.stop_session(session_id)
        
        with pytest.raises(WorkflowLifecycleError, match="is not ACTIVE"):
            workflow.start_workflow(session_id)
        assert session_id not in workflow._workflows
        
    def test_workflow_evicted_session(self, integrated_stack):
        platform, _, workflow = integrated_stack
        session_id = "PROC-EVICTED"
        platform._session_manager.start_session(session_id, 1)
        platform._session_manager.evict_session(session_id)
        
        with pytest.raises(WorkflowLifecycleError, match="is not ACTIVE"):
            workflow.start_workflow(session_id)
        assert session_id not in workflow._workflows

    def test_workflow_capacity_attack(self, integrated_stack):
        platform, _, workflow = integrated_stack
        session_id = "PROC-ATTACK"
        platform._session_manager.start_session(session_id, 1)
        platform._session_manager.evict_session(session_id)
        
        for i in range(100):
            with pytest.raises(WorkflowLifecycleError, match="is not ACTIVE"):
                workflow.start_workflow(session_id)
        
        assert session_id not in workflow._workflows
        assert len(workflow._workflows) == 0
        
        # Verify legitimate session works
        legit = "PROC-LEGIT"
        platform._session_manager.start_session(legit, 1)
        workflow.start_workflow(legit)
        assert legit in workflow._workflows
        assert len(workflow._workflows) == 1
        
    def test_workflow_reactivation(self, integrated_stack):
        platform, _, workflow = integrated_stack
        session_id = "PROC-REACT"
        platform._session_manager.start_session(session_id, 1)
        platform._session_manager.stop_session(session_id)
        
        with pytest.raises(WorkflowLifecycleError):
            workflow.start_workflow(session_id)
            
        platform._session_manager.start_session(session_id, 1)
        workflow.start_workflow(session_id)
        assert session_id in workflow._workflows
        
    def test_workflow_cross_session_isolation(self, integrated_stack):
        platform, _, workflow = integrated_stack
        a = "PROC-AAA"
        b = "PROC-BBB"
        platform._session_manager.start_session(a, 1)
        platform._session_manager.start_session(b, 1)
        
        platform._session_manager.stop_session(a)
        
        with pytest.raises(WorkflowLifecycleError):
            workflow.start_workflow(a)
            
        workflow.start_workflow(b)
        assert a not in workflow._workflows
        assert b in workflow._workflows
