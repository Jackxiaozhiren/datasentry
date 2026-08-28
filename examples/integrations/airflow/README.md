# Airflow + DataSentry

This example treats DataSentry as a **quality task inside an existing Airflow pipeline**:

```text
produce/export data → DataSentry scan + gate → downstream work
```

It is intentionally small, local, and credential-free.

The DAG targets modern Airflow authoring conventions:

- `DAG` from `airflow.sdk`;
- `BashOperator` from `airflow.providers.standard.operators.bash`.

## Prerequisites

Use an Airflow 3 environment with the Standard Provider available, and install DataSentry into the same worker/runtime environment:

```bash
pip install datasentry-ai
```

If Airflow runs in Docker/Kubernetes, add `datasentry-ai` to the worker image rather than installing it interactively after containers start.

## DAG

Copy or point your Airflow DAG folder at:

```text
dags/datasentry_quality_gate.py
```

The demo DAG has two tasks:

```text
produce_orders
      ↓
datasentry_quality_gate
```

`produce_orders` writes a synthetic CSV under `/tmp/datasentry-airflow/`.

One row intentionally contains the impossible date:

```text
2026-02-30
```

`datasentry_quality_gate` then runs:

```bash
datasentry scan /tmp/datasentry-airflow/orders.csv --fail-on high
```

and stores the raw stdout result at:

```text
/tmp/datasentry-airflow/datasentry-result.json
```

The task exits with DataSentry's native exit code. A quality-gate failure therefore marks the Airflow task as failed and prevents normal downstream tasks from running.

## Exit semantics

| Exit | Meaning |
|---:|---|
| `0` | scan completed and gate passed |
| `1` | findings met the quality-gate severity |
| `2` | configuration error |
| `3` | execution error |
| `4` | data source unavailable |

Do not collapse these into a generic success/failure wrapper if your observability system can preserve the distinction.

## Production adaptation

The demo uses `/tmp` only to stay self-contained. In production:

1. point DataSentry at the actual file/database/table produced by the upstream task;
2. keep credentials in Airflow's secret/connection mechanism or DataSentry's supported secret/environment path;
3. retain the DataSentry JSON/HTML report in durable object storage or your normal pipeline artifact system;
4. keep the quality-gate task directly upstream of consumers that must not run on unacceptable data.

For example, a production chain might be:

```text
ingest → transform → datasentry_quality_gate → publish → notify
```

## Airflow scheduling vs DataSentry scheduling

Use **Airflow scheduling** when Airflow already orchestrates the data pipeline and DataSentry is one quality-control step among many dependencies.

Use **DataSentry's built-in scheduler** when you want lightweight recurring DataSentry scans, gates, webhooks, or worker execution without operating an external orchestrator.

Avoid scheduling the same scan independently in both systems unless duplicate execution is intentional.

## Why `BashOperator` here?

The public DataSentry CLI is the integration contract. Calling it from a standard Airflow shell task keeps this example independent of Airflow-specific Python bindings and preserves the same exit behavior used by local scripts and CI.

For larger integrations, you can wrap the DataSentry Python SDK in a custom operator, but that should add Airflow-specific value rather than duplicate the CLI.
