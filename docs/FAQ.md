# DataSentry FAQ

This page answers the questions that usually come up when evaluating DataSentry for a data-quality workflow.

## What is DataSentry for?

DataSentry is a local-first data-quality toolkit built around a complete loop:

```text
Find → Explain → Fix safely → Verify
```

It is most useful when you want to inspect data you do not fully trust yet, discover candidate quality problems, review the evidence behind each finding, and decide whether the next step should be a permanent rule, a CI gate, or a controlled repair.

## Do I need an LLM or API key?

No.

Detection, scoring, evidence generation, drift checks, CLI workflows, and the default demo can run without an LLM or external API key. AI assistance is optional and is not the source of truth for deterministic quality checks.

When AI is enabled, it can help explain findings or propose actions. State-changing repairs remain human-approved.

## Does DataSentry overwrite my source data?

The file repair workflow is intentionally conservative. The normal sequence is:

```text
propose → preview → apply to a copy → verify → rollback
```

The original source file is not overwritten by that workflow. Repairs are fingerprinted and auditable, and the repaired copy is re-scanned so regressions can be surfaced before you adopt the result.

See the [safe repair section in the README](../README.md#safe-repair-not-blind-mutation).

## How is this different from dbt data tests?

dbt data tests are assertions about dbt resources. They are excellent for expressing known invariants such as `not_null`, `unique`, `accepted_values`, relationships, or custom SQL assertions, and for repeatedly enforcing those expectations in a dbt project.

DataSentry is complementary. Its strongest use case is earlier in the loop: scan data, surface suspicious patterns with evidence, then decide which findings deserve a durable rule or quality gate.

A practical combined workflow is:

```text
DataSentry discovery → inspect evidence → encode stable expectations in dbt → enforce in CI
```

## How is this different from Great Expectations?

Great Expectations centers on Expectations: explicit, verifiable assertions that describe the state data should conform to, organized into reusable suites and validations.

DataSentry focuses on discovery plus remediation. It can be useful before you know all the expectations you want to write, and it adds a conservative repair/verify/rollback loop.

They can work together: use discovery to identify recurring failure modes, then encode the important ones as durable Expectations.

## How is this different from Soda?

Soda's broader platform includes data-quality checks, contracts, CI/CD workflows, and automated observability. Soda Core also provides local execution for checks and contract verification.

DataSentry is not positioned as a replacement for that platform. Its narrower focus is an open-source, local-first workflow that combines automatic issue discovery, evidence-backed findings, controlled reversible repair, verification, and MCP access for AI agents.

Choose based on the job you need to solve rather than a feature-count comparison.

## Is discovery just one anomaly score?

No.

DataSentry runs multiple deterministic detectors across different failure modes, including missing values, invalid formats, duplicates, inconsistent categories, referential-integrity issues, outliers, schema changes, and drift.

Findings include evidence such as affected counts or ratios, samples, detector context, and confidence rather than only a single opaque anomaly score.

## What should I do with false positives?

Treat discovery as triage, not automatic truth.

The intended workflow is to inspect evidence before acting. If a finding reflects legitimate business behavior, do not turn it into a blocking gate or repair. If it represents a stable invariant, promote it into an explicit check or contract.

For CI, start with higher-severity failures and tighten gates only after you understand the signal on your own data.

## Can I use DataSentry in CI without enabling repairs?

Yes.

The CI path can be read-only: scan data, fail on a selected severity, and export reports. You do not need to enable repair actions.

For GitHub Actions, see [DataSentry in GitHub Actions](GITHUB_ACTIONS.md).

## What data can it scan?

Current documented sources include:

- CSV, Parquet, JSONL, and XLSX;
- DuckDB and SQLite;
- PostgreSQL and MySQL;
- `s3://`, `gs://`, and `az://` objects;
- single files, batches, and globs.

Use `datasentry demo` if you want to evaluate the workflow without connecting any real dataset.

## What is the MCP server for?

The MCP server exposes the same data-quality capabilities to compatible AI clients so an agent can scan data, inspect findings and evidence, query quality history, compare drift, and work with scheduled jobs.

The safety boundary does not disappear when MCP is used: AI may propose actions, but state-changing repair workflows remain controlled and human-approved.

See [MCP setup](MCP.md).

## Is DataSentry intended to replace catalogs, lineage platforms, or every validator?

No.

Its scope is deliberately narrower: find bad data, explain why it was flagged, support safe remediation, and verify the result. Metadata catalogs, lineage systems, transformation frameworks, contract platforms, and rule-based validators solve adjacent problems and can be used alongside it.

## What is the fastest way to evaluate it?

```bash
pip install --upgrade datasentry-ai
datasentry demo
```

The demo requires no dataset, cloud account, signup, API key, or LLM.
