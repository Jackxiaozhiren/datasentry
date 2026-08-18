# Verifying the fix: four ways to prove a repair worked

The previous post ended with the repair loop closed: `scan → compare → propose → apply →
rollback`. "Closed" — but not yet *proven*. v0.46 → v0.52 (the next seven releases) added
the step every quality loop needs but most tools skip: **verification**.

A repair writes a repaired copy, never the source. That's safe, but it raises a question:
*does the repaired copy actually fix the issues, and did it break anything else?* Instead
of trusting the copy, DataSentry re-scans it and compares against the scan the repair came
from.

## The missing link: provenance

For verification you need to know *which scan a repair came from*. Repair runs now record
`source_scan_run_id` (schema v10), so every applied repair points back at its source scan.
That one field turns a snapshot into a closed loop:

```
source scan ──► repair run ──► repaired copy
     ▲                              │
     └──── verify: re-scan copy ─────┘
```

## Verify in one click (Web)

Every applied repair — on the repair history page, the batch-apply result page, or the
artifact page — has a **Verify** button. It re-scans the repaired copy and redirects to
the compare view with the original scan as reference and the post-repair scan as current.

The compare view already knew how to group issues into NEW / FIXED / persistent buckets,
and since v0.42 every FIXED group shows the linked repair (`fixed by rep_…`). Now that
link goes straight to the repair's artifact page, where you see the actual row diff:
before snapshot vs repaired copy, changed cells highlighted red/green. The loop reads like
a story: *this run was broken, this repair changed these rows, and re-scanning shows the
issues are gone.*

## Verify in CI (CLI)

`datasentry repair verify <run_id>` does the same re-scan from the terminal:

```
$ datasentry repair verify rep_4bf447097915
fixed types: string_format
persistent types: categorical_anomaly, numeric_outlier
new types: (none)
```

Exit code is the contract: `0` unless the repair introduced a regression (a `new_types`
bucket that wasn't there before). `--require-clean` is stricter — it demands *zero*
remaining issues. Both make `repair verify` a drop-in quality gate:

```bash
datasentry repair apply-batch "$SCAN" --file orders.csv --all &&
  datasentry repair verify "$LAST_RUN" --require-clean
```

One nuance worth knowing: the verify gate's default is *no regression*, not *fully clean*.
Detectors like `categorical_anomaly` fire on nearly every column, so requiring a totally
clean re-scan would make the gate useless. "Your fix landed and nothing new broke" is the
right bar for automation; "everything is clean" is a product decision, not a gate.

## Verify for agents (MCP) and scripts (REST)

Agents get `repair_verify` over MCP — same report, JSON, no browser. Scripts get
`POST /repairs/{id}/verify` for the same payload, plus `GET /repairs/{id}/diff` which
returns only the changed rows (`line`, `before`, `after`) instead of the whole file.

## Diff everywhere

The artifact page is the web side of the diff story; `datasentry repair diff <run_id>`
prints the same changes on the terminal (`line 3: name: ' alice ' -> 'alice'`), and the
REST diff endpoint feeds audit pipelines. Four surfaces, one implementation:
`client.repair_verify` and `client.repair_diff` — the Web UI, CLI, MCP server, and REST
API all call the same two functions.

## Why this matters

A fix you can't verify is a guess. The previous loop made repair reversible; this one
makes it *provable*:

- **Provenance.** Every repair knows its source scan, so "verify" is a comparison, not a
  promise.
- **A gate, not a feeling.** Exit codes and JSON reports let CI decide whether a fix
  landed — and detect regressions automatically.
- **Audit with eyes.** The artifact diff shows the exact rows changed, before and after,
  on every surface a person or an agent might be using.

The loop was closed locally. Now it's also verifiable locally — scan, fix, prove, repeat.