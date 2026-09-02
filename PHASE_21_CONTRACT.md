# PHASE 21 CONTRACT
# UNIVERSAL CLINICAL EXECUTION GATEWAY & DISPATCHER ACTUATION HARDENING

**Baseline Milestones**:
- M19 Authoritative Commit: `e93f88a02e13cf170fc55cb12f5339a3638996ad`
- M20 Authoritative HEAD: `a7f1ee77dfa5040f8aa61e3f74159e56967650d5`

**Status**: `M21 CONTRACT LOCKED`  
**Lock Timestamp**: 2026-09-02T18:19:00Z  

---

## 1. OBJECTIVE

Expand M19 `holomed.execution` into the single authoritative clinical execution gateway.

M21 must:
1. Eliminate raw clinical execution dispatcher routes from M07 and M14.
2. Require an internal `_ExecutionCapability` for direct M07/M14 execution primitives.
3. Add M18 `SafetyGateAction.TOOL_INVOCATION` with selective precedence gating.
4. Expose M20 recovery resumption through dispatcher command `execution.workflow.resume`.
5. Preserve M20 workflow authority without duplicating recovery or transition logic.
6. Preserve M11 Gateway completely unchanged (frozen).
7. Maintain exactly one execution coordinator across the system.

---

## 2. REOPENED AND FROZEN MILESTONES

### Exact Reopened Milestones (4 Only):
- **M07**: `python/holomed/tools/`
- **M14**: `python/holomed/navigation/`
- **M18**: `python/holomed/safety_gate/`
- **M19**: `python/holomed/execution/`

### Strictly Frozen Milestones:
- **M00–M06**: Device plane, core dispatcher primitives, vision, audio, gestures, ultron, anatomy, XR
- **M08–M09**: Platform supervisor, session persistence & journaling
- **M10/M20**: Clinical workflow state machine & recovery resumption
- **M11**: External client gateway & transport (specifically remains frozen)
- **M12–M13**: Preoperative planning, patient-to-plan registration
- **M15–M17**: Proximity monitoring, landmark drift, spatial recovery

*No additional milestone may be modified without a formal contract revision.*

---

## 3. EXECUTION AUTHORITY HIERARCHY

### Sole Execution Coordinator:
`ClinicalExecutionGatewayService`  
Package: `holomed.execution`

There is **no peer gateway** and **no second execution coordinator**.

### Authority Breakdown:
- **COORDINATOR**: `ClinicalExecutionGatewayService` (`holomed.execution`)  
  Receives all execution commands, enforces sequence numbers, orchestrates safety gates and workflow authorization, issues internal execution capabilities, and emits lifecycle events.
- **AUTHORIZER**: `WorkflowService` (`holomed.workflow`)  
  Authoritative evaluator of clinical workflow phase eligibility, tool classification authorization, and recovery resumption transactions.
- **SAFETY EVALUATOR**: `SafetyGateService` (`holomed.safety_gate`)  
  Authoritative evaluator of multi-subsystem safety state (proximity breaches, landmark drift, sensor integrity, registration status).
- **EXECUTORS**:
  - `NavigationService` (`holomed.navigation`): Computes trajectory deviations and processes tracked instrument poses.
  - `ToolService` (`holomed.tools`): Deterministically executes clinical tool logic and captures tool execution metrics.
- **PERSISTER**: `PersistenceService` (`holomed.persistence`)  
  Authoritative journal recorder for all execution decisions, blocked operations, and audit records.

---

## 4. EXACT DISPATCHER ROUTES AFTER M21

### COMMAND Routes:
1. `execution.navigation.execute` (Inherited from M19)
2. `execution.recovery.execute` (Promoted M19 Python API to Dispatcher Bus)
3. `execution.trajectory.bind` (Promoted M19 Python API to Dispatcher Bus)
4. `execution.tool.invoke` (New M21 Unified Tool Execution Command)
5. `execution.workflow.resume` (New M21 Protocol Route for M20 Recovery Resumption)

### QUERY Routes:
1. `execution.status.get` (Inherited from M19)

*No other clinical execution command routes may be registered on `MessageDispatcher`.*

---

## 5. EXACT ROUTES TO REMOVE FROM SUBSYSTEM REGISTRIES

### M07 (`ToolService`):
- **REMOVE**: `tools.invoke` from `ToolService.initialize()`

### M14 (`NavigationService`):
- **REMOVE**: `navigation.pose.submit` from `NavigationService.initialize()`
- **REMOVE**: `navigation.evaluate` from `NavigationService.initialize()`

### Retained Non-Execution Subsystem Routes:
- **M07**: `tools.registry` (QUERY), `tools.result` (QUERY), `tools.reset` (CMD), `tools.status` (QUERY)
- **M14**: `navigation.status.get` (QUERY)

*Do not remove or redesign non-execution query/management routes.*

---

## 6. M07 TOOL INVOCATION PIPELINE

All clinical M07 tool execution must strictly execute through:
```
execution.tool.invoke (MessageEnvelope)
  │
  ├── 1. Validate envelope identity & request format
  ├── 2. Fresh synchronous M18 evaluation:
  │      SafetyGateService.evaluate(action=TOOL_INVOCATION)
  ├── 3. Synchronous M10 authorization check:
  │      WorkflowService.authorize_tool(session_id, tool_id, safety_classification)
  ├── 4. Create internal single-use _ExecutionCapability
  ├── 5. Execute: ToolService.invoke_tool(context, capability)
  ├── 6. Journal audit record: PersistenceService.record_audit(...)
  ├── 7. Emit execution event: execution.tool.completed / execution.tool.blocked
  └── 8. finally: invalidate _ExecutionCapability
```

### Unchanged M10 Signature:
```python
WorkflowService.authorize_tool(
    session_id: str,
    tool_id: str,
    safety_classification: ToolSafetyClassification,
    is_surgical_actuation: bool = False,
) -> WorkflowToolAuthorizationDecision
```

---

## 7. EXACT M07 TOOL SEMANTICS & RECOVERY RULES

1. `anatomy.query_organ` (`QUERY_ANATOMY`)
   - Classification: `READ_ONLY_INFORMATIVE`
   - Nominal Behavior: Permitted during nominal phases; blocked during `RECOVERY_REQUIRED`.
2. `navigation.measure_distance` (`MEASURE_DISTANCE`)
   - Classification: `READ_ONLY_INFORMATIVE`
   - Nominal Behavior: Permitted during nominal phases; blocked during `RECOVERY_REQUIRED` and when landmark drift is exceeded.
3. `xr.highlight_structure` (`HIGHLIGHT_STRUCTURE`)
   - Classification: `VISUALIZATION_ADJUSTMENT`
   - Nominal Behavior: Permitted in planning, navigation, and intervention; blocked during `RECOVERY_REQUIRED`.
4. `xr.adjust_viewport` (`ADJUST_VIEWPORT`)
   - Classification: `VISUALIZATION_ADJUSTMENT`
   - Nominal Behavior: Permitted in planning, navigation, and intervention; blocked during `RECOVERY_REQUIRED`.
5. `system.capture_telemetry` (`CAPTURE_TELEMETRY`)
   - Classification: `TELEMETRY_RECORDING`
   - Nominal Behavior: Permitted in all phases, including `RECOVERY_REQUIRED` and `ABORTED`.

### Safety Rules:
- **Recovery Rule**: In `RECOVERY_REQUIRED`, M10 allows **only** `TELEMETRY_RECORDING`.
- **Landmark Drift Rule**: M18 `DRIFT_EXCEEDED` denies all operations except `RECOVERY_REORIENTATION`.

---

## 8. M18 TOOL_INVOCATION ACTION SPECIFICATION

Add `SafetyGateAction.TOOL_INVOCATION` to [SafetyGateAction](file:///c:/Users/mohit/OneDrive/Desktop/HOLOMEDAI-FIRE/python/holomed/safety_gate/models.py).

### GateRequest Fields:
- `session_id: str`
- `action: SafetyGateAction.TOOL_INVOCATION`
- `sequence_number: int`
- `now_utc: str`
- `tool_id: Optional[str] = None`
- `safety_classification: Optional[ToolSafetyClassification] = None`
- `target_trajectory_id: Optional[str] = None`
- `instrument_id: Optional[str] = None`
- `recovery_revision: Optional[int] = None`

### Action Isolation:
Granting `TOOL_INVOCATION` permission **never implies** permission for:
- `TOOL_NAVIGATION`
- `TRAJECTORY_ALIGNMENT`
- `RECOVERY_REORIENTATION`
- `WORKFLOW_RESUMPTION`

The gate fails closed on critical exclusion zone breaches (`DENIED_CRITICAL`) and active interlocks (`DENIED_INTERLOCKED`).

---

## 9. INTERNAL EXECUTION CAPABILITY ARCHITECTURE

Module: `python/holomed/execution/_capability.py` (Unexported; omitted from `__all__`).

### Invariants:
1. **Unexported**: Internal to `holomed.execution`.
2. **Sentinel Key**: Constructor enforces `internal_key is _INTERNAL_EXECUTION_KEY`.
3. **Non-Serializable**: `__getstate__` and `__setstate__` raise `TypeError`.
4. **Transaction-Bound & Single-Use**: `_is_active: bool`; invalidated in `finally:`.
5. **Structural Binding**:
   - `service_instance_id: int`
   - `session_id: str`
   - `action: str`
   - `sequence_number: int`
   - `transaction_id: str` (UUID4)

### Direct-Call Enforcement:
- `ToolService.invoke_tool(context: ToolInvocationContext, capability: _ExecutionCapability) -> ToolResult`
- `NavigationService.submit_pose(pose: TrackedInstrumentPose, capability: _ExecutionCapability) -> None`
- `NavigationService.evaluate(session_id: str, capability: _ExecutionCapability) -> TrajectoryDeviationRecord`

*Missing, invalid, expired, or mismatched capability fails closed with an authorization error.*

---

## 10. M14 NAVIGATION EXECUTION

The sole external clinical execution path for instrument navigation is:
`execution.navigation.execute`

`ClinicalExecutionGatewayService` coordinates M18 Safety Gate and M10 Workflow authorization before issuing `_ExecutionCapability` to `NavigationService`.  
`NavigationService` remains a passive mathematical executor, **never** an execution coordinator.

---

## 11. M20 RECOVERY RESUMPTION ROUTE INTEGRATION

Dispatcher command:
`execution.workflow.resume`

Invokes the locked, authoritative M20 method:
`WorkflowService.resume_from_recovery(request: WorkflowResumptionRequest, safety_gate_service: Any)`

### Strict Constraints:
- Does **not** duplicate interlock clearance logic.
- Does **not** duplicate state machine transitions.
- Does **not** duplicate revision or sequence monotonicity checks.
- M20 remains **100% authoritative** for `RECOVERY_REQUIRED → NAVIGATION`.

---

## 12. M19 PROMOTED PYTHON APIS

Expose as dispatcher command routes:
1. `execution.recovery.execute` $\to$ Invokes `execute_recovery_reorientation(request)`
2. `execution.trajectory.bind` $\to$ Invokes `execute_trajectory_binding(request)`

Underlying dual-gate safety semantics remain strictly preserved.

---

## 13. GLOBAL EXECUTION GUARANTEE MATRIX

| Channel / Access Path | Guarantee Level | Technical Mechanism |
|---|:---:|---|
| **External Dispatcher** | **GUARANTEED** | Raw execution topics removed; unroutable topics fail closed into DLQ. |
| **External Gateway** | **GUARANTEED** | Gateway routes through dispatcher to registered `execution.*` commands. |
| **Direct M07 Python Call** | **GUARANTEED** | Mandatory `_ExecutionCapability` required by `ToolService.invoke_tool()`. |
| **Direct M14 Python Call** | **GUARANTEED** | Mandatory `_ExecutionCapability` required by `submit_pose()` & `evaluate()`. |
| **Internal Bus Call** | **GUARANTEED** | Dispatcher route registry permits only `execution.*` command topics. |
| **Internal Direct References** | **GUARANTEED** | Missing capability fails closed with `ExecutionAuthorizationError`. |
| **M20 Recovery Resumption** | **GUARANTEED** | Directly invokes locked `WorkflowService.resume_from_recovery()` transaction. |
| **Clinical M07 Tool Execution** | **GUARANTEED** | Dual-gated via M18 `TOOL_INVOCATION` and M10 `authorize_tool()`. |
| **Navigation Execution** | **GUARANTEED** | Dual-gated via M18 `TOOL_NAVIGATION` and M10 workflow phase check. |

---

## 14. MANDATORY SECURITY INVARIANTS

1. **Zero Raw Execution Routes**: No subsystem registers execution commands on `MessageDispatcher`.
2. **Zero Uncoordinated Direct Execution**: Subsystems reject calls lacking an active `_ExecutionCapability`.
3. **No External Capability Synthesis**: Capabilities cannot be created outside `holomed.execution`.
4. **No Serialization / Replay**: Capabilities cannot be pickled, reused, or replayed.
5. **Strict Monotonicity**: Sequence numbers must increase monotonically per `(session_id, action)`.
6. **No Actuation during Recovery**: When phase is `RECOVERY_REQUIRED`, all navigation and non-telemetry tool invocations are blocked.

---

## 15. REQUIRED TESTS

The M21 test suite must explicitly verify:
1. Old raw dispatcher routes (`navigation.pose.submit`, `navigation.evaluate`, `tools.invoke`) are absent and unroutable.
2. All new and inherited `execution.*` routes handle valid envelopes correctly.
3. Direct call to `ToolService.invoke_tool()` without capability raises authorization error.
4. Direct call to `NavigationService.submit_pose()` without capability raises authorization error.
5. Direct call to `NavigationService.evaluate()` without capability raises authorization error.
6. Valid coordinator capability allows execution to succeed.
7. Capability with mismatched `session_id` is rejected.
8. Capability with mismatched `action` is rejected.
9. Capability with non-monotonic sequence number is rejected.
10. Capability replayed after commit or abort is rejected.
11. Attempting to pickle or serialize capability raises `TypeError`.
12. `TOOL_INVOCATION` safety gate rules are enforced across all 5 M07 tools.
13. M10 workflow phase tool authorization is enforced.
14. In `RECOVERY_REQUIRED`, only `CAPTURE_TELEMETRY` is authorized.
15. `execution.workflow.resume` triggers M20 `resume_from_recovery()` successfully.
16. M20 recovery invariants (interlocks, rollback, single public path) remain intact.
17. Existing M19 navigation tests pass with zero regressions.
18. Full repository test suite passes cleanly.

---

## 16. IMPLEMENTATION RESTRICTION

- No feature, route, or abstraction may be added beyond this contract.
- Any requirement conflicting with this contract constitutes a formal **CONTRACT CHANGE REQUEST** and must halt implementation.

---

## 17. COMPLETION GATE

M21 is **NOT complete** until all criteria are satisfied:
1. Implementation adheres strictly to reopened files (`M07`, `M14`, `M18`, `M19`).
2. Hostile pre-commit audit passes.
3. Full repository regression suite passes (100% green).
4. Working tree is clean (`git status --short` is empty).
5. Exactly one commit created with message: `feat(M21): add universal clinical execution gateway`.
6. Commit pushed to `origin/main` without force push.
7. Local HEAD matches `origin/main`.
8. Final post-push release report generated.

---

## 18. CONTRACT STATUS

**STATUS**: `M21 CONTRACT LOCKED`  
**LOCKED AGAINST**:
- M19 Commit SHA: `e93f88a02e13cf170fc55cb12f5339a3638996ad`
- M20 HEAD SHA: `a7f1ee77dfa5040f8aa61e3f74159e56967650d5`
