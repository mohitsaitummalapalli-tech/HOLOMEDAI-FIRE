# HoloMed AI

HoloMed AI is designed as a medical spatial intelligence and interactive anatomical simulation platform with the future goal of unifying low-latency spatial tracking, multimodal intelligence, and real-time medical simulation within an interactive XR/desktop visualization runtime.

## Project Status

```text
Current milestone: M00 Foundation
Current work package: M00.1 Repository Bootstrap
```

This repository is currently in its foundational bootstrap phase. No runtime application features, machine learning solvers, vision pipelines, or visualization engines are implemented yet.

## Architecture

The project establishes clean modular boundaries and reserved namespaces designed to evolve incrementally across planned work packages:

* **Common (`holomed.common`)**: Architectural responsibility for shared primitives, base exception types, utility helpers, and foundational mathematical typing.
* **Configuration (`holomed.configuration`)**: Architectural responsibility for application configuration schemas, environment loading mechanisms, validation rules, and hardware device profile specifications.
* **Protocol (`holomed.protocol`)**: Architectural responsibility for inter-process communication contracts, serialization/deserialization schemas, event structures, and messaging envelopes.
* **Runtime (`holomed.runtime`)**: Architectural responsibility for process lifecycle supervision, async task scheduling, service orchestration, and graceful shutdown flows.
* **Core (`holomed.core`)**: Architectural responsibility for platform-level coordination and cross-domain synchronization; explicitly bounded to prevent arbitrary domain logic accumulation.
* **Devices (`holomed.devices`)**: Architectural responsibility for hardware abstraction interfaces governing sensor, camera, and audio input integration.

Future domain capabilities (including Vision, Gesture tracking, Hybrid solvers, Anatomy modeling, Simulation, and XR integration) will be introduced in subsequent milestone packages.

## Repository Structure

```text
HOLOMEDAI-FIRE/
├── README.md               # Project overview and setup guide
├── LICENSE                 # Apache 2.0 license
├── SECURITY.md             # Security policy and disclosure guidelines
├── CONTRIBUTING.md         # Developer contribution guidelines
├── CHANGELOG.md            # Release and change tracking
├── pyproject.toml          # Packaging and project configuration
├── .gitignore              # Git ignore rules for Python, Windows, and Unity
├── .env.example            # Environment variable template
├── docs/                   # Architectural decisions, contracts, and specifications
│   ├── architecture/
│   ├── decisions/
│   ├── contracts/
│   └── implementation/
├── protocol/               # Protocol schemas, fixtures, and examples
│   ├── schemas/
│   ├── examples/
│   └── fixtures/
├── python/                 # Python source packages
│   └── holomed/
│       ├── common/
│       ├── configuration/
│       ├── protocol/
│       ├── runtime/
│       ├── core/
│       └── devices/
├── tests/                  # Automated test suites
│   └── unit/
├── benchmarks/             # Performance and latency benchmark suites
├── experiments/            # Isolated experimental evaluations
├── content/                # Static assets, models, and references
├── datasets/               # Reference datasets and sample captures
├── tools/                  # Developer utilities and diagnostics
├── scripts/                # Automation and build scripts
├── config/                 # Static configuration files
└── artifacts/              # Local build and test artifacts (ignored)
```

## Development Setup

Prerequisites:
* Python 3.14 (3.14.x)
* Git

To install the foundational package in editable development mode with testing dependencies:

```bash
python -m pip install -e ".[test]"
```

## Testing

Run unit tests via `pytest`:

```bash
python -m pytest -q
```

Verify package importability:

```bash
python -c "import holomed; print(holomed.__version__); print(holomed.__app_name__)"
```

## Security Note

HoloMed AI treats external inputs and AI model outputs as untrusted. Never commit API keys, environment credentials, or patient health information (PHI) to version control. See [SECURITY.md](SECURITY.md) for vulnerability reporting procedures.

## License

This project is licensed under the [Apache License 2.0](LICENSE).
