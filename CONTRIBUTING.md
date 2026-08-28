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
make check      # standard pre-PR gate
make bench      # performance benchmark
make demo       # reproducible demo
make build      # build distributions
```

## Before opening a pull request

1. Keep the change focused.
2. Add or update tests for user-visible behavior.
3. Run `make check` when possible.
4. Update documentation if commands, interfaces, or behavior changed.
5. Remove secrets, credentials, and real customer data from examples and logs.
6. Add an ADR when the change alters a load-bearing architecture decision or project invariant.

If you cannot run part of the test suite, say so in the pull request and explain why.

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
