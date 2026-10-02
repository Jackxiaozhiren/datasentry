# AGENTS.md — DataSentry Agent 工作契约

> 新会话 Agent 必读。本文件指向权威源而非复述，避免双写漂移。

## 1. 六条不变量（红线）

以 `CONTRIBUTING.md`「Project invariants」六条为准：

1. 确定性检测不依赖 LLM 可用性；
2. AI 建议只是提案，不做静默自主变更；
3. 修复永不覆盖源文件（只写副本 + rollback 工件）；
4. 修复链路保持 previewable + auditable + reversible；
5. 日志与持久化报告不含 secret/DSN；
6. 性能声明可复现（方法论文档化，见 `docs/BENCHMARKS.md`）。

## 2. 质量门禁（改完必跑）

```bash
uv sync
make check   # ruff check + format --check + mypy --strict + pytest(覆盖率≥85%)
make demo    # 公共 demo 路径
make bench   # 性能基准（1M 行，见 benchmarks/bench_scan.py 预算表）
```

`pyproject.toml` 约束：`line-length=100, target py312, mypy strict=true`。

## 3. 模块地图

- `packages/core/src/datasentry_core/`：detectors / engine / scoring / repair /
  drift / storage / connectors(csv|parquet|jsonl|xlsx|sqlite|duckdb|postgres|mysql|remote_file)
  / reporting / privacy / rules / models —— 确定性核心，不引 LLM。
- `src/datasentry/`：cli / api / tui / ui / mcp_server / client / scheduler ——
  四端（CLI/Web/REST/MCP）复用同一 `client` 实现，不引入第二套领域逻辑。
- `tests/`：pytest + hypothesis；`benchmarks/bench_scan.py` 为性能预算表。

## 4. Agent 红线

- 不许跳过测试、降低覆盖率下限、新增 `type: ignore` / `noqa`；
- 一次只修一类，保持最小 diff；修 public API 须给迁移说明；
- 不许为覆盖率写无效断言，不许静默改行为；
- 安全相关改动先读 `AUDIT/FINDINGS.yaml` 对应条目（证据链在该文件）；
- 不代操作 GitHub（评论/关 issue 关 PR 需人类执行）。
