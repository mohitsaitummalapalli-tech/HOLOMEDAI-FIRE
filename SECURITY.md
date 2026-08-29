# Security Policy

## Security Principles

1. **No Secrets in Source Control**: Credentials, private keys, API tokens (such as Gemini API keys), and sensitive configurations must never be committed to Git. Use environment variables managed via local `.env` files (derived from `.env.example`).
2. **Untrusted AI and External Outputs**: All responses and payloads originating from AI models, external microservices, and network protocols are treated as untrusted and must undergo strict schema validation and sanitization prior to execution or rendering.
3. **Privileged Actions Authorization**: Sensitive, administrative, or state-altering system operations (such as configuration modification, external tool execution, or privileged OS commands) require explicit policy authorization and verification, distinguishing them from standard read-only queries or routine sensor polling.
4. **Experimental vs. Production Separation**: Experimental scripts located in `experiments/` or `benchmarks/` must remain strictly isolated from core production pathways.
5. **Data Privacy**: No patient identifiers or protected health information (PHI) should ever be ingested or stored in repository assets.

## Reporting a Vulnerability

If you discover a security vulnerability within this repository:

1. **Do not open a public issue or discussion.**
2. Report the vulnerability privately using GitHub's **Private Vulnerability Reporting** feature (via the **Security** tab -> **Advisories** -> **Report a vulnerability**) on the official repository.
3. The repository maintainers will review the submission and respond through the advisory channel.
