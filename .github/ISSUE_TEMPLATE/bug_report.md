---
name: Bug report
about: Something isn't working — include a minimal repro, please
title: "[bug] "
labels: bug
---

**Describe the bug**
A clear and concise description of what went wrong.

**Minimal repro (required)**
A small snippet or CSV that reproduces the issue. Data quality bugs are
usually data-dependent, so a concrete example is the fastest path to a fix.

```python
from datasentry import DataSentry

c = DataSentry(project="repro-ws")
run, runs, issues = c.scan_file("small.csv")  # or the CLI command you used
print(run, issues)
```

**What I ran**
Paste the exact command / snippet and its output.

**Expected behavior**
What should have happened instead?

**Environment**
- datasentry-ai version (`pip show datasentry-ai` or `datasentry --version`):
- Python version:
- OS:
- Did you configure an LLM provider (Ollama/OpenAI/...)? If yes, which: