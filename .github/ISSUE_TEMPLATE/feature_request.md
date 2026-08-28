---
name: Feature request / detector idea
about: Describe the workflow, data shape, and evidence you want DataSentry to support
title: "[feature] "
labels: enhancement
---

## Problem / workflow

What are you trying to accomplish, and where does the current DataSentry workflow stop being useful? A concrete scenario is more helpful than a feature name.

## Example data shape

If the request involves detection, validation, repair, or a connector, provide a minimal synthetic example when possible.

- input type / source:
- relevant columns / values:
- what “bad” looks like:
- what “good” looks like:
- approximate scale or frequency:

Do not attach real customer data, credentials, or secrets.

## Desired outcome

What should DataSentry produce or enable?

Examples:

- a new evidence-backed issue type;
- a safer repair proposal;
- a connector or integration;
- a report/export improvement;
- a CLI/Web/MCP workflow improvement.

## Evidence / acceptance criteria

How would we know the feature works? For a detector, include false-positive boundaries where possible. For performance requests, include a reproducible workload rather than only a target number.

## Alternatives / workarounds

What do you do today instead?

## Design constraints

DataSentry intentionally keeps deterministic detection independent from LLM availability, and repair should remain previewable, human-approved where state changes are involved, auditable, and reversible.

See [`ROADMAP.md`](../../ROADMAP.md) for current direction and [`CONTRIBUTING.md`](../../CONTRIBUTING.md) if you want to implement the request.
