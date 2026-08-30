# ADR-002: Foundational Protocol Contracts and Wire Standards

## Status
Accepted

## Context
HoloMed AI requires a unified, high-performance, and deterministic communication protocol across diverse subsystems, including Python AI pipelines, Unity C# visualizers, and spatial hardware drivers. Ad-hoc message dictionaries and loose conventions lead to schema drift, parsing race conditions, and uncorrelatable asynchronous failures.

## Decisions

1. **Unified Canonical Wire Envelope**: All inter-subsystem messages will use a single top-level `MessageEnvelope` containing exactly 11 required fields.
2. **Semantic Factory Abstractions**: Message classifications (`Command`, `Query`, `Event`, `Response`, `Error`) are exposed as factory builder functions over `MessageEnvelope`, preventing model duplication.
3. **Draft 2020-12 Schema Standard**: Schema contracts are authored in JSON Schema Draft 2020-12 as language-agnostic structural contracts for cross-language validation (Python, C#, Web).
4. **Pure Standard-Library Runtime Validation**: The Python runtime protocol layer is implemented using 100% Python standard library components, enforcing safety limits (1 MiB wire limit, max depth 32 during parse, duplicate key rejection, NaN rejection) and semantic invariants without runtime external schema dependencies.
5. **Version Compatibility**: Versioning follows strict `MAJOR.MINOR`. Messages under the same major version with equal or higher minor version are accepted forward-compatibly if conforming to the known envelope contract; different major versions are rejected.
6. **Strict Rejection Policy**:
   - Duplicate JSON keys at any nesting level are strictly rejected.
   - Non-finite numbers (`NaN`, `Infinity`) are rejected on serialization and deserialization.
   - Unknown envelope fields are strictly forbidden (`additionalProperties: false`).
   - Leading zeroes in protocol versioning (e.g. `01.0`) are forbidden.
7. **Immutable Response Invariants & Immutability Model**: Builders enforce immutable linkage of `correlation_id`, `causation_id`, and `target`. `@dataclass(frozen=True)` provides top-level field immutability.

## Consequences
* **Positive**:
  - Deterministic byte-for-byte serialization and parsing.
  - Zero external runtime dependency overhead.
  - Seamless cross-language parity with Unity/C#.
  - Robust causality and distributed trace reconstruction across pipelines.
  - Parser-level depth bounding prevents stack exhaustion.
* **Negative / Trade-offs**:
  - Requires explicit schema validation on every message ingress.
