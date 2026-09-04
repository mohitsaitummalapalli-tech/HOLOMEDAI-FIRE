# M32 RELEASE REPORT

## Release Metadata
- **Milestone**: M32 — Clinical Data Isolation, Lifecycle Retention & Cross-Service Contract Hardening
- **Authoritative Baseline SHA**: `daf8324453378bb1f45e84de26e09479c8ad75ff`
- **Parent Commit SHA**: `daf8324453378bb1f45e84de26e09479c8ad75ff`
- **Commit Message**: `feat(M32): enforce clinical data isolation and lifecycle ownership`
- **Release Target Branch**: `origin/main`
- **Date/Time**: 2026-09-04T22:06:00+05:30
- **Final Classification**: `M32_RELEASED`

---

## 1. Precommit Verifications

### Test Suite Regression
```
$ python -m pytest -q -ra
1651 passed in 6.80s
```

### Targeted Milestone Regression
- `tests/unit/gateway/`: 75 passed
- `tests/unit/tools/`: 34 passed
- `tests/unit/planning/`: 44 passed
- `tests/unit/execution/`: 164 passed
- `tests/unit/persistence/`: 33 passed
- `tests/unit/safety_gate/`: 73 passed
- `tests/unit/execution/test_m29_tool_lifecycle.py`: 23 passed

### Pyright Static Type Checking
```
$ npx -y pyright python/holomed/tools/models.py python/holomed/tools/engine.py python/holomed/tools/service.py python/holomed/planning/service.py python/holomed/gateway/authorization.py python/holomed/persistence/service.py tests/unit/tools/test_tool_service.py tests/unit/planning/test_planning_service.py
0 errors, 0 warnings, 0 informations
```

### Git Diff Whitespace & Syntax Check
```
$ git diff --check
(clean output, exit code 0)
```

---

## 2. Release Scope & Diff Summary

### Production Files (7 Files)
- `python/holomed/tools/models.py`: Added `session_id` field and non-empty string validation to `ToolResult`.
- `python/holomed/tools/engine.py`: Scoped `ToolExecutionEngine.get_result` to mandatory caller session; purged owned results in `evict_session`.
- `python/holomed/tools/service.py`: Enforced session ownership in `ToolService.handle_result_query`, failing closed with `ERR_RESULT_NOT_FOUND`.
- `python/holomed/planning/service.py`: Enforced session ownership in `PlanningService.handle_get_query`, eliminating unauthenticated fallback; purged plans and verification records in `evict_session`.
- `python/holomed/gateway/authorization.py`: Removed `"workflow.interlock.trip"` from `CLIENT_ISSUABLE_ROUTES`; stamped authenticated `session_id` into payload and metadata; added unroutable dispatcher exception handling.
- `python/holomed/execution/service.py`: Aligned recovery reset command to call canonical `RecoveryService.reset_session(session_id)`.
- `python/holomed/persistence/service.py`: Enforced `validate_session_path` in `handle_session_get_query` and `handle_cycle_get_query` to block path traversal.

### Test Files (6 Files)
- `tests/unit/execution/test_clinical_execution_gateway.py`
- `tests/unit/gateway/test_gateway_authorization.py`
- `tests/unit/gateway/test_m31_gateway_boundary.py`
- `tests/unit/persistence/test_persistence_service.py`
- `tests/unit/planning/test_planning_service.py`
- `tests/unit/tools/test_tool_service.py`

### Documentation Artifacts (9 Files)
- `M32_CONTRACT_SPEC.md`
- `M32_DISCOVERY_REPORT.md`
- `M32_FINAL_FEASIBILITY_REPORT.md`
- `M32_HOSTILE_AUDIT_REPORT.md`
- `M32_REMEDIATION_REPORT.md`
- `M32_HOSTILE_REAUDIT_REPORT.md`
- `M32_PRECOMMIT_RELEASE_AUDIT.md`
- `M32_IMPLEMENTATION_REPORT.md`
- `M32_RELEASE_REPORT.md`

---

## 3. Hostile Security Audit Closures
1. **`planning.get` Omission / Null Session Bypass:** CLOSED. All unauthenticated, null, empty, whitespace, and cross-session queries return `ERR_PLAN_NOT_FOUND` fail-closed.
2. **`tools.result` Null-Session Query Bypass:** CLOSED. Mandatory non-empty string caller identity matching authenticated context required; returns `ERR_RESULT_NOT_FOUND`.
3. **`tools.result` Direct Engine Lookup Bypass:** CLOSED. `ToolExecutionEngine.get_result` enforces mandatory caller identity argument; returns `None` fail-closed.
4. **Gateway Session Stamping:** Established authoritative identity at ingress; prevents cross-session spoofing.
5. **Persistence Path Traversal:** Validates session identifiers before filesystem access, blocking directory traversal.

---

## 4. Frozen Milestone Confirmation
- **M19–M31 Invariants:** 100% frozen, intact, and preserved.
- Zero regression across M28 gateway session isolation, M29 tool sequence tracking/capacity, M30 safety gate boundaries, and M31 ingress hardening.
