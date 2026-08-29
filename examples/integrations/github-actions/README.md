# GitHub Actions quality gate

Use DataSentry to stop a pull request or release when a dataset contains high-severity quality issues, while still uploading a human-readable report for review.

Copy [`datasentry-quality-gate.yml`](datasentry-quality-gate.yml) to `.github/workflows/data-quality.yml` and change the dataset path:

```yaml
- name: Scan data and enforce quality gate
  run: datasentry scan data/orders.csv --fail-on high
```

The example deliberately keeps repair out of CI. Automated pipelines should detect, explain, and gate; state-changing repairs should stay reviewable and reversible.

## What the workflow does

1. installs Python and `datasentry-ai`;
2. scans the selected dataset with deterministic detectors;
3. exits non-zero if the configured severity gate fails;
4. exports the latest scan as an HTML report even when the gate fails;
5. uploads the report as a workflow artifact.

## Other report formats

DataSentry can also export JSON, Markdown, JUnit, and SARIF:

```bash
datasentry report export latest --as junit --output datasentry-junit.xml
datasentry report export latest --as sarif --output datasentry.sarif.json
```

Use JUnit when your CI surface understands test reports and SARIF when you want to feed findings into compatible code-scanning tooling.
