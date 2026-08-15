Show HN: DataSentry – an LLM copilot for data quality that runs 100% on your machine

I built DataSentry because the existing data quality tools (Great Expectations, Soda, etc.) share a pattern I don't like: they force you into a hosted platform or a big deployment to do something that is fundamentally a local problem. If your data is a CSV in a repo or a table in a local DuckDB file, you should not need a cluster to know it's dirty.

DataSentry scans any file or database (CSV/Parquet/JSONL/XLSX/DuckDB/SQLite/PostgreSQL/MySQL/s3/gcs/az) and produces:

- 39 statistical detectors — missingness, dates, encodings, cross-field rules, foreign keys, exact + fuzzy duplicates, Isolation Forest / LOF outliers. Every issue carries an evidence chain: samples, affected ratio, confidence. No AI guessing in detection — the core is deterministic statistics.
- A six-dimension quality score (completeness/validity/uniqueness/consistency/integrity/timeliness) with explainable weights.
- A repair loop where AI only proposes: propose → preview (rules re-run before/after) → apply (fingerprinted copy + rollback artifact) → rollback. You decide.
- Drift comparison between historical scans, CI quality gates (`scan --fail-on`), reports in JSON/MD/HTML/JUnit/SARIF, cron scheduling with a distributed worker pool, and webhooks.

The thing I think is actually new: an MCP stdio server with 20 tools, so Claude (or any MCP-capable agent) can run quality checks directly — scan, list issues, get evidence, propose repairs — with the same human-approval guarantee. Ask "are there duplicate orders in orders.csv?" and the agent runs the real detectors, not a guess.

A taste:

    pip install datasentry-ai
    datasentry scan orders.csv
    datasentry issues list --severity high

Each issue prints samples, ratio, confidence. Then `datasentry repair propose <id> --file orders.csv` shows the exact rows that would change before anything touches the file.

Local-first is a feature, not a limitation: LLM integration is optional and can point at local Ollama, so PII never leaves the machine. If you don't configure an LLM, everything still works — detection is pure statistics anyway.

Honest limitations: ~1200 tests at 94.9% coverage, but I'm a solo author and the project is young (v0.24.0). No fancy UI yet — the Web UI is server-rendered and functional, not polished. The ML outlier detectors are tuned for tabular data and will be wrong for weird distributions — that's exactly what the evidence chain is for: you can see the confidence and samples before trusting anything.

This is Apache-2.0: https://github.com/Jackxiaozhiren/datasentry
Docs: https://jackxiaozhiren.github.io/datasentry/

Feedback I'd value most: does the "detection is deterministic, AI only assists" split match how you'd want to trust a data quality tool? And what's the one detector you're missing most? I keep the detector registry as a plugin API precisely so the answer can be "yours."
