# HoloMed AI — Protocol Specification (v1.0)

## 1. Overview

The HoloMed Protocol defines the standardized, transport-agnostic message exchange format for all HoloMed AI communication between Python subsystems, Unity visualizers, and external integration points.

The protocol enforces:
- Strict schema conformance (JSON Schema Draft 2020-12).
- Zero transport dependency (pluggable over WebSockets, IPC, Shared Memory, TCP, or files).
- Exact causal tracing and correlation across asynchronous pipelines.
- Deterministic, byte-reproducible JSON serialization.

---

## 2. Canonical Wire Format (`MessageEnvelope`)

Every protocol message is wrapped in a single, top-level JSON object containing exactly 11 required fields. Unknown top-level fields are strictly rejected (`additionalProperties: false`).

```json
{
  "protocol_version": "1.0",
  "message_id": "10000000-0000-4000-8000-000000000001",
  "correlation_id": "10000000-0000-4000-8000-000000000001",
  "causation_id": null,
  "message_type": "COMMAND",
  "message_name": "device.camera.start",
  "source": "core.orchestrator",
  "target": "device.camera",
  "timestamp_utc": "2026-08-29T20:00:00.000000Z",
  "payload": {
    "device_id": "cam_0",
    "fps": 60
  },
  "metadata": {
    "trace_id": "trc_1001"
  }
}
```

### 2.1 Field Definitions & Grammar

| Field | Type | Nullable | Grammar & Rules |
| :--- | :--- | :--- | :--- |
| `protocol_version` | `string` | No | `^(0\|[1-9][0-9]*)\.(0\|[1-9][0-9]*)$`. Leading zeroes are forbidden. Baseline: `"1.0"`. |
| `message_id` | `string` | No | `^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$` (Canonical lowercase UUIDv4). |
| `correlation_id` | `string` | No | `^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$` (Canonical lowercase UUID). |
| `causation_id` | `string \| null` | Yes | Canonical lowercase UUID of immediate parent message, or `null` for root messages. |
| `message_type` | `string` | No | One of: `"COMMAND"`, `"QUERY"`, `"EVENT"`, `"RESPONSE"`, `"ERROR"`. |
| `message_name` | `string` | No | `^[a-z0-9_]+(\.[a-z0-9_]+)*$` ($\le 128$ characters). |
| `source` | `string` | No | `^[a-z0-9_]+(\.[a-z0-9_]+)*$` ($\le 128$ characters). |
| `target` | `string \| null` | Yes | `^[a-z0-9_]+(\.[a-z0-9_]+)*$` ($\le 128$ characters), or `null` for unaddressed broadcast messages. |
| `timestamp_utc` | `string` | No | `^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{6}Z$` (UTC ISO 8601 with 6-digit microseconds and `Z`). |
| `payload` | `object` | No | JSON object containing domain data or `ErrorPayload`. |
| `metadata` | `object` | No | JSON object reserved exclusively for protocol/routing/tracing context. |

---

## 3. Message Classifications

1. **`COMMAND`**: A request directed to a specific subsystem to perform a state-altering action.
2. **`QUERY`**: A request to retrieve state information without causing side-effects.
3. **`EVENT`**: An asynchronous notification of a state change, sensor capture, or occurrence. Can be unaddressed (`target: null`).
4. **`RESPONSE`**: Direct reply answering a preceding `COMMAND` or `QUERY`.
5. **`ERROR`**: Failure notification carrying structured error diagnostics.

---

## 4. Causal Correlation Semantics

* **Root Initiator** (`COMMAND`, `QUERY`, root `EVENT`):
  - `correlation_id = message_id`
  - `causation_id = null`
* **Derived Event** (e.g. state transition caused by command):
  - `event.correlation_id = parent.correlation_id`
  - `event.causation_id = parent.message_id`
* **Direct Response / Error Response**:
  - `response.correlation_id = request.correlation_id` (enforced invariant)
  - `response.causation_id = request.message_id` (enforced invariant)
  - `response.target = request.source` (enforced invariant)
  - `response.source = responder_source`

---

## 5. Error Model (`ErrorPayload`)

When `message_type == "ERROR"`, the `payload` object must conform to `ErrorPayload`:

```json
{
  "error_code": "ERR_DEVICE_UNAVAILABLE",
  "error_message": "Camera device is disconnected or busy.",
  "details": {
    "device_id": "cam_0"
  },
  "recoverable": true
}
```

* `error_code`: `^ERR_[A-Z0-9_]+$` ($\le 64$ chars).
* `error_message`: Non-empty string ($\le 1024$ chars).
* `details`: JSON object with diagnostic key-values.
* `recoverable`: Boolean indicating if sender may retry.

---

## 6. Serialization & Safety Bounds

* **Deterministic Encoding**: Sorted keys (`sort_keys=True`), compact separators (`separators=(',', ':')`), UTF-8 encoding, `ensure_ascii=False`, `allow_nan=False`, and **no trailing newline**.
* **Duplicate Key Rejection**: Duplicate JSON keys at top-level or inside nested objects are strictly rejected.
* **Non-finite Number Rejection**: `NaN`, `Infinity`, `-Infinity` are rejected during both serialization and deserialization.
* **Wire Size Limit**: Maximum 1,048,576 bytes (1 MiB) UTF-8. Exactly 1,048,576 bytes is accepted; >1,048,576 bytes is rejected.
* **Nesting Depth Limit**: Maximum 32 levels (root object = depth 1). Depth 32 is accepted; depth 33 is rejected.
* **Parser-Level Depth Guard (`SafeJSONDecoder`)**: Relies intentionally on CPython stdlib JSON decoder internals (`parse_object`, `parse_array`, `scanner`) for zero-dependency parser-level recursion depth protection, officially supported and tested on Python 3.14.x.

---

## 7. Schema vs. Runtime Validation Architecture

* **JSON Schema (Draft 2020-12)**: Serves as the language-agnostic, canonical structural contract (`protocol/schemas/envelope.schema.json` and `protocol/schemas/error.schema.json`) for cross-language validation (Python, Unity C#, Web).
* **Python Runtime Validator**: Implements 100% standard-library runtime enforcement of safety bounds (wire size limits, parser-level depth bounding, duplicate key rejection, NaN rejection) and semantic invariants (causality rules, calendar timestamps, UUIDv4 checks). It does not invoke an external Draft 2020-12 schema validator at runtime, preserving zero external dependencies.
* **Contract Drift Tests**: Automated test suites (`test_schema_contract.py`) enforce bidirectional consistency between Python dataclass models/enums and JSON Schema declarations to prevent specification drift.

---

## 8. Version Compatibility & Immutability Semantics

### 8.1 Versioning Rules
* Protocol versions use canonical `MAJOR.MINOR` format without leading zeroes.
* **Same Major, Lower/Equal Minor** (`major == current_major`, `minor <= current_minor`): Fully compatible and accepted.
* **Same Major, Higher Minor** (`major == current_major`, `minor > current_minor`): Accepted forward-compatibly provided the message conforms to the currently known envelope contract.
* **Different Major** (`major != current_major`): Strictly rejected as incompatible.
* **Non-canonical / Malformed Versions**: Strictly rejected.

### 8.2 Immutability Model
* Python wire models (`MessageEnvelope`, `ErrorPayload`) utilize `@dataclass(frozen=True)` to enforce top-level attribute immutability.
* Nested structures (`payload`, `metadata`, `details`) are copied at boundaries (`_normalize_dict_input`, `dict()`) but are not recursively frozen.
