---
name: Bug report
about: Something is not working — include a minimal synthetic reproduction
title: "[bug] "
labels: bug
---

> If the report involves a vulnerability, credential exposure, arbitrary file access, authorization bypass, PII-vault weakness, or remote-code execution, **do not post exploit details here**. Follow `SECURITY.md`.

## What happened?

Describe the observed behavior and why it is incorrect.

## Minimal reproduction

Data-quality bugs are often data-dependent. Please provide the smallest synthetic dataset or snippet that reproduces the problem.

```python
from datasentry import DataSentry

client = DataSentry(project="repro-ws")
run, detector_runs, issues = client.scan_file("small.csv")
print(run, issues)
```

Or paste the exact CLI command you used.

## Expected behavior

What should have happened instead?

## Evidence

Include the smallest useful evidence:

- error/traceback with secrets removed;
- issue/report excerpt;
- repair diff or verify output;
- screenshot for UI problems;
- benchmark command for performance regressions.

## Environment

- DataSentry version (`datasentry --version` or `pip show datasentry-ai`):
- Python version:
- OS / architecture:
- input type (CSV/Parquet/DB/cloud/etc.):
- interface (CLI/TUI/Web/REST/MCP):
- LLM provider configured? If yes, which provider (do not include keys):

## Additional context

Anything else needed to reproduce the issue. Do not attach credentials or real customer data.
