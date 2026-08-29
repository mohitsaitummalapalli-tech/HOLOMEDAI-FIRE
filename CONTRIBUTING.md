# Contributing to HoloMed AI

Thank you for contributing to HoloMed AI. All contributions must adhere to our engineering guidelines and architectural discipline.

## Work Package Workflow

1. **Work Package Alignment**: Every code modification must map directly to a designated Milestone (e.g., `M00`) and Work Package (e.g., `M00.1`).
2. **Branch Strategy**: Branch names should follow the convention `feature/M<milestone>.<package>-<brief-description>` (e.g., `feature/M00.1-repo-bootstrap`).
3. **Traceability**: Commit messages must reference the specific work package and clearly articulate architectural changes.

## Architectural Boundaries

* **Strict Modularity**: Maintain clear separation between `common`, `configuration`, `protocol`, `runtime`, `core`, and `devices`.
* **Contract-First Design**: Modules interact via well-defined contracts and interfaces rather than accessing private implementation internals.
* **No Cross-Module Hacks**: Ad-hoc imports across isolated subsystem boundaries are prohibited.

## Quality and Testing

* **Deterministic Tests**: All new features and refactors must include deterministic automated unit tests.
* **Pre-Merge Validation**: Before submitting or merging any changes, ensure:
  - `python -m pytest -q` passes with zero failures.
  - Package discovery and importability are verified.
* **Documentation**: Update architectural decision records (ADRs) under `docs/decisions/` whenever introducing significant structural changes.

## Security & Secrets

* Never commit secrets, tokens, or environment credentials to Git.
* Validate that all local environment additions are reflected as empty placeholders in `.env.example`.
