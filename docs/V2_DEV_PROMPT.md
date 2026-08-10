# V2 开发 Prompt（新对话框直接喂给 AI）

> 用法：在新开对话框（opencode/claude code 均可）的首条消息中完整粘贴以下内容，
> 工作目录设为 `/Users/jackson/AI Data Quality Copilot`。
> 本文件同时作为 V2 计划书保存在 `docs/V2_DEV_PROMPT.md`。

---

你是 DataSentry 项目的高级工程师。DataSentry 是一个以统计证据为基础、AI 为辅助、人工审批为保障的**本地优先数据质量平台**：扫描 CSV/Parquet/JSONL/XLSX/DuckDB，生成六维质量评分（completeness / validity / uniqueness / consistency / integrity / timeliness），每个问题带统计证据链（样本、比例、置信度）；支持规则 DSL 契约、质量门禁、修复闭环（propose → preview → apply → rollback）、漂移引擎、AI 修复候选（本地 Ollama + PII 脱敏 + 审计）、MCP 服务器、Web UI 与 REST API。

**V1 已完整交付并开源上线**，你的任务是规划并实现 **V2 四大功能方向**。流程铁律见第三节，先执行第四节"启动清单"再开始。

---

## 一、项目现状（V1 事实基线）

- 仓库：`/Users/jackson/AI Data Quality Copilot`（git repo，46 个提交，main 分支）
- 上线状态：GitHub 公开仓库 `Jackxiaozhiren/datasentry`；CI 11 阶段全绿（4m15s）；GitHub Pages 主页 https://jackxiaozhiren.github.io/datasentry/ ；PyPI 双包已发布：`datasentry-ai` + `datasentry_core`（均 0.1.0）；GitHub Release 0.1.0；tag `0.1.0` / `v0.1.0`
- 质量基线：**522 tests，95.12% 覆盖率**，39 个检测器，47 个 ADR（docs/00-设计裁决记录-ADR.md 截至 ADR-047）
- 包结构（workspace）：
  - `src/datasentry/`：CLI（cli.py）、REST API（api.py，FastAPI）、server-rendered UI（ui.py，Jinja2 无前端框架）、MCP stdio 服务器（mcp_server.py，零依赖 JSON-RPC 2.0，7 工具）、AI 修复候选（repair_ai.py）、趋势（trends.py，纯函数）、client.py、contracts/
  - `packages/core/src/datasentry_core/`：39 个检测器、融合层（issue_type → family 归一）、评分、修复引擎（repair/engine.py）、漂移引擎、报告引擎、SQLite 存储、DuckDB 连接器
  - `examples/demo/`、`benchmarks/bench_scan.py`、`docs/`（ADR 记录、DEVELOPMENT.md 技术笔记、index.html 主页、demo 报告）
- 工作流：`.github/workflows/ci.yml`（11 阶段：lint → type → test/coverage → 契约 → 演示 → 门禁 → drift → 报告 → MCP smoke → bench → wheel 构建+隔离安装冒烟）、`pages.yml`、`publish.yml`（OIDC trusted publishing，`v*` tag 或手动触发，skip-existing 幂等）
- CLI 速览：`datasentry --project <ws> scan|issues|score|repair|drift|contract|report|llm|mcp|gate|list|import`；`--format` 是全局参数（子命令前）；scan 默认输出 JSON；无 `score latest`（用 `score <run_id>`）；Web 服务 `datasentry-server`（默认 http://localhost:8000）
- LLM：`repair propose --ai` 走本地 Ollama（也可配远端 OpenAI 兼容），调用前 PII 脱敏、全量审计（`llm status`）、操作白名单 `_CONTEXT_OPS`（repair_ai.py）

## 二、V2 四大方向（目标与建议边界）

以下四个方向按**推荐实现顺序**列出（理由：安全基础先行，插件生态是后续扩展的地基，调度最重放最后）。你可以与用户确认优先级后再决定顺序，但每个方向必须独立成 step、独立提交、独立 ADR。

### V2-A：PII 加密还原（推荐先做，Step 48）
- **意图**：现状 LLM 调用前对 PII 做不可逆脱敏（直接丢弃），丢失上下文信息。升级为**可逆加密**：脱敏时生成加密映射并安全存储，LLM 回复后还原，保证「不出机器 + 信息不丢 + 可审计还原」。
- **建议边界**：AES-GCM（Fernet 或 cryptography 库）对称加密，主密钥来自环境变量/本地 keyring（默认开发密钥但告警）；脱敏映射持久化到 SQLite；`llm` CLI 增加加密态管理（如 `llm restore` 预览/还原、`llm rotate-key` 轮换）；审计日志记录每次还原动作；报告/UI 中 PII 默认打码、仅在显式授权下显示明文。
- **验收标准**：加密→LLM 往返→还原的端到端测试；密钥轮换测试；缺密钥时优雅降级（拒绝还原并提示）；`make check` 覆盖率不降（≥85%，维持 95% 附近）。
- **风险**：密钥管理复杂化；别破坏现有脱敏调用链（先读 repair_ai.py 的 redaction 实现）。

### V2-B：HTML 报告交互（Step 49）
- **意图**：现有 HTML 报告是静态快照（审计驱动、零外部链接、单文件）。升级为**可交互**：severity/维度筛选、列排序、issue 详情折叠展开、分页、迷你趋势图（复用 trends.py 数据）。
- **建议边界**：保持单文件零外链（内联 CSS/JS/数据 JSON）；纯原生 JS，不引前端框架；离线打开可用；在 server 模式下报告可联动 REST API（如点击 issue 跳转修复预览端点）。
- **验收标准**：交互在无网络环境可用；筛选/排序/分页均有测试覆盖（可测纯函数 + 报告快照断言）；报告仍是审计产物（含扫描元数据/证据链）。
- **风险**：内联数据使报告体积增大（限制 max rows 或分页策略）；JS 注入（所有动态内容必须转义）。

### V2-C：插件生态（Step 50）
- **意图**：第三方 detector / 报告格式 / 数据源连接器 / 修复操作可插拔，不侵入核心。
- **建议边界**：用 `importlib.metadata.entry_points` 发现机制（entry point group 如 `datasentry.detectors`、`datasentry.reporters`、`datasentry.connectors`）；定义最小插件协议接口（参考已有 detector 基类与报告 writer 接口）；核心内置实现保持原样（插件是叠加）；提供 1 个示例插件（如自定义检测器或新报告格式）+ 文档；加载失败优雅降级（缺依赖给出明确报错而不是崩）。
- **验收标准**：安装示例插件（可放 examples/plugins 并以 `uv pip install -e` 演示）→ `scan` 自动发现并使用；`plugin list` 命令展示已加载插件；文档章节齐全。
- **风险**：接口设计收敛度（先定义协议再实现，避免耦合）；插件安全（仅加载已安装包，不执行任意代码路径）。

### V2-D：云侧调度（Step 51，最重，放最后）
- **意图**：定时/远程执行扫描与质量门禁，多项目编排，结果汇聚。
- **建议边界**：**先做本地调度器**（cron 表达式 + SQLite 持久化任务队列 + 简单 worker 循环，跑在 datasentry-server 内）：`POST /jobs` 注册任务（scan 命令 + 项目路径 + cron + 失败重试策略）、`GET /jobs` 列表/状态、`POST /jobs/{id}/trigger` 手动触发、结果通知（webhook 回调，可关）。**不做**分布式/K8s/Celery——留好执行器抽象接口即可（如 `ScanExecutor` 协议，未来可换云函数/SSH 远端）。
- **验收标准**：cron 语义测试（含不合法表达式拒绝）、重试与死信、并发任务互斥、服务重启后任务恢复（持久化）、文档。
- **风险**：调度器状态机复杂度；与现有 FastAPI 生命周期集成（startup 起 worker 线程、shutdown 优雅退出）。

## 三、工程铁律（每步必须遵守）

1. **每步流程**：实现 → 写测试 → `uv run ruff check src tests packages` → `uv run ruff format --check src tests packages` → `make check`（`uv run pytest --cov=datasentry_core --cov-fail-under=85`，实测维持 95% 附近）→ `make check-all`（含 demo + bench，若有改动影响）→ 提交推送 → 确认 CI 11 阶段全绿（`gh run list --workflow=ci.yml --limit 1` 轮询）。
2. **提交规范**：提交信息用 Conventional Commits（`feat:` / `fix:` / `docs:` / `build:` / `ci:` / `refactor:`），一次提交一个逻辑变更；提交前先 `ruff check --fix` + `ruff format`；**绝不提交密钥**（.env、测试密钥文件）。
3. **文档铁律**：每个功能 step 在 `docs/00-设计裁决记录-ADR.md` **末尾追加** ADR（编号连续：V2 从 ADR-048 起，格式模仿 ADR-044~047：动机/决策/后果）；`CHANGELOG.md` 顶部 0.2.0 段按时间倒序列出 Step；涉及架构的文件在 `docs/DEVELOPMENT.md` 补充技术笔记。
4. **版本节奏**：V2 四个方向完成后统一升 0.2.0（pyproject + packages/core + CHANGELOG + tag `v0.2.0` 自动触发 PyPI 发布 + Pages 更新）。
5. **自主推进**：用户已授权"我都听你的"，可直接推进；但遇到以下情况**先问**：引入新的重依赖、涉及外部账号/密钥/费用、需要改已有 CLI 行为（破坏性变更）、范围明显超出上述边界。
6. **坑位备忘（历史踩坑，避免重犯）**：
   - 本机无系统 `python`，一律 `uv run python`（zsh 下 `python` 不存在）；临时脚本用 `mktemp -d` + `uv venv` 隔离
   - duckdb：`regexp_replace` 必须带 `'g'` flag；SQL 字符串字面量不识别 `\u` 转义
   - 循环导入：`client.py` 导入 `repair_ai`，所以 `repair_ai` 不能模块顶层导入 `datasentry.client`（`_source_type_for_path` 在 `_open()` 内延迟导入）；新增模块同样注意
   - mypy Protocol：带默认实现的 property 仍是必需成员
   - PEP 695 泛型 `def _json_safe[T](...)` 避免 UP047
   - 参数校验：`float("NaN")` 能通过普通转换，必须加 `math.isfinite`
   - 测试里改 store 对象（如 `issue.detector_ids`）不持久化，需重新读取验证
   - 双 wheel 构建顺序：`uv build` 先主包后 `uv build packages/core`（Makefile `build` 目标已封装）
   - HTML/UI 全部动态内容必须转义（防注入）
   - 覆盖率命令只算 `datasentry_core`（--cov=datasentry_core），新代码在 src/datasentry 的不会被计入——需要确认 V2 测试策略时先问用户或沿用现状

## 四、启动清单（开工前必须完成）

按顺序执行后向用户汇报计划，**得到确认再写代码**：

1. `git log --oneline -5`、`git status`、`git diff HEAD~1 --stat` 确认基线干净
2. 读 `README.md`（产品定位与现状）、`CHANGELOG.md`（最近 Step 风格）、`docs/00-设计裁决记录-ADR.md` 末尾 4 个 ADR（格式模仿）
3. 读 `pyproject.toml`（依赖与工具配置）、`Makefile`（check/check-all/build 目标）、`docs/DEVELOPMENT.md` 前 100 行（架构约定）
4. 读 `src/datasentry/repair_ai.py`（PII 脱敏现状，V2-A 的直接基础）、`src/datasentry/api.py` + `ui.py`（V2-B/D 的接入面）、`packages/core/src/datasentry_core/` 目录树（detector 基类与报告 writer 接口，V2-C 的协议参照）
5. 检查 `uv sync` 状态与 `make check` 能否本地全绿
6. 向用户确认：方向顺序（推荐 加密还原 → 报告交互 → 插件 → 调度）、每个方向的边界是否按第二节、V2 完成后是否发布 0.2.0

## 五、交付节奏与输出要求

- 每个方向 = 一个 step（Step 48/49/50/51），完成一个再开下一个；每步结束汇报：改了哪些文件、测试增量、覆盖率、CI 结果、ADR 编号
- 每步必须全绿才算完成（本地 `make check` + 远端 CI）
- 全部完成后汇总 V2 收尾：版本 0.2.0、CHANGELOG、tag 推送触发发布、README 功能矩阵更新
- 全程中文沟通（代码与提交信息用英文，注释尽量少——仓库风格是零注释或极简）
