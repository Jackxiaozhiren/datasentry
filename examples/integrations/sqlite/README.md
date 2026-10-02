# SQLite + DataSentry

The smallest end-to-end path: **build a local database → scan a table → read the findings**. No external service, no credentials, no API key, no LLM.

If you already have a SQLite file, skip to step 3 and point `--table` at your own table.

DataSentry requires Python 3.12+.

## 1. Install

```bash
python -m pip install datasentry-ai
```

From a source checkout, run `uv sync` and prefix the commands below with `uv run`.

## 2. Build the sample database

```bash
python create_database.py
```

This writes `shop.db` with a single `orders` table of 14 rows. Everything in it is synthetic. Eight rows are clean; the rest carry one flaw each, so a finding maps back to an obvious cause:

| Row | Flaw |
|---|---|
| 9 | `2026-02-30` — a date that does not exist |
| 10 | `not-an-email` in `customer_email` |
| 11 | `quantity` of `-2` |
| 12 | `unit_price` missing |
| 13 | an exact duplicate of row 1 |
| 14 | empty `country` |

Deliberate flaws rather than random corruption: the same database produces the same findings on every machine, which is what makes the output below something you can check yourself against.

## 3. Scan the table

`--table` is required for `.db` / `.sqlite` / `.duckdb` files, since one file holds many tables.

```bash
datasentry init          # creates .datasentry/ for scan history
datasentry scan shop.db --table orders
```

```json
{
  "scan_run_id": "scan_1868d5d829d0",
  "dataset_id": "shop",
  "status": "failed",
  "row_count": 14,
  "issues_count": { "info": 0, "low": 5, "medium": 9, "high": 1, "critical": 0 },
  "total_issues": 15,
  "detector_runs": 39,
  "quality_score": 94.7
}
```

`"status": "failed"` is the quality gate, not a crash — the scan ran, and the data did not pass. That exit status is what makes `datasentry scan` usable in CI.

Your `scan_run_id` will differ; everything else is reproducible.

## 4. Read the findings

```bash
datasentry issues list
```

```text
iss_1461b0ef3cd3 [medium] Numeric outlier in quantity (priority=84.0, conf=1.00, affected=2)
  columns=['quantity'] detectors=['iqr_outlier', 'percentile_outlier', 'tail_probability']
iss_7a3d7afbff32 [high] Datetime anomaly in order_date (priority=75.2, conf=0.95, affected=1)
  columns=['order_date'] detectors=['impossible_date']
iss_cb578797c46d [low] String format issue in customer_email (priority=67.8, conf=0.85, affected=1)
  columns=['customer_email'] detectors=['invalid_email']
...
```

Issues are ordered by priority, not by severity alone. The `2026-02-30` row is the only `high` finding, but three separate detectors agreeing about `quantity` puts that issue above it in the list.

For the evidence behind one issue:

```bash
datasentry issues show iss_7a3d7afbff32
```

## 5. Read the score

```bash
datasentry score
```

```text
Overall quality score: 94.7  (score_version=1)
  completeness      98.9  weight=0.363636
  validity          91.0  weight=0.363636
  uniqueness        93.9  weight=0.272727
  consistency       None  weight=-
```

A dimension reads `None` when nothing in this dataset could be scored for it — `consistency`, `integrity` and `timeliness` need a contract or a previous scan. The weights renormalize across the dimensions that *were* scored, so an absent dimension does not quietly count as a pass.

## 6. Optional: export a report

```bash
datasentry report export latest --as html
```

```json
{ "path": ".datasentry/reports/latest.html", "format": "html" }
```

`--as` also takes `json`, `markdown`, `junit` and `sarif`. `junit` is the one to point a CI runner at.

## What this example does not cover

**Repairs.** `datasentry repair` proposes changes for a person to preview and approve, and it never overwrites the source file. That is a separate step with its own review loop, deliberately kept out of a first-run example. See [`../../demo/`](../../demo/) for the full propose → preview → apply → verify cycle.

## Clean up

```bash
rm -rf shop.db .datasentry
```

## Next

- [`../dbt/`](../dbt/) — the same gate after a dbt model builds
- [`../github-actions/`](../github-actions/) — failing a pull request on severe findings
- [`../airflow/`](../airflow/) — a gate between pipeline tasks
