# dbt + DataSentry

This example shows where DataSentry fits **after dbt builds a model**:

```text
dbt build → DuckDB model → DataSentry automatic discovery → quality gate
```

dbt remains responsible for transformation logic, tests, and contracts you already know to write. DataSentry adds automatic issue discovery, evidence, historical quality state, and the controlled repair loop.

The example is fully local and requires no cloud account.

## 1. Install

From this directory:

```bash
python -m pip install dbt-duckdb datasentry-ai
```

DataSentry currently requires Python 3.12+.

## 2. Build the dbt model

```bash
dbt build --profiles-dir .
```

The included profile creates:

```text
warehouse.duckdb
```

and materializes the `orders` model in the `main` schema.

The model intentionally contains one impossible date:

```text
2026-02-30
```

so the example exercises a real DataSentry datetime-quality finding rather than relying on random corruption.

## 3. Scan the dbt output

```bash
datasentry scan warehouse.duckdb --table orders
```

Inspect findings:

```bash
datasentry issues list --severity high
```

## 4. Turn it into a CI gate

```bash
datasentry scan warehouse.duckdb --table orders --fail-on high
```

DataSentry uses exit code `1` when findings meet the configured gate severity, so the pipeline task fails as a **quality failure** rather than an infrastructure error.

The current demo data is intentionally invalid and is expected to exercise the high-severity datetime-anomaly path. Detector implementations can evolve, so CI should assert the DataSentry exit contract rather than parse human-readable text.

## GitHub Actions example

A minimal job can keep the raw scan result as a CI artifact:

```yaml
name: dbt data quality

on: [pull_request]

permissions:
  contents: read

jobs:
  dbt-quality:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4

      - uses: actions/setup-python@v5
        with:
          python-version: "3.12"

      - name: Install dbt + DataSentry
        run: pip install dbt-duckdb datasentry-ai==1.0.0

      - name: Build dbt models
        working-directory: examples/integrations/dbt
        run: dbt build --profiles-dir .

      - name: Scan dbt output
        id: datasentry
        working-directory: examples/integrations/dbt
        shell: bash
        run: |
          set +e
          datasentry scan warehouse.duckdb --table orders --fail-on high > datasentry-result.json
          code=$?
          set -e
          echo "exit_code=$code" >> "$GITHUB_OUTPUT"
          cat datasentry-result.json

      - name: Upload DataSentry result
        if: always()
        uses: actions/upload-artifact@v4
        with:
          name: dbt-datasentry-result
          path: examples/integrations/dbt/datasentry-result.json

      - name: Enforce quality gate
        if: always()
        run: exit "${{ steps.datasentry.outputs.exit_code }}"
```

Once the reusable DataSentry quality-gate workflow is merged, callers that already have a local file/database artifact can use that workflow instead of repeating the install/exit-code wrapper.

## When to use dbt tests vs DataSentry

Use **dbt tests/contracts** when you know the invariant you want to enforce, for example:

- `order_id` must be unique;
- `customer_id` must not be null;
- accepted values for a status column.

Use **DataSentry** when you also want to discover issues you did not enumerate up front, inspect statistical evidence, compare historical scans, or enter a safe repair/verify workflow.

They are complementary rather than mutually exclusive.

## Clean up

```bash
dbt clean --profiles-dir .
rm -f warehouse.duckdb
rm -rf .datasentry
```
