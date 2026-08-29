# Recommended GitHub repository settings — Growth V3

These settings are intentionally kept outside runtime documentation because they are repository-discovery metadata rather than product behavior.

## About description

Recommended:

> Find, explain, and safely fix bad data. Local-first data quality for files, databases, CI pipelines, and AI agents.

Shorter alternative:

> Find bad data before your users do — local-first detection, evidence, and reversible repair.

Avoid listing detector counts, schedulers, worker pools, REST, MCP, and every connector in the About description. Those capabilities belong in the README after the main value proposition is clear.

## Website

Keep:

`https://jackxiaozhiren.github.io/datasentry/`

## Topics

Use a focused discovery set, ordered around user intent:

- `data-quality`
- `data-cleaning`
- `data-validation`
- `data-profiling`
- `data-observability`
- `data-engineering`
- `data-repair`
- `quality-gates`
- `data-drift`
- `duckdb`
- `python`
- `local-first`
- `mcp`
- `mcp-server`

Add `llm` or `ai-copilot` only if AI-agent discovery is a current acquisition priority. The core product should not look dependent on AI because detection and scoring are deterministic.

## GitHub features

Recommended state:

- Issues: enabled.
- Discussions: enabled.
- Projects: optional; only keep if actively used for public planning.
- Wiki: optional; prefer versioned repository docs unless the wiki has a clear use case.
- Sponsorship/Funding: add only when there is a real support path.

## Social preview

Create a 1280×640 social preview with one message, not a feature wall:

**DataSentry**

**Find bad data before your users do.**

Supporting line:

`Find → Explain → Fix safely → Verify`

Use the same logo and typography as the project site. Avoid badges, architecture diagrams, screenshots with unreadable text, or long feature lists.

## Pinned repository / profile

If DataSentry is a priority open-source project, pin it on the maintainer profile. Keep the repository description, social preview, README hero, and launch copy on the same positioning language.

## Release coordination

The growth branch introduces the `datasentry-demo` console entry point. Publish the next `datasentry-ai` release before presenting that command as already available on PyPI. Until then, the README's primary quick start intentionally uses the currently published `datasentry scan` path.
