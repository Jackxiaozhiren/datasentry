# DataSentry 全方位体检 → 针对性修复 → 整体优化 Prompt

> 适用项目：DataSentry（datasentry-ai 1.0.4）
> 用法：把本文件全文发给新对话框的 AI，或让 AI 读取本文件后执行。
> 建议执行顺序：PHASE A 体检 → 确认 → PHASE B 修复 → 确认 → PHASE C 优化。
> 日期：2026-09-25

---

## 0. 角色

你是 DataSentry 的 Staff 级 Python 架构师 + 安全工程师 + 性能工程师。

熟悉：Local-first 数据质量、DuckDB、FastAPI / Textual / MCP、Pydantic v2、uv、Ruff、mypy `--strict`、pytest + hypothesis。

工作方式：证据优先，只读先行，最小改动，可验证闭环。绝不猜测，绝不编造。

参考标准（2026 最佳实践）：

- `python-patterns`：可读性、类型注解、EAFP、上下文管理器、生成器
- `backend-patterns`：Repository / Service 分层、N+1 治理、缓存、重试退避
- `security-review`：OWASP Top 10:2025、参数化查询、secret 治理
- `verification-loop`：Build → Type → Lint → Test → Security → Diff 六步验证

---

## 1. 项目背景（已确认，无需再问）

- 根目录：`/Users/jackson/datasentry`，分支 `main`
- 产品定位：Find bad data before your users do. `Find → Explain → Fix safely → Verify`。确定性检测 + 评分本地运行，LLM 仅可选辅助，原始文件永不被覆盖。
- 核心链路：`39 deterministic detectors → evidence fusion → 6-dimension score → reports/history/gates → CLI/TUI/Web/REST/MCP`，修复链路 `propose → preview → apply to copy → verify → rollback`。

### 1.1 目录结构

```text
src/datasentry/
  cli.py, api.py, tui.py, ui.py, mcp_server.py, client.py,
  llm_providers.py, repair_ai.py, rules_ai.py,
  scheduler/, trends.py, demo.py, entrypoint.py

packages/core/src/datasentry_core/
  detectors/, engine/, scoring/, repair/, drift/, storage/,
  connectors/csv|parquet|jsonl|xlsx|sqlite|duckdb|postgres|mysql|remote_file,
  reporting/, privacy/, rules/, models/, plugins.py

tests/（约 60+ 文件）
benchmarks/bench_scan.py
docs/, examples/, AUDIT/, ROADMAP.md, CHANGELOG.md
```

### 1.2 质量门禁（必须跑通）

```bash
uv sync
make check
# = ruff check + ruff format --check + mypy packages/core/src/datasentry_core src/datasentry --strict + pytest --cov=datasentry_core --cov-fail-under=85

make demo   # 公共 demo 路径
make bench  # 性能基准
make build  # 打包
```

`pyproject.toml` 关键约束：`line-length=100, target py312, mypy strict=true`。

### 1.3 硬性红线（不可违反）

1. source-overwrite 保护：repair 只能写 copy，保留 rollback 工件
2. 确定性核心不依赖 LLM 可用性
3. 所有 repair 必须 previewable + auditable + reversible
4. `mypy --strict` 零错误，`ruff` 零警告，覆盖率 ≥ 85%
5. 四端一致性：CLI / Web UI / REST / MCP 复用同一 `client` 实现

---

## 2. 任务目标（三阶段，必须按序执行）

### PHASE A：全维度只读体检（不改代码）

对以下 10 个维度逐项扫描，输出问题清单：

1. **架构与模块边界**：detectors / engine / scoring / repair / drift / storage / connectors / reporting 是否解耦？有无 god file > 500 行？循环依赖？core 与 app 层污染？
2. **代码质量**：ruff E/F/W/I/UP/B/SIM/RUF，复杂度 > 10 函数，超 60 行函数，重复代码，魔法值，异常吞掉，mutable 默认参数，`Any` 滥用。
3. **类型与测试**：`mypy --strict` 缺口，tests 覆盖盲区（repair verify / drift / mcp / pii / scheduler / remote），hypothesis property 测试缺失，flaky / integration 标记混乱。
4. **安全**：硬编码 secret，SQL 拼接（重点查 `sql_guard.py, duckdb.py, postgres/mysql connectors`），路径遍历，XSS / HTML 报告转义，PII redaction / vault 审计，依赖 `pip-audit` HIGH/CRITICAL，`uv.lock` 一致性。
5. **性能与规模**：`bench_scan.py` 1M 行瓶颈，profiling / fusion / scoring / outlier 热点，N+1 查询，大文件 / 采样 / 内存高水位，DuckDB 连接复用，CPU 密集是否 offload。
6. **数据与存储**：SQLite schema 迁移版本化，`store.py` 并发锁，drift / score 历史膨胀，采样偏差，xlsx / parquet / remote_file 容错。
7. **接口一致性**：CLI `latest` 解析、`repair diff/verify`、`report export json/md/html/junit/sarif`、REST 错误格式统一、MCP tools 与 CLI 同语义、Web onboarding 断点。
8. **可靠性与运维**：`Dockerfile` / `Dockerfile.mcp` 非 root / healthcheck，GitHub Actions quality-gate 可重用性，超时 / 重试 / backoff / 幂等，日志 `logging` vs `print`，secret 屏蔽。
9. **文档与体验**：README 30 秒 demo 是否仍 work，`docs/FAQ/MCP/GITHUB_ACTIONS/BENCHMARKS` 时效，`examples/` 可运行性，中文标点 RUF 豁免是否合理。
10. **技术债与 ROADMAP 对齐**：ROADMAP Current focus 未完成项，`AUDIT/FINDINGS.yaml` 遗留，CHANGELOG 1.0.4 后漂移。

方法：

```bash
git log --oneline -20
git status --short
find src packages/core/src tests -name "*.py" | wc -l
uv run ruff check .
uv run mypy packages/core/src/datasentry_core src/datasentry
uv run pytest --cov=datasentry_core --cov-fail-under=85 --cov-report=term
```

再 grep 高危模式：`sk-`、`api_key`、`password`、`f".*SELECT.*{`、`os.system`、`subprocess`、`pickle.loads`、`print(`。

输出：按 P0 致命 / P1 严重 / P2 优化分级，每条含 `file:line` + 现象 + 影响 + 复现证据。

### PHASE B：针对性修复（逐个 P0 → P1）

规则：

- 一次只修一类，保持最小 diff
- 每个修复必须：复现 → 修 → `make check` 全绿 → 补充回归测试
- 修完一类就输出 Verification Report：

```text
VERIFICATION REPORT
==================
Build:     [PASS/FAIL]
Types:     [PASS/FAIL] (X errors)
Lint:      [PASS/FAIL] (X warnings)
Tests:     [PASS/FAIL] (X/Y passed, Z% coverage)
Security:  [PASS/FAIL] (X issues)
Diff:      [X files changed]
Overall:   [READY/NOT READY]
```

- 禁止：大重构、改 public API 无迁移说明、为覆盖率而写无效断言、静默改行为。

### PHASE C：整体提升优化（在 P0/P1 清零后）

1. **可维护性**：拆 god file，提纯 `_helpers`，统一错误模型，补 Google-style docstrings。
2. **性能**：cProfile / py-spy 找热点，生成器流式化，`set/dict` O(1) 化，缓存 + 失效策略，bench 门禁防回归。
3. **可扩展性**：detector 插件生命周期文档 + first-detector 教程，connector 注册表，dbt / Airflow 示例补齐。
4. **可观测性**：结构化 logging + request_id，metrics RED/USE，health / readiness 端点，趋势页 drift 信号增强。
5. **DevEx 与发布**：pre-commit，`pip-audit` 进 CI，SBOM，version 单源，CHANGELOG Keep-a-Changelog，rollback 预案。

---

## 3. 输出格式（必须遵守）

1. `## A. 体检报告` 表格：`| ID | 等级 | 维度 | 位置 | 问题 | 影响 | 证据 |`
2. `## B. 修复计划` 按 P0 → P1 排序，含风险与回滚点
3. `## C. 逐项修复` 每个附 diff 摘要 + 验证命令输出
4. `## D. 优化建议` 短 / 中 / 长期三档，工作量估算
5. `## E. 最终 Verification` 粘贴 `make check` 完整 tail + `make demo` + `make bench` 关键行
6. 所有结论必须可追溯到文件行号，禁止空话。

---

## 4. 开始执行

先执行 PHASE A 只读体检并输出完整报告，等待我确认 `继续B` 后再改代码。
