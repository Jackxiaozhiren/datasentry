# Contributing to DataSentry

Thanks for considering a contribution. DataSentry welcomes small, focused improvements as well as larger engineering changes.

You do **not** need to understand the full architecture or the ADR history before making a useful contribution.

## Good first contributions

Useful starting points include:

- documentation fixes and examples;
- minimal bug reproductions using synthetic data;
- new detectors with focused semantics and tests;
- connector or integration examples;
- CLI/TUI/Web usability improvements;
- benchmark cases and reproducibility improvements;
- translations;
- tests for uncovered edge cases.

Look for GitHub issues labeled `good first issue` or `help wanted`.

## Development setup

DataSentry currently requires Python 3.12+ for local development.

```bash
uv sync
make check
```

Common commands:

```bash
make lint       # ruff check + format check
make type       # mypy --strict
make test       # pytest + coverage gate
make check      # standard pre-PR gate (local minimum)
make bench      # performance benchmark
make demo       # reproducible demo
make build      # build distributions
```

> `make check` 是本地最低门禁。CI（`.github/workflows/ci.yml`）跑更宽的
> 内联流水线：除 `check` 三项外还有 demo smoke、1M 行 benchmark 门禁、
> CLI/API/UI/MCP smoke 与双包 wheel 隔离安装验证。过了 `make check`
> 不等于过了 CI，开 PR 前请对照 CI 步骤自查。

## Before opening a pull request

1. Keep the change focused.
2. Add or update tests for user-visible behavior.
3. Run `make check` when possible.
4. Update documentation if commands, interfaces, or behavior changed.
5. Remove secrets, credentials, and real customer data from examples and logs.
6. Add an ADR when the change alters a load-bearing architecture decision or project invariant.

If you cannot run part of the test suite, say so in the pull request and explain why.

## Merging into `main`

`main` is a protected branch. A pull request can be merged only when all four required status
checks report a passing result on its head commit:

| Required check | Source |
| --- | --- |
| `test` | `.github/workflows/ci.yml` |
| `SonarCloud Code Analysis` | SonarQube Cloud GitHub App |
| `Issue signatures vs committed baseline` | `.github/workflows/detector-drift-gate.yml` |
| `Rescan survives source removal, prune returns uncovered` | the same workflow |

These are exact context names, not job labels copied loosely from YAML. Renaming a job without
updating the protection settings leaves a PR waiting for a status that never arrives.

Two consequences are worth knowing before changing any workflow:

- **A required workflow must not use `pull_request.paths` filtering.** A filtered workflow
  creates no status at all on a PR outside its list, so GitHub waits forever rather than
  merging. The detector drift gate therefore runs on every PR; its two jobs take roughly 25s
  in parallel. The `push` side is unfiltered for the same reason: a missed path entry fails
  silently, which costs more than a redundant 25s run.
- **Branches need not be up to date with `main`** (`strict: false`). The `push` trigger
  re-checks the merge commit, so forcing every PR to rebase adds friction without adding
  coverage. Admins may bypass protection, so a broken CI job cannot lock the owner out.

A PR opened before this policy was enabled can show "Waiting for status to be reported":
its head commit predates the required checks. Updating the branch produces a new head commit
and re-runs every PR workflow. Replace `<number>` with the PR number:

```bash
sha=$(gh api repos/Jackxiaozhiren/datasentry/pulls/<number> --jq '.head.sha')
gh api -X PUT repos/Jackxiaozhiren/datasentry/pulls/<number>/update-branch \
  -f "expected_head_sha=$sha"
```

The endpoint is `PUT`, not `POST`. Passing `expected_head_sha` makes the call fail with 422
instead of racing against a concurrent push to the branch.

## Project invariants

Contributions should preserve these core design constraints unless an explicit architecture decision changes them:

- deterministic detection should not require an LLM;
- AI suggestions remain proposals rather than silent autonomous mutations;
- repair operations must not overwrite the original source file;
- repair workflows should remain previewable, auditable, verifiable, and reversible;
- secrets and DSNs should not appear in logs or persisted reports;
- performance claims should be reproducible with documented methodology.

## Adding a detector

A detector contribution should usually include:

1. implementation under `packages/core/src/datasentry_core/detectors/`;
2. clear detector metadata and supported input semantics;
3. focused unit tests, including false-positive boundaries where practical;
4. registration in the appropriate detector registry;
5. benchmark consideration if the detector can materially affect scan cost;
6. user-facing documentation when the detector adds a new class of finding.

Detector count is not a goal by itself. Prefer explainable, common failure modes with useful evidence.

## Performance-sensitive changes

Run the repository benchmark when changing connectors, scanning, detection, evidence fusion, sampling, or other hot paths:

```bash
uv run python benchmarks/bench_scan.py 1000000 42
```

See [`docs/BENCHMARKS.md`](docs/BENCHMARKS.md) for the benchmark policy and the metadata expected in published comparisons.

## Commit and PR scope

There is no requirement for external contributors to use internal historical “Step” numbering in commit messages.

Prefer conventional, descriptive messages such as:

```text
feat: add example connector
fix: prevent duplicate repair artifact names
docs: add Airflow integration guide
perf: reduce repeated schema reads
```

Maintainers may still reference ADR or historical Step identifiers when useful for long-running internal work.

## Security

Do not file exploitable vulnerability details in a public issue. Follow [`SECURITY.md`](SECURITY.md).

## Community

Please follow [`CODE_OF_CONDUCT.md`](CODE_OF_CONDUCT.md) in issues, pull requests, Discussions, and other project spaces.

## License

By contributing, you agree that your contribution may be distributed under the project's Apache-2.0 license.
