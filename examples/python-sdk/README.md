# DataSentry from Python

Everything the CLI does is available on the `DataSentry` client. This is the first thirty seconds of it: open a workspace, scan a file, read the score and the issues.

No credentials, no network, no LLM, no new dependencies.

DataSentry requires Python 3.12+.

## Run it

```bash
python -m pip install datasentry-ai
python make_csv.py        # writes a small synthetic orders.csv
python quickstart.py
```

From a source checkout, `uv sync` and prefix with `uv run`.

## The whole thing

```python
from datasentry import DataSentry

client = DataSentry(project=".")
try:
    scan, _detector_runs, issues = client.scan_file("orders.csv")

    score = client.quality_score(scan.id)
    print(f"quality score: {score.overall} / 100" if score else "no score")
    print(f"{scan.fingerprint.row_count} rows, {len(issues)} issues\n")

    for issue in sorted(issues, key=lambda i: -i.priority_score)[:5]:
        print(f"  [{issue.severity.value:<8}] {issue.title}")
        print(f"             {issue.affected_count} affected · {', '.join(issue.columns)}")
finally:
    client.close()
```

Output:

```text
quality score: 95.5 / 100
10 rows, 10 issues

  [medium  ] Numeric outlier in unit_price
             3 affected · unit_price
  [high    ] Datetime anomaly in order_date
             1 affected · order_date
  [medium  ] Duplicate values in customer_email
             1 affected · customer_email
  [medium  ] Duplicate values in order_date
             1 affected · order_date
  [medium  ] Duplicate values in quantity
             7 affected · quantity
```

`orders.csv` has ten rows; the last four each carry one deliberate flaw — a malformed email, `2026-02-30`, a missing price, and an exact duplicate — so every finding traces to a visible cause and the numbers above are the same on any machine.

## Four things worth knowing

**`scan_file` returns three things.** `(ScanRun, list[DetectorRun], list[Issue])`. The middle one is per-detector timing and status, useful when you are asking *why* a detector found nothing; the example discards it.

**Sort by `priority_score`, not by severity.** Priority already folds in confidence, how much of the table is affected, and how many detectors agreed. The `high` datetime issue is second here precisely because three detectors agreed about `unit_price`.

**`close()` belongs in a `finally`.** The client holds an open SQLite metadata database. Leaking it is survivable in a script and is not in a long-running process.

**`project="."` puts state in the current directory.** `.datasentry/` holds scan history, so a second run can be compared against the first. Point it anywhere; use a temporary directory if you want each run to start fresh.

## Optional: export the report

```python
report = client.export_report(scan.id)         # the JSON report as a dict
print(report["quality"]["overall"])
```

For HTML, use the CLI: `datasentry report export latest --as html`.

## Where to go next

| You want | Look at |
|---|---|
| Scan a table in a database you already have | [`../integrations/sqlite/`](../integrations/sqlite/) |
| Filter to what matters: `client.list_issues(severity_at_least="high")` | `src/datasentry/client.py` |
| Gate a pipeline on the result | [`../integrations/github-actions/`](../integrations/github-actions/) |
| The full propose → preview → apply repair loop | [`../demo/`](../demo/) |
