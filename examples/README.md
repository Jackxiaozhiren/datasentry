# DataSentry examples

Start with the workflow that matches the problem you are trying to solve.

| Goal | Start here | What you get |
|---|---|---|
| **See the full product loop locally** | [`demo/`](demo/) | synthetic dirty data → scan → report → safe repair → verify |
| **Explore a realistic ecommerce dataset** | [`ecommerce/`](ecommerce/) | a concrete data-quality scenario with inspectable findings |
| **Block bad data in GitHub Actions** | [`integrations/github-actions/`](integrations/github-actions/) | a copy-paste CI quality gate |
| **Add a gate to dbt** | [`integrations/dbt/`](integrations/dbt/) | scan a materialized model and fail the pipeline on severe findings |
| **Add a gate to Airflow** | [`integrations/airflow/`](integrations/airflow/) | place DataSentry between pipeline tasks |
| **Extend DataSentry** | [`plugins/`](plugins/) | plugin examples and extension patterns |
| **Give an AI agent data-quality tools** | [`../docs/MCP.md`](../docs/MCP.md) | MCP setup for VS Code and Claude Desktop |

## Fastest path

From this repository branch, run the zero-config product tour with:

```bash
uv sync
uv run datasentry demo
```

It runs locally, generates its own synthetic dirty data, and needs no API key or LLM.

After the next `datasentry-ai` release is published, installed users will be able to run the same tour with:

```bash
pip install datasentry-ai
datasentry demo
```

The existing repository shortcut remains:

```bash
make demo
```

## Integration principle

DataSentry is designed to sit at a quality boundary without taking over orchestration:

```text
source / transformation → DataSentry scan + evidence + gate → next pipeline step
```

State-changing repair remains a separate, reviewable workflow. In automated pipelines, prefer validation and reporting; keep human approval enabled for repairs.
