# awesome-mcp-servers PR 草稿（punkpeye/awesome-mcp-servers）

## README 条目（追加到 Server Implementations 列表末尾，## Frameworks 之前）

```markdown
- [Jackxiaozhiren/datasentry](https://github.com/Jackxiaozhiren/datasentry) [![Jackxiaozhiren/datasentry MCP server](https://glama.ai/mcp/servers/Jackxiaozhiren/datasentry/badges/score.svg)](https://glama.ai/mcp/servers/Jackxiaozhiren/datasentry) 🐍 🏠 🍎 🐧 - Local-first AI copilot for data quality: 39 evidence-driven detectors, six-dimension scoring, human-approved LLM-assisted repairs, drift engine, CI quality gates, and distributed workers. 20 MCP tools.
```

## PR 标题

```
Add Jackxiaozhiren/datasentry MCP server 🤖🤖🤖
```

（`🤖🤖🤖` 是仓库声明的 agent PR 快速通道标记）

## PR 描述

```markdown
Adds [DataSentry](https://github.com/Jackxiaozhiren/datasentry) — a local-first data quality copilot with an MCP stdio server (20 tools).

What the MCP server lets agents do:
- run scans over CSV/Parquet/JSONL/XLSX/DuckDB/SQLite/PostgreSQL/MySQL/s3/gcs/az data sources
- list issues with their statistical evidence chain (samples, ratios, confidence)
- propose/apply/rollback repairs — always behind a human approval gate
- create and trigger scheduled scan jobs, query drift between historical scans

Why it fits the list: Python (`pip install datasentry-ai`), local-first (works fully offline; LLM integration optional and can target local Ollama), Apache-2.0, 1200+ tests at ~95% coverage.

Format follows existing entries: repo link, glama badge, language/scope/OS emoji, one-line description.
```

## Commit message

```
Add Jackxiaozhiren/datasentry MCP server
```

## 状态与备注

- [x] 已核对 README 现有格式（badge + emoji 图例 + 描述句式）
- [x] 追加位置：Server Implementations 列表末尾（该列表按提交追加，非严格字母序）
- [ ] 等待维护者 review；agent PR 声明可快速合并
- 备选（若被拒）：提交到官方 MCP registry（registry.modelcontextprotocol.io，走 issue 流程）
