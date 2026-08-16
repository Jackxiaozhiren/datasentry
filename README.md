<p align="center">
  <img src="docs/assets/logo.svg" alt="DataSentry" width="140">
</p>

<h1 align="center">DataSentry</h1>

<p align="center">
  <b>Evidence-driven, local-first AI copilot for data quality.</b><br>
  Detect · Explain · Validate · Repair — with statistical evidence, AI assistance, and human approval.
</p>

<p align="center">
  <img alt="Release" src="https://img.shields.io/github/v/release/Jackxiaozhiren/datasentry">
  <img alt="PyPI" src="https://img.shields.io/pypi/v/datasentry-ai">
  <img alt="Python" src="https://img.shields.io/badge/python-3.12-blue">
  <img alt="License" src="https://img.shields.io/github/license/Jackxiaozhiren/datasentry">
  <img alt="Tests" src="https://img.shields.io/github/actions/workflow/status/Jackxiaozhiren/datasentry/ci.yml?label=CI%20checks">
  <img alt="Coverage" src="https://img.shields.io/badge/coverage-95%25-brightgreen">
  <img alt="GitHub Pages" src="https://img.shields.io/github/actions/workflow/status/Jackxiaozhiren/datasentry/pages.yml?label=Pages">
</p>

---

> **中文导读**：DataSentry 是一个以统计证据为基础、以 AI 为辅助、以人工审批为保障的本地优先数据质量平台。
> 一次扫描生成六维质量评分，每个问题带证据链；自然语言即可提出规则与修复方案，但**只有人工批准才生效**。
> 数据不出机器（LLM 可接本地 Ollama），DuckDB 执行引擎，百万行 10 秒级。
> 调度体系已就绪：cron 任务队列 → 分布式执行节点 → 多 worker 容错路由 → 并行派发。

## Try it in 3 commands

```bash
pip install datasentry-ai
datasentry scan orders.csv                      # detect → fuse → score → persist
datasentry issues list --severity high          # each issue with samples + ratios + confidence
```

<p align="center">
  <img src="docs/demo/quickstart.gif" alt="DataSentry quickstart: scan → issues → repair" width="760">
</p>

## What is DataSentry?

DataSentry scans your data (CSV / Parquet / JSONL / XLSX / DuckDB / SQLite / PostgreSQL / MySQL / cloud objects on s3:// gs:// az://) and produces:

- **39 evidence-driven detectors** — missingness, dates, encodings, cross-field rules, cross-table foreign keys, duplicates (exact + fuzzy), outlier models (Isolation Forest / LOF), and more. Every issue carries a statistical evidence chain: samples, ratios, confidence.
- **Six-dimension quality score** — completeness, validity, uniqueness, consistency, integrity, timeliness — with explainable weights and per-dimension contributions.
- **Repair loop with human approval** — propose → preview (rule re-run before/after) → apply (fingerprinted copy + rollback artifact) → rollback. AI suggests; you decide.
- **Drift engine** — compare historical scans: schema, row-count, score and issue-distribution drift.
- **Quality gates in CI** — `scan --fail-on` blocks releases by severity or score; export reports as JSON / Markdown / HTML / JUnit / SARIF.
- **LLM assistance, safely** — natural language → rule candidates with preflight simulation; PII redacted before any prompt into an encrypted vault with key rotation (`llm restore` / `rotate-key`); every call audited (`llm status`).
- **Cron scheduling** — persistent SQLite job queue: cron jobs, manual triggers, run history, webhooks, per-job quality gates and change-aware skip (no re-scan when the source is unchanged).
- **Distributed execution** — any instance runs as a worker (`datasentry worker`); a worker pool gives round-robin routing, failover, cooldown and optional health checks, plus parallel dispatch (`DATASENTRY_MAX_WORKERS`).
- **Plugin ecosystem** — `plugin.yaml` metadata, install/uninstall lifecycle, and SHA-256 integrity locks (tamper-resistant loading, `plugin test` sandbox).
- **Multiple surfaces** — CLI, REST API, server-rendered Web UI with cross-scan trends, and an MCP stdio server (20 tools) so LLM agents can use the tools directly.

<p align="center">
  <img src="docs/demo/orders-report.html.png" alt="Sample quality report" width="720">
</p>

> **Live demo report** — [orders-report.html](docs/demo/orders-report.html) (200 rows with 15 injected quality issues)

## See it work: find duplicates and outliers in 5 lines

```python
from datasentry import DataSentry

sentry = DataSentry()
run, runs, issues = sentry.scan_file("orders.csv")  # 39 detectors + six-dimension score
dupes = [i for i in issues if i.issue_type == "uniqueness"]
outliers = [i for i in issues if i.issue_type in ("numeric_outlier", "distribution_anomaly")]
print(run.id, "—", len(dupes), "duplicate", len(outliers), "outlier issues, all with evidence")
```

Every issue carries its statistical evidence chain — samples, affected ratio, and confidence —
not just a row in a log. `datasentry repair propose <issue_id>` then shows you a rule re-run
before/after so a human decides what gets applied.

## Quick start

```bash
pip install datasentry-ai     # or: uv sync (source checkout)

datasentry                     # interactive terminal UI (TUI): dashboard / scan / issues / repair
datasentry ui                  # same TUI, explicit entry

datasentry scan orders.csv               # detect → fuse → score → persist, one step
datasentry scan "a.csv, b.csv, data/*.csv"  # batch scan (comma/newline separated, globs)
datasentry issues list                   # issues by severity / dimension
datasentry score <run_id>                # six-dimension quality score (defaults to latest)
datasentry repair propose <issue_id> --file orders.csv   # fix proposal
datasentry drift latest orders           # drift between the two latest scans
datasentry-server                       # Web UI + REST API at http://localhost:8000
```

Run `datasentry` with no arguments to open the interactive terminal
UI (Textual): four tabs — a dashboard of your recent scans with
quality trends, guided scanning with live detector progress and
CSV preview, filterable/sortable issues with evidence chains, and a
repair workbench (`propose → preview → apply → rollback`, same
AI-suggests / human-approves / always-reversible semantics as the CLI).

TUI keyboard cheatsheet:

```
1 / 2 / 3 / 4     switch view: dashboard / scan / issues / repair
j / k             move up / down in the issue or scan list
Enter             select an issue row (evidence chain below)
/                 filter issues: keyword, severity:high, column:order_id,
                  type:missing, detector:… (space-separated AND)
s                 cycle sort: priority / affected / confidence
ctrl+p            command palette (scan / switch view / help / quit)
?                 help dialog with all shortcuts
r                 refresh view
q                 quit (confirmation dialog, Enter = cancel)
```

`datasentry scan` also streams live detector progress to stderr
(`scan: detector 12/39 — IQR Outlier`), so scripts can keep stdout
clean JSON while humans watch the scan run. `datasentry score`
defaults to the most recent scan (`datasentry score`).

The Web UI (`datasentry-server`, http://localhost:8000) scans with a
live progress bar, accepts multiple files per scan (comma/newline
separated or `*.csv` globs — a batch scan lands on the scan list),
and its trends page plots each quality dimension over time with a
dimension-by-dimension score table.

Every CLI command stays available for scripts and CI.

Scheduled jobs on remote workers (multi-worker pool with
failover; jobs stay in the scheduler's SQLite queue, execution is
delegated to `datasentry worker` nodes):

```bash
DATASENTRY_WORKER_TOKEN=<secret> datasentry worker --host 0.0.0.0 --port 8001   # execution node (any instance)
DATASENTRY_WORKERS="http://worker-a:8001:secret;http://worker-b:8001:secret" datasentry-server
# scheduler round-robins jobs across workers; a failing/unreachable worker is
# cooled down (60s) and the next worker takes over; unset DATASENTRY_WORKERS
# to keep running everything locally (zero migration).

# Parallel execution: default is synchronous (one job at a time);
# set a worker count to dispatch due jobs concurrently on a thread pool.
DATASENTRY_MAX_WORKERS=4 datasentry-server
```

Scan a DuckDB file (optional — any CSV/Parquet/JSONL/XLSX/SQLite works):

```bash
datasentry scan analytics.duckdb --table payments
datasentry scan analytics.db --table payments     # SQLite
```

Scan a MySQL table (via DuckDB mysql extension, no client
library; `--table` required) or a cloud file (CSV/Parquet/
JSONL over s3:// gs:// az://, credentials from process env / `secrets`):

```bash
datasentry scan "mysql://user:pass@localhost:3306/analytics" --table payments
datasentry scan s3://bucket/orders.csv            # AWS credentials from env
```

Scan a PostgreSQL table (DSN is passed on the command line /
via `DATASENTRY_PG_DSN` and is never persisted or logged):

```bash
datasentry scan "postgresql://user:pass@localhost:5432/analytics" --table payments
DATASENTRY_PG_DSN="postgresql://user:pass@localhost:5432/analytics" \
  datasentry scan postgresql:// --table payments --schema public
```

### Credentials

`connection_ref` resolution chain: process environment variable, then
`~/.config/datasentry/secrets.env` (overridable via `DATASENTRY_CONFIG_HOME`
or `XDG_CONFIG_HOME`), then `DataSourceNotFoundError`:

```bash
datasentry secrets set DATASENTRY_PG_DSN      # interactive, no echo, chmod 600
datasentry secrets list                       # key names only (audit-safe)
datasentry secrets get DATASENTRY_PG_DSN
datasentry secrets rm DATASENTRY_PG_DSN
```

The secrets file uses `KEY=VALUE` lines (env-var-shaped keys, source-able);
the directory is `0700` and the file `0600` — both enforced on read and
write. Credentials never enter scan runs, logs, reports, or webhook
payloads; all connector errors are redacted (`postgresql://***` /
`passwd=***`).

Contract-driven scanning (optional):

```bash
datasentry contract validate contract.yaml
datasentry contract export contract.yaml --as pandera   # or --as ge
datasentry scan orders.csv --contract contract.yaml     # gate + rules bound
```

## Architecture

```mermaid
flowchart LR
    subgraph Sources
        CSV[CSV / Parquet / JSONL / XLSX] --> Exec[DuckDB SQL executor]
        DDB[(.duckdb / .db files)] --> Exec
        PG[(PostgreSQL / SQLite / MySQL / cloud)] --> Exec
    end
    Exec --> Dets[39 detectors]
    Dets --> Fuse[Evidence fusion]
    Fuse --> Score[Six-dimension scoring]
    Score --> Gate[Quality gate]
    Gate --> Report[JSON / MD / HTML / JUnit / SARIF]
    Report --> UI[Web UI + trends]
    Report --> MCP[MCP stdio server]
    Report --> CLI[CLI / REST]
    subgraph Scheduling
        Q[(SQLite job queue)] --> Sched[Scheduler + worker thread]
        Sched -->|dispatch| Pool[Worker pool: round-robin + failover + parallel]
        Pool --> W1[Worker A: /rpc/execute]
        Pool --> W2[Worker B: /rpc/execute]
    end
    subgraph AI
        LLM[LLM provider: OpenAI / Ollama]
        Red[PII redaction + encrypted vault]
        Audit[llm_cache + audit]
        LLM --> Red
        Red --> Rules[NL → rule candidates]
        Rules --> Repair[AI repair candidates]
        Audit -.->|every call| Rules
    end
    Repair --> RepairEngine[Repair engine: propose → preview → apply → rollback]
```

- **Local-first**: DuckDB executes everything; LLM is optional (auto-degrades when unconfigured) and can run on local Ollama so data never leaves the machine.
- **Deterministic core**: detectors, scoring and repair are pure statistics — no AI guesswork in detection.
- **Human in the loop**: rules and repairs are proposals until you approve them; every repair is fingerprinted and rollback-able.

## Features

| Area | What you get |
|------|--------------|
| Detection | 39 detectors across 6 dimensions; SQL-pushdown single-table; plugin API (`plugins/` auto-load, SHA-256 integrity locks) |
| Scoring | 0–100 six-dimension score, severity normalization, contract criticality |
| Contracts | YAML contract DSL → validation + gate + Pandera / Great Expectations export |
| Repair | trim / normalize case / replace missing token / set null / clip values; preview re-runs rules |
| Drift | schema / row-count / score / issue-distribution signals between historical scans |
| AI | NL→rules with preflight + approval gate; AI repair candidates with locked operation surface; PII vault + key rotation |
| Scheduling | cron jobs, manual triggers, run history (pruned), webhooks, quality gates, change-aware skip — CLI / REST / MCP 三面同语义 |
| Distributed | `datasentry worker` nodes; pool routing with failover + cooldown + health checks; parallel dispatch (`DATASENTRY_MAX_WORKERS`) |
| Plugins | `plugin.yaml` metadata, install/uninstall, integrity locks, test sandbox (three-state exit codes) |
| Interfaces | CLI · REST API · Web UI (`/ui`, `/ui/trends`) · MCP stdio (20 tools) |
| Engineering | 11-stage CI, wheel build + isolated install smoke, 1e6-row benchmark gate |

## Documentation

| Doc | Content |
|-----|---------|
| [docs/DEVELOPMENT.md](docs/DEVELOPMENT.md) | Full development notes, per-step decisions and conventions |
| [docs/00-设计裁决记录-ADR.md](docs/00-设计裁决记录-ADR.md) | 110+ architecture decision records (design rationale) |
| [docs/01-一致性检查.md](docs/01-设计材料-一致性检查.md) | Spec consistency checks |
| [docs/03-MVP-V1-划分.md](docs/03-设计材料-MVP-V1-划分.md) | MVP vs V1 feature scoping |

## Blog

- [Detecting data quality issues with LLM-assisted tooling](.growth/blog-1-detect-quality-en.md) — why detection stays statistical while LLMs translate and suggest; a full walkthrough on real data. (中文版：[用 LLM 做数据质量检测，我把「检测」和「建议」分开了](.growth/blog-1-detect-quality-zh.md))
- [Great Expectations vs DataSentry: two ways to care about data quality](.growth/blog-2-ge-vs-datasentry-en.md) — assertion frameworks vs detection frameworks, and where they complement each other.

## Development

```bash
uv sync
make check          # ruff + mypy --strict + pytest with 85% coverage gate
make demo           # demo script
make bench          # 1e6-row benchmark (60s gate)
make build          # build both wheels (datasentry + datasentry_core)
```

Requirements: Python ≥ 3.12, [uv](https://docs.astral.sh/uv/). CI validates lint, types, coverage, demo, benchmark, API/UI smoke and wheel installability on every push.

## Contributing

- Report issues with the exact data shape (or a minimal CSV) and the command you ran.
- Code: add a detector → register it in `build_initial_detectors` → cover it in `tests/` → `make check`.
- Every change should reference its ADR decision; see [docs/DEVELOPMENT.md](docs/DEVELOPMENT.md) for conventions.
- Please keep the **human-in-the-loop** invariant: anything AI proposes must remain a proposal until a human approves it.

## License

Apache-2.0 — see [LICENSE](LICENSE).
