# Great Expectations vs DataSentry: two ways to care about data quality

I've used Great Expectations for years and I still reach for it in the right situations. But when I started DataSentry, I built it against a specific frustration with the GE workflow, and I think the differences are worth spelling out — because they're philosophical, not just feature-level.

Full disclosure up front: I'm the author of DataSentry. This is not a "GE is bad" post. GE has a massive ecosystem, a decade of battle testing, and an enterprise story DataSentry will likely never need to replicate. What follows is an honest account of where each tool fits.

## The core difference: assertions vs detection

Great Expectations is an **assertion framework**. You write Expectations — "column `order_total` should be above zero", "table should have between 10k and 1M rows" — and it validates your data against them, run by run. Its power comes from the community's huge library of pre-built expectations and the DataDocs system that turns results into browsable HTML documentation.

DataSentry is a **detection framework**. There are no upfront expectations to write: you point it at data and it runs 39 statistical detectors — missingness, date patterns, duplicates, outliers — and reports what it *finds*, with evidence. You don't need to know your data is broken before you ask it to check. That's the workflow difference in one sentence:

- GE answers "is my data meeting the rules I already defined?"
- DataSentry answers "what is wrong with this data, right now?"

Both questions are legitimate. They're just asked at different moments. GE is strongest when your rules are stable and you're guarding a pipeline. DataSentry is strongest at the start — when you don't yet know what to assert because you haven't looked at the data closely.

## What a first session looks like

Here's the honest GE experience for a new table: you need a Data Context, a datasource, a suite of expectations, a checkpoint. You can generate "expectations from data" with `suite edit` or the profiler, but the output is a config file you then own and maintain. The return on investment is real — the checkpoint runs in CI and catches regressions — but the setup is a project before it's a check.

DataSentry's first session:

```bash
pip install datasentry-ai
datasentry scan orders.csv
```

That's it. Thirty-nine detectors run, issues get fused, a six-dimension quality score comes out, and every issue carries samples, ratios, and confidence — so you can judge the findings without writing a single assertion first. In GE terms: the profiler ran, and it kept the evidence instead of throwing it away.

There's a bridge between the two worlds that I care about a lot: DataSentry's contract DSL can export to Great Expectations format (`datasentry contract export --as ge`). If you've let DataSentry find the issues, you can turn those findings into GE expectations for the pipeline you already run. Detection informs assertion.

## Validation vs repair

GE validates; it doesn't fix. There are checkpoint actions and some automated flows, but the core model is "detect and alert" — which is the right posture for many teams.

DataSentry goes further down the "what do we do about it" path, and I built it with a hard rule: **nothing modifies data without human approval**. The repair loop is propose → preview (rules re-run, before/after) → apply (fingerprinted copy + rollback artifact) → rollback. AI can *suggest* repairs, but it never writes. For a local CSV, that's a genuinely useful loop — one command shows exactly which 14 rows would change before anything touches the file.

## The AI difference, stated plainly

GE has been adding AI-assisted workflows to its platform. DataSentry went a different route: a deterministic detection core that works with zero LLM configured, plus an optional LLM layer for natural-language rules and repair suggestions, plus an MCP server with 20 tools so Claude (or any MCP-capable agent) can run real checks with real evidence.

The philosophical position: detection must not depend on a model that can hallucinate. Statistics detect; the LLM translates and suggests; a human approves. GE's strength is the opposite axis — governance, collaboration, enterprise process around quality.

## How to choose

Pick Great Expectations when:
- You have a pipeline with known, stable rules and you want regression guards
- You need DataDocs-style documentation your team will actually read
- You're already in the GX ecosystem or need enterprise support

Pick DataSentry when:
- You have data you haven't inspected yet and don't know what to assert
- You want local-first: CSV/Parquet/DuckDB/SQLite on your laptop, no platform, no data leaving the machine
- You want agents to run quality checks through MCP, with the same human approval gate
- You want the repair loop with rollback, not just alerts

And when your rules are finally known — use the contract export to take DataSentry's findings into GE. They complement each other at exactly the point where most teams get stuck: the transition from "I don't know what's wrong" to "I will guard against regressions."

DataSentry is Apache-2.0: https://github.com/Jackxiaozhiren/datasentry
