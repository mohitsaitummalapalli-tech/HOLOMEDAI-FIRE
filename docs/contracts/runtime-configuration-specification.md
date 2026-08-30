# Runtime & Configuration Specification

**Milestone**: M00.3 — Configuration & Runtime Foundation  
**Status**: Contract Locked & Authoritative  
**Standard Compliance**: Standard Library Only (CPython 3.14.x)

---

## 1. Subsystem Architecture

### 1.1 `holomed.configuration`
- **`AppConfig`**: Genuinely immutable (`frozen=True`) configuration descriptor.
- **`SecretString`**: Protection container masking raw secret values in `__repr__`, `__str__`, and `__format__`, accessible solely via `get_secret_value()`.
- **`load_config()`**: Sentinel-based loader resolving precedence:
  $$\text{Explicit kwargs (non-\_UNSET)} \succ \text{Provided env mapping} \succ \text{os.environ} \succ \text{Documented Defaults}$$
  Explicit `None` for non-optional fields raises `ConfigurationTypeError`.
- **Semantic Validation**: Hostnames (RFC 1123 / IP address), Ports ($1 \dots 65535$), App Name ($1 \dots 128$), Environment Profiles (`DEVELOPMENT`, `STAGING`, `PRODUCTION`, `TESTING`), Log Levels (`DEBUG`, `INFO`, `WARNING`, `ERROR`, `CRITICAL`), Protocol Version (`"1.0"`).

### 1.2 `holomed.runtime`
- **`RuntimeState` Machine**: Deterministic 7-state lifecycle machine (`NEW`, `INITIALIZING`, `READY`, `RUNNING`, `STOPPING`, `STOPPED`, `FAILED`) governing 49 transition cells (14 legal non-state-preserving, 6 state-preserving, 29 forbidden).
- **`IService`**: Abstract service lifecycle interface (`initialize`, `start`, `stop`, `health`).
- **`ServiceRegistration`**: Genuinely frozen descriptor storing `name`, `factory`, and `dependencies`.
- **`compile_topology()`**: Deterministic topological sorter using Kahn's algorithm with min-heap alphabetical tie-breaking and canonical cycle path reporting.
- **`OwnedResourceSet`**: In-process resource accounting proving tracked resource ownership (`ACQUIRED`, `RELEASED`, `UNRELEASED_FAILURE`).
- **`ConfigurationEpoch` & `EpochDiagnosticRecord`**: Immutable configuration snapshot and retirement audit records. Monotonic `_next_epoch_id` guarantees identity preservation across retirements.
- **Cumulative Lifetime Freshness**: Strong reference retention across all historically active epochs preventing object-ID recycling and multi-epoch service reuse.
- **`StructuredLogger` & `SecretFilter`**: Standard library JSON logging with trace context correlation (`correlation_id`, `causation_id`) and atomic secret redaction.
- **`HealthEvaluator`**: Synchronous memory-only health evaluation with exception boundary mapping to `FAILED`.
- **`run_guarded()`**: Operational execution boundary executing reverse topological teardown and causal exception chaining on fatal failures.

---

## 2. Invariants & Mathematical Theorems

1. **STOPPED Resource Necessity Theorem**:
   $$\text{service\_state} == \text{STOPPED} \implies \text{service.resources.outstanding\_handles} == \text{frozenset}()$$
   *Zero outstanding resources is necessary for `STOPPED`, but not sufficient by itself.*
   **Conditional Converse**:
   $$\big(\text{service\_state} \in \{\text{INITIALIZED}, \text{STARTED}, \text{FAILED}\} \land \text{service.stop() executed} \land \text{service.resources.outstanding\_handles} == \text{frozenset}()\big) \implies \text{service\_state} == \text{STOPPED}$$
2. **Start Resource Acquisition Non-Acquisition**:
   $$\text{resources\_after} == \text{resources\_before}$$
   Acquiring new resources in `start()` raises `ServiceStartupError`, triggers rollback, and assigns `FAILED`.
3. **Epoch Retirement Safety Gate**:
   $$\text{_retire_epoch()} \text{ is legal } \iff \text{outstanding\_tracked\_resources} == 0$$
   Retirement clears `active_epoch = None`, `services = {}`, `service_states = {}`.
4. **Operationally Retryable FAILED State**:
   `retry_cleanup()` preserves engine `FAILED` state while re-attempting cleanup on dirty services in reverse topology and alphabetical resource-ID order. Publicly raises `ResourceCleanupRequiredError` if resources remain.
