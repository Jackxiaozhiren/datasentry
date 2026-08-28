# Security policy

DataSentry processes data that may contain sensitive information, so security reports should be handled differently from ordinary bug reports.

## Supported versions

Security fixes are prioritized for the latest released version and the current `main` branch. Older releases may not receive backports unless a maintainer explicitly announces otherwise.

## Reporting a vulnerability

Please **do not open a public GitHub issue** for a vulnerability that could expose data, credentials, encryption material, authorization boundaries, or remote execution paths.

Use GitHub's private vulnerability reporting / Security Advisories feature for this repository when available. Include enough information to reproduce and assess the issue:

- affected DataSentry version or commit;
- operating system and Python version;
- affected interface (CLI, REST, Web UI, MCP, worker, connector, repair, PII vault, etc.);
- minimal reproduction steps;
- expected and observed behavior;
- security impact and required attacker access;
- logs or traces with secrets and real customer data removed.

If private vulnerability reporting is unavailable, open a minimal public issue asking for a private security contact **without disclosing exploit details**.

## Sensitive areas

Reports involving the following deserve particular care:

- secrets or DSNs appearing in logs, reports, exceptions, or telemetry;
- path traversal or arbitrary file access;
- unsafe repair operations or source-file overwrite paths;
- authentication/authorization boundaries in REST or distributed workers;
- MCP tool surfaces that bypass approval or project boundaries;
- PII redaction, encrypted-vault storage, key rotation, or restoration;
- prompt/data leakage to configured LLM providers;
- deserialization, command injection, SQL injection, or remote-code execution;
- malicious plugin loading or integrity-check bypass.

## Security invariants

The project aims to preserve these invariants:

1. Detection does not require sending data to an LLM.
2. AI-generated repair proposals do not silently mutate source data.
3. Repair applies to a copy and remains auditable/reversible.
4. Credentials should come from environment/secrets mechanisms and should not be logged.
5. Sensitive values sent to an LLM should pass through the configured redaction path.
6. External plugins and distributed execution should not weaken workspace or integrity boundaries.

## Disclosure

Please allow maintainers an opportunity to investigate and release a fix before publishing exploit details. Once a fix is available, coordinated disclosure is welcome.
