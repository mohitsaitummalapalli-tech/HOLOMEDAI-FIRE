# ADR-001: Clean Project Bootstrap and Foundational Architecture

## Status
Accepted

## Context
Previous prototype iterations of HoloMed and related exploratory codebases proved conceptual viability but accumulated tight cross-module coupling, untyped IPC interfaces, and unstructured dependencies. To support high-fidelity medical spatial intelligence and real-time anatomical simulations, a deterministic, clean, and extensible architecture is required.

## Decisions
1. **Canonical Repository**: This repository (`HOLOMEDAI-FIRE`) is established as the sole canonical implementation of HoloMed AI.
2. **Decoupling from Legacy Prototypes**: Previous experimental implementations are intentionally discarded as runtime dependencies. No legacy code is assumed to be reusable without formal design and re-implementation under established quality gates.
3. **Incremental Milestone Execution**: System architecture and capabilities will be delivered through discrete, testable, and deterministic Work Packages (beginning with `M00.1`).
4. **Contract-First & Stable Module Boundaries**: All interactions between subsystems (Common, Configuration, Protocol, Runtime, Core, Devices) must rely on explicit schemas and abstract contracts rather than internal implementation details.

## Consequences
* **Positive**:
  - Predictable build, test, and deployment cycles.
  - Clear separation of concerns, eliminating accidental dependency bloat.
  - Seamless modular evolution towards vision, gesture tracking, physics simulation, and XR visualization.
* **Negative / Trade-offs**:
  - Requires re-specifying and implementing all subsystems from first principles.
