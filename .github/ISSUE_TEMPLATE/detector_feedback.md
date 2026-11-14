---
name: Detector feedback
about: Report a false positive, false negative, or confusing detector result
title: "[detector] "
labels: bug
---

Thanks for helping improve DataSentry's signal quality.

Please use synthetic or sanitized data only. Do not attach credentials, secrets, or real customer data.

## What kind of feedback is this?

- [ ] False positive — DataSentry flagged data that is valid in this context
- [ ] False negative — DataSentry missed a problem I expected it to catch
- [ ] Evidence is confusing or insufficient
- [ ] Severity/confidence feels wrong
- [ ] Other detector behavior

## Detector / finding

If known, include the detector name, issue type, severity, and confidence shown in the report.

## Why should the result be different?

Explain the domain rule or data behavior that makes the current result incorrect or misleading.

## Minimal synthetic reproduction

Provide the smallest synthetic dataset that demonstrates the behavior.

```csv
id,value
1,example
2,example
```

Then include the exact command, for example:

```bash
datasentry scan small.csv
datasentry issues list
```

## Current evidence

Paste the smallest useful issue/report excerpt with sensitive values removed.

## Expected behavior

Describe what a useful result would look like instead: no finding, a different severity, different evidence, or a finding that is currently missing.

## Environment

- DataSentry version (`datasentry --version` or `pip show datasentry-ai`):
- Python version:
- OS / architecture:
- input type (CSV/Parquet/DB/cloud/etc.):
- interface (CLI/TUI/Web/REST/MCP):

## Would this make a good regression fixture?

- [ ] Yes, this synthetic example can be committed as a test fixture
- [ ] No / not sure

If the example is safe to publish, maintaining it as a regression test is the most useful long-term outcome of detector feedback.
