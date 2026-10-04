# SQLite + DataSentry

This example shows how to scan a small local SQLite database with DataSentry.

The example is fully local and uses synthetic data only.

## 1. Install

From the repository root:

```bash
python -m pip install -e .
```

DataSentry currently requires Python 3.12+.

## 2. Create the SQLite database

Run:

```bash
python examples/integrations/sqlite/setup.py
```

This creates a local SQLite database named:

```text
example.db
```

inside this example directory.

The database contains a `customers` table with a few intentionally bad rows so that DataSentry has something to discover.

No external database, credentials, or network services are required.

## 3. Scan the SQLite table

Run:

```bash
datasentry scan examples/integrations/sqlite/example.db --table customers
```

DataSentry will inspect the `customers` table and persist the scan results locally.

The synthetic data should produce inspectable data-quality issues.

## 4. Inspect the issues

List the issues found by the scan:

```bash
datasentry issues list
```

You can also filter by severity:

```bash
datasentry issues list --severity high
```

The exact findings depend on the detectors enabled by the installed DataSentry version.

## 5. Inspect the quality score

Run:

```bash
datasentry score
```

This shows the quality score calculated from the scan results.

## 6. Export an HTML report

To create a human-readable HTML report for the most recent scan:

```bash
datasentry report export latest --as html
```

By default, the report is written to:

```text
.datasentry/reports/latest.html
```

The generated HTML file can be opened locally in a browser.

## 7. What this example demonstrates

The flow is:

```text
create SQLite database
        ↓
insert synthetic data
        ↓
DataSentry scans the SQLite table
        ↓
issues are discovered
        ↓
inspect issues and quality score
        ↓
optionally export an HTML report
```

SQLite is only used as the local data source. The example does not reimplement any DataSentry detection or quality-scoring logic.

## Clean up

Remove the generated files when finished.

On macOS/Linux:

```bash
rm -f examples/integrations/sqlite/example.db
rm -rf .datasentry
```

On Windows PowerShell:

```powershell
Remove-Item examples\integrations\sqlite\example.db -ErrorAction SilentlyContinue
Remove-Item .datasentry -Recurse -Force -ErrorAction SilentlyContinue
```
