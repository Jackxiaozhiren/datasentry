# DataSentry in GitHub Actions

DataSentry can be used as a CI quality gate without copying installation and exit-code handling into every repository.

The DataSentry repository exposes a reusable workflow:

```text
.github/workflows/datasentry-quality-gate.yml
```

It installs a pinned `datasentry-ai` release from PyPI, runs `datasentry scan ... --fail-on ...`, writes a GitHub Job Summary, uploads stdout/stderr artifacts, and preserves DataSentry's exit semantics.

## Use from another repository

Create a workflow such as `.github/workflows/data-quality.yml`:

```yaml
name: Data quality

on:
  pull_request:
    paths:
      - "data/**"
  push:
    branches: [main]
    paths:
      - "data/**"

permissions:
  contents: read

jobs:
  datasentry:
    uses: Jackxiaozhiren/datasentry/.github/workflows/datasentry-quality-gate.yml@main
    with:
      path: data/orders.csv
      fail_on: high
      datasentry_version: "1.0.1"
      python_version: "3.12"
```

For long-lived production workflows, pin the reusable workflow to a release tag or commit SHA once the workflow is included in a tagged DataSentry release. `@main` is convenient for early adoption but is not an immutable dependency.

## Inputs

| Input | Required | Default | Purpose |
|---|---:|---|---|
| `path` | yes | — | file or local database path to scan |
| `fail_on` | no | `high` | minimum severity that fails the gate |
| `datasentry_version` | no | `1.0.1` | PyPI version installed by the workflow |
| `python_version` | no | `3.12` | Python runtime for the scan |

The first version intentionally keeps the surface small. Database DSNs, cloud credentials, custom contracts, and arbitrary extra CLI flags are not accepted as free-form workflow inputs because an overly generic shell surface is harder to secure and reason about.

## What appears in the run

The reusable workflow produces:

- the normal DataSentry scan logs;
- a **DataSentry quality gate** Job Summary;
- quality score and issue counts when returned by the scan;
- the DataSentry process exit code;
- an artifact named `datasentry-quality-result` containing `datasentry-result.json` and `datasentry-stderr.log`.

Artifacts are retained for 14 days by default.

## Exit behavior

DataSentry's exit semantics are preserved:

| Exit | Meaning |
|---:|---|
| `0` | scan completed and gate passed |
| `1` | quality gate failed |
| `2` | configuration error |
| `3` | execution error |
| `4` | data source unavailable |

The workflow also validates the structured scan result. A Python process that merely exits with code `1` is not automatically labeled as a data-quality failure; the result must explicitly contain a failed DataSentry gate.

## Local repository smoke test

This repository contains a clean synthetic fixture at:

```text
examples/integrations/github-actions/clean-orders.csv
```

and a caller workflow:

```text
.github/workflows/datasentry-quality-gate-smoke.yml
```

Changes to the reusable workflow trigger the smoke caller so GitHub validates the actual `workflow_call` path against the released PyPI package rather than relying only on YAML inspection.

## Security model

The reusable workflow declares only:

```yaml
permissions:
  contents: read
```

The basic file-scan path does not require repository write permissions or secrets.

For database or cloud scans, prefer an explicit integration workflow where credentials are passed through GitHub Secrets to DataSentry's supported credential mechanisms. Do not encode DSNs, access tokens, or secret values directly in workflow YAML or command output.

## Why a reusable workflow first?

A reusable workflow gives DataSentry an immediately consumable GitHub-native integration while keeping the runtime in the `datasentry-ai` package.

The longer-term distribution target is a dedicated **`datasentry-action` repository** with a root `action.yml` and independent `v1` release line. A dedicated repository lets the Action be packaged, tagged, released, and listed independently. Until that repository exists and is released, do not advertise `Jackxiaozhiren/datasentry-action@v1` as a working install path.

## Future dedicated Action

The intended future user experience remains approximately:

```yaml
- uses: Jackxiaozhiren/datasentry-action@v1
  with:
    path: data/orders.csv
    fail-on: high
```

That dedicated Action should remain a thin integration layer: it should install a released DataSentry version and invoke the public CLI rather than duplicating detection or scoring logic.
