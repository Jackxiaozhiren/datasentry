# DataSentry integration examples

These examples show how DataSentry fits into existing engineering workflows without replacing the tools that already own orchestration or transformation.

| Integration | Purpose | Path |
|---|---|---|
| dbt + DuckDB | scan a materialized dbt model and fail on high-severity findings | [`dbt/`](dbt/) |
| Apache Airflow | insert a DataSentry gate between pipeline tasks | [`airflow/`](airflow/) |
| GitHub Actions | reusable CI gate for repository data artifacts | [`github-actions/`](github-actions/) |

The integration rule is deliberately simple:

```text
existing workflow → DataSentry scan/evidence/gate → existing workflow continues
```

DataSentry should remain the source of data-quality detection, scoring, repair, and exit semantics. Integration layers should stay thin rather than reimplementing the engine.

## Security

- use synthetic data in examples;
- keep DSNs/API credentials in the host platform's secret mechanism;
- do not print credentials or PII into CI/task logs;
- preserve DataSentry's human-approval boundary for state-changing repairs.
