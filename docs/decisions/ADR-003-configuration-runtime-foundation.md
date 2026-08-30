# ADR-003: Configuration & Runtime Foundation Architecture

## Status
Accepted / Locked

## Context
Milestone M00.3 establishes the foundational configuration management, service model, lifecycle state machine, and in-process runtime orchestration for HoloMed AI. The system requires strict determinism, immutable configuration epochs, reliable partial failure rollback, zero secret leakage, and clean non-interference with frozen baselines M00.1 and M00.2.

## Decision
1. **Zero External Runtime Dependencies**: Standard library only (`dependencies = []` in `pyproject.toml`) targeting CPython 3.14.x.
2. **Sentinel Precedence Loader**: Implemented `_UNSET` sentinel distinguishing omitted kwargs from explicit `None`. Explicit `None` for non-optional fields strictly raises `ConfigurationTypeError`.
3. **Immutable Configuration & SecretString**: `AppConfig` is frozen; `SecretString` masks secrets across `repr`, `str`, and `__format__`, accessible solely via `get_secret_value()`.
4. **Authoritative 7-State Lifecycle Machine**: `RuntimeEngine` is the single authority for externally visible `ServiceState` across all 49 transition cells in the $7 \times 7$ state matrix.
5. **Deterministic Topological Scheduling**: Kahn's algorithm with min-heap priority queue enforces deterministic alphabetical tie-breaking on startup and reverse topological ordering on teardown.
6. **Resource Accounting Model**: `OwnedResourceSet` tracks handles (`ACQUIRED`, `RELEASED`, `UNRELEASED_FAILURE`). `service.stop()` return value is not assumed to indicate release; engine validates emptiness postcondition before assigning `STOPPED`.
7. **Monotonic Epoch Identity & Cumulative Lifetime Freshness**: Monotonic counter `_next_epoch_id` never resets across retirements. Cumulative lifetime retention `_previous_epoch_instances` prevents object-ID recycling and service instance reuse across all historical epochs.
8. **Operationally Retryable FAILED State**: `retry_cleanup()` operates strictly on dirty services while keeping engine state `FAILED`, publicly raising `ResourceCleanupRequiredError` if resources remain.
9. **Guarded Execution Boundary**: `run_guarded()` catches unhandled exceptions, logs structured error, triggers reverse teardown, transitions to `FAILED`, and preserves cause chaining.

## Consequences
- Clean, decoupled architecture with 100% deterministic, testable behavior.
- Zero risk of secret exposure in logs or health metrics.
- Genuinely robust rollback and cleanup mechanics preventing orphaned system resources.
