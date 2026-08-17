# Detect → fix → verify: the data quality loop

Data quality tools usually stop at the report. They tell you *what* is broken — a score, a
table of issues, maybe a trend line — and then you go fix it by hand in a spreadsheet.

That's where DataSentry spent its last ten releases (v0.31 → v0.40): closing the loop from
**detection to repair**, and making the repair side batchable enough to survive real data.

## The loop

```
scan ──► compare ──► propose ──► apply ──► rollback
  ▲                                        │
  └──────────── verify ────────────────────┘
```

### 1. Scan (and batch scan)

`POST /scans` scans a file; the CLI accepts comma-separated paths, newlines, and globs
(`datasentry scan "a.csv, b.csv, data/*.csv"`), and each file lands on the scan list with
its own six-dimension mini-bars. Since v0.39 every scan remembers its **source path**, so
every downstream form is prefilled — you never type a path twice.

### 2. Compare

`/ui/compare?runs=a&runs=b` diffs two runs: dimension deltas, severity shifts, column
drift, schema changes — and an issue-level diff grouped by `(issue type, columns)` into
**NEW**, **FIXED**, and **persistent** buckets.

The interesting part: every NEW group carries a one-click **propose repair** button that
ships the group's issue ids and the source path straight to the proposal engine. A
regression introduced between two scans is one click away from a fix.

### 3. Propose — and nothing is written

Batch propose takes checked issues (checkbox column on the scan detail page, select-all in
the header) and asks the rule engine for a repair per issue. The result page groups rows by
**proposed / unsupported / error** with operation, target columns, risk level, and estimated
rows touched.

The deliberate design point: **proposals never write data**. No auto-apply, no silent
mutations. The rule engine is deterministic and LLM-free, so proposals are cheap, fast, and
auditable — but the human always pulls the trigger.

### 4. Apply — copies, never overwrites

`batch-apply` runs the selected proposals. Each one writes a **repaired copy** plus a
`before` snapshot under `.datasentry/repairs/`. The source file is never overwritten. This
is the same safety contract as the per-issue workbench, extended to N issues at once.

### 5. Roll back — individually or in a batch

Every applied repair is a transaction: roll back one from the repair history page
(`/ui/repairs`), or select several applied rows on the batch-apply result page and roll
them back together. The result page reports each run as `rolled back` or `error`, and the
history page shows the full audit trail: run id, dataset, operations, rows touched,
status, timestamps.

## Why it works

- **Batch ≠ reckless.** Batch proposal is read-only; batch apply writes only copies;
  batch rollback restores snapshots. Every destructive step is explicit and reversible.
- **Diff → repair.** The compare page turns a regression between two scans into a repair
  proposal in one click, which is where quality tooling usually stops being actionable.
- **Audit by default.** Every action lands in the repair history with status transitions
  you can verify, not just trust.
- **Local-first.** No cloud, no telemetry — the whole loop runs on your files with SQLite
  underneath.

The loop is closed, and it's closed locally. Scan something messy, compare two runs, and
the NEW issues are one click from a reversible fix.