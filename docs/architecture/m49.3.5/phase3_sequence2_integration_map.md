# Phase 3.2 Sequence 2: Recovery State Machine Integration Map

## 1. Purpose
The purpose of Sequence 2 is to deterministically integrate the verified `StateRehydrationEngine` into the production device-control lifecycle (`DeviceControlManager`). This integration ensures that upon controller restart or device reset, durable operations are correctly classified and safely quarantined without releasing physical capacity.

Integration of the `StateRehydrationEngine` into `DeviceControlManager` is **mandatory**. A physical-control manager MUST NOT become admission-capable (enter the `READY` state) unless recovery integration is present and rehydration completes successfully.

## 2. Current Production Lifecycle
Currently, `DeviceControlManager` transitions from `UNINITIALIZED` → `INITIALIZED` → `STARTED`. 
Admission to physical operations is permitted as soon as the manager is `STARTED`. The current lifecycle lacks a dedicated `REHYDRATING` or `RECOVERY` gate, leaving the system vulnerable to admitting new operations over orphaned physical capacity on restart.

## 3. Controller Restart Flow
Upon controller restart, the system must execute the following independent flow:

```text
old controller authority disappears
→ new authoritative controller epoch
→ durable rehydration
→ recovered operations classified
→ capacity reconstructed
→ recovery-required endpoints remain blocked
```
Endpoints hosting recovered active operations remain locked from new admissions until explicitly cleared by an operator/coordinator.

## 4. Device Restart Flow
Upon device restart, the system must execute the following independent flow via an explicit detection mechanism (device registry hook/event):

```text
device reboot/reset detected
→ device epoch / lifecycle generation validation
→ physical state becomes unknown where required
→ existing capacity remains held
→ no success is inferred
→ explicit recovery/isolation/reinitialization path
```

## 5. Exact Startup Ordering
To prevent race conditions between admission and rehydration, `DeviceControlManager.start()` MUST enforce the following deterministic startup ordering:

```text
manager construction
→ authoritative controller epoch establishment
→ durable store/journal initialization
→ integrity/tail validation
→ durable active-operation discovery
→ StateRehydrationEngine
→ recovered endpoint/operation classification
→ admission gate establishment
→ manager becomes READY/admission-capable
```
Admission strictly remains blocked until this sequence completely resolves.

## 6. Readiness / Admission Gate
`DeviceControlManager` prevents physical admission before recovery is complete by introducing explicit readiness states:

```text
INITIALIZING
→ REHYDRATING
→ READY
```
Where a failed or incomplete rehydration prevents the transition to `READY`. If a device requires recovery, its specific endpoint is blocked:
```text
INITIALIZING
→ REHYDRATING
→ RECOVERY_REQUIRED / QUARANTINED (for specific devices/endpoints)
```

**Invariant:**
```text
not READY
→ physical admission forbidden
```

The architecture explicitly prevents the following adversarial scenario:
```text
restart
→ rehydration still running
→ concurrent admission
```
Because the global admission gate remains closed until `READY`.

## 7. Component Responsibility Matrix
The architecture clearly separates responsibilities, preventing a competing recovery state machine inside `DeviceControlManager`:

- **DeviceControlManager**: Enforces the readiness/admission gate. Blocks execution until `READY`. Triggers `StateRehydrationEngine` on startup or device epoch changes. Does NOT mutate physical state directly for recovery.
- **StateRehydrationEngine**: Discovers durable operations. Mutates persistence records to classify operations as `FAULTED_UNKNOWN` using the new authoritative epoch. Never executes physical recovery.
- **RecoveryCoordinator (Phase 3.1)**: Explicitly orchestrates and executes physical recovery, reinitialization, and capacity release based on evidence.
- **ExecutionResolutionGate**: Evaluates terminal boundaries and timeouts. Defers to `StateRehydrationEngine` on system boot.
- **Persistence/session journal**: Owns the canonical durable operation log. Enforces the lock hierarchy. Routes state mutations to the original session.
- **IPhysicalEndpoint**: Executes physical commands; entirely isolated from durable/recovery logic.

## 8. Lock / Concurrency Boundaries
The system strictly adheres to the sealed Phase 2 lock hierarchy during all recovery and admission paths:

```text
EPOCH AUTHORITY LOCK
→ GLOBAL PHYSICAL ADMISSION LOCK
→ SESSION JOURNAL LOCK
```

For every recovery/admission boundary:
```text
lock acquired (GLOBAL PHYSICAL ADMISSION LOCK via DurableSessionStore)
→ protected state read/write (Rehydrate operation / append FAULTED_UNKNOWN)
→ lock release
```
All concurrent admission attempts blocking on the global physical admission lock will either see the capacity reserved or the endpoints quarantined once the lock is released.

## 9. Durable Session Routing
Recovered operations must be written back to their originating context. Transient memory (`_execution_to_session`) is NOT authoritative.
```text
durable operation
→ original session_id
→ recovery mutation
→ original durable session journal
```

The canonical identity must remain uncorrupted during this routing:
```text
(
    device_id,
    device_epoch,
    controller_epoch,
    physical_operation_id,
    command_nonce
)
```
The historical `controller_epoch` inside the operation identity MUST remain unchanged. The new authoritative controller epoch is passed separately to authenticate the recovery mutation.

## 10. Failure-Closed Behavior
In all failure cases during startup/recovery, the system fails closed:

- **Journal corruption**: Manager State = `FAILED`, Capacity = Retained, Admission = Forbidden, Physical = Unknown. Required Action: Manual intervention / structural audit.
- **Journal tail failure**: Manager State = `FAILED`, Capacity = Retained, Admission = Forbidden, Physical = Unknown. Required Action: Manual sync / recovery.
- **Authority establishment failure**: Manager State = `FAILED`, Capacity = Retained, Admission = Forbidden, Physical = Unknown. Required Action: Restart / Authority resolution.
- **Device epoch mismatch**: Manager State = `READY`, Capacity = Retained on specific device, Admission = Forbidden on device, Physical = Unknown. Required Action: Device isolation / explicit `rehydrate_device_state`.
- **Rehydration failure**: Manager State = `FAILED`, Capacity = Retained, Admission = Forbidden, Physical = Unknown. Required Action: Restart system.
- **Inconsistent durable operation**: Manager State = `READY`, Capacity = Retained on specific device, Admission = Forbidden on device, Physical = Unknown. Required Action: Endpoint quarantined.
- **Unknown physical state**: Manager State = `READY`, Capacity = Retained, Admission = Forbidden for occupied endpoints. Required Action: Operator/RecoveryCoordinator resolves `FAULTED_UNKNOWN`.

## 11. Idempotency / Replay
If a restart is triggered multiple times or operations are re-evaluated:
```text
same restart recovered twice
same operation encountered twice
same recovery classification replayed
same terminal record already exists
stale controller attempts recovery
```

Required guaranteed outcome:
```text
no duplicate terminal record
no capacity double-release
no identity rewriting
no stale-authority mutation
```

## 12. Sequence 2 Production Test Matrix
The test suite MUST contain the following adversarial concurrency proof using deterministic synchronization (no `sleep` or timing-based synchronization):

```text
restart
→ new authoritative epoch
→ recovery starts
→ concurrent admission attempt
→ recovered operation still owns physical capacity
→ admission rejected
→ recovery continues
```

Additional proofs required:
- Fail-closed verification on missing authority.
- Fail-closed verification on corrupt journal.
- Routing isolation proving rehydration to `session1` journal while `session2` is active.

## 13. Open Risks / Explicit Non-Goals
- **Physical Integration**: Interfacing with physical hardware for recovery is explicitly out of scope. `FAULTED_UNKNOWN` is a terminal control-plane state where:
  - physical state is unknown.
  - capacity remains retained.
  - no completion claim is made.
  - no isolation claim is made.
- **Session Migration**: Sessions cannot migrate. Recovery guarantees writes to the original durable session.
