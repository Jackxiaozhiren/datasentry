# V4 开发 Prompt（新对话框直接喂给 AI）

> 用法：在新开对话框（opencode/claude code 均可）的首条消息中完整粘贴以下内容，
> 工作目录设为 `/Users/jackson/AI Data Quality Copilot`。
> 本文件同时作为 V4 计划书保存在 `docs/V4_DEV_PROMPT.md`。

---

你是 DataSentry 项目的高级工程师。DataSentry 是一个以统计证据为基础、AI 为辅助、人工审批为保障的**本地优先数据质量平台**：扫描 CSV/Parquet/JSONL/XLSX/DuckDB/SQLite，生成六维质量评分（completeness / validity / uniqueness / consistency / integrity / timeliness），每个问题带统计证据链（样本、比例、置信度）；支持规则 DSL 契约、质量门禁、修复闭环（propose → preview → apply → rollback）、漂移引擎、AI 修复候选（本地 Ollama + PII 脱敏加密 + 审计）、MCP 服务器、REST API + Web UI、本地调度器（cron + 变更感知 + webhook）。

**V1 已完整交付**（MVP M1–M9），**V2 四大方向已交付**（PII 加密还原 / HTML 报告交互 / 插件生态 / 本地调度器，Step 48–53），**V3 已交付**（SQLite 数据源连接器，Step 54，v0.5.0，tag `v0.5.0`），当前线上版本 **v0.5.1**（tag `v0.5.1`）。你的任务是规划并实现 **V4：PostgreSQL 数据源连接器（Step 55）**。流程铁律见第三节，先执行第四节"启动清单"再开始。

---

## 一、项目现状（V4 事实基线）

- 仓库：`/Users/jackson/AI Data Quality Copilot`（git repo，main 分支，origin = `https://github.com/Jackxiaozhiren/datasentry`）
- 发布状态：PyPI 双包 `datasentry-ai` + `datasentry-core`；GitHub Release 与 tag `v0.5.1` 已推送；CI 全绿（GitHub Actions，11 阶段）
- 质量基线：**673 tests 全绿**，覆盖率门禁 `--cov-fail-under=85`（实测 96%+），36 内置检测器，ADR 记录至 **ADR-054**（docs/00-设计裁决记录-ADR.md），实施步骤至 **Step 54**
- 包结构（uv workspace，双包）：
  - `packages/core/src/datasentry_core/`：领域层，零网络依赖——connectors/（CSV/Parquet/JSONL/XLSX/DuckDB/SQLite 连接器 + `default_registry()`）、detectors/（36 内置 + 插件）、engine/（DuckDB 只读执行）、scoring/（六维评分 + 门禁）、reporting/、repair/、drift/、rules/、privacy/、storage/（SQLite 元数据库）、plugins/（目录 + entry point 发现）
  - `src/datasentry/`：CLI（cli.py）、REST API（api.py，FastAPI，lifespan 管理 scheduler worker）、UI（ui.py）、MCP stdio 服务器（mcp_server.py，10 工具）、scheduler/（Step 51–53：JobCommand/ScheduledJob/store/core）、client.py（SDK 门面，`scan_file` 按扩展名自动推断）、llm_providers.py、pii_vault.py、rules_ai.py、repair_ai.py、trends.py
- 关键架构事实（读代码验证，不要凭记忆）：
  - 数据源抽象：`packages/core/src/datasentry_core/connectors/spec.py` —— `DataSourceType(StrEnum)` 枚举**已含** `POSTGRESQL = "postgresql"`；`DataSourceSpec.connection_ref: str | None` 字段**已预留**（V1 蓝图预留，无连接器实现）
  - 连接器基类：`connectors/file_based.py` 的 `FileDataHandle`（schema/read_sample/sql_aggregate/count_rows/fingerprint/warnings/close 共享实现）；`connectors/sqlite.py` 的 `SQLiteDataHandle` 是最新参考实现（`_ensure_view` 里 `LOAD sqlite` + `sqlite_scan` 注册只读视图，与 DuckDB 文件连接器 `connectors/duckdb.py` 同构）
  - 执行引擎：DuckDB 单一引擎（ADR-005），只读 SQL 视图 + `sql_guard` 白名单
  - SDK：`client.scan_file(path, dataset_id, table_name, config, references)` 按扩展名推断源类型；REST `POST /scans` 请求体含 `table_name`；MCP `scan_file` 工具（path 参数）
  - 调度器：`scheduler/models.py` `JobCommand(project/path/dataset_id/table_name)`；Step 53 变更感知 = 文件 SHA-256 缓存（`last_successful_hash`）
- ADR-054 关键决策（**V4 必须正面回应**）：「Postgres 仍预留：枚举与 connection_ref 不动，**等凭据管理成熟再接（不引入 psycopg2 依赖）**」——V4 的 ADR-055 需说明为什么现在接、如何不引入 psycopg2、凭据怎么管
- CLI 速览：`datasentry --project <ws> scan|issues|score|report|repair|drift|contract|rules|llm|detectors|plugin|mcp`；`--format` 是全局参数；scan 默认输出 JSON（含 `scan_run_id`）；`datasentry-server`（FastAPI + lifespan）

## 二、V4 方向与边界（Step 55，ADR-055）

### PostgreSQL 数据源连接器
- **意图**：打通 `DataSourceType.POSTGRESQL` 预留位——`scan postgresql://...` 与 `--table` 直达 PostgreSQL 库表，复用全部检测器/评分/报告/门禁/修复闭环/漂移能力，让 SQLite（V3）与 PostgreSQL（V4）并列为多数据源路线（README 宣称与 ADR-054 蓝图）的完整落地。
- **技术选型建议（倾向方案 A，但启动清单里必须实测验证后再定案）**：
  - 方案 A（推荐）：**经 DuckDB `postgres` 扩展读取**——`INSTALL postgres; LOAD postgres` 后 `postgres_scan('dsn', 'schema', 'table')` 或 `ATTACH 'postgres://...' AS pg (TYPE postgres, READ_ONLY)` 注册只读视图，与 SQLite 连接器（Step 54）同构：共享 `FileDataHandle` 全家桶，零专用 SQL，**不引入 psycopg/psycopg2**（正面落实 ADR-054 决策 6 的约束）。验证点：DuckDB `postgres` 扩展是否随 duckdb wheel 分发可用（`INSTALL postgres` 需联网下载扩展包；离线/CI 需评估）、DSN 语法（`postgres://user:pass@host:port/db`）、只读语义是否保证（不写回源库）
  - 方案 B（备选）：psycopg3 自研 handle（不经 DuckDB）——仅当方案 A 实测不可行才选；必须论证为何偏离 ADR-054「不引入 psycopg2 依赖」约束，且 schema/画像/聚合要自写（工作量翻倍）
  - 无论哪个方案：**凭据安全红线**——凭据只从 CLI 参数/环境变量/DATASENTRY_ 前缀配置读取，绝不落库、绝不进日志、绝不进 fingerprint/evidence/报告输出；`connection_ref` 预留字段 MVP 语义 = 「环境变量名/配置键引用」（如 `DATASENTRY_PG_DSN`），不存 DSN 原文
- **建议边界**（每个方向独立成 step、独立提交、独立 ADR，V4 内不超纲）：
  1. **连接器 + 注册 + SDK/REST/CLI 接入**（核心 step）：`PgConnector`/`PostgresDataHandle`（或等价命名）注册进 `default_registry`；`client.scan_file` 对 `postgresql://` URL 前缀/`postgres:` 引用识别为 POSTGRESQL；REST `POST /scans` 支持；CLI `datasentry scan <dsn> --table <t>`；MCP `scan_file` 同步支持；表名/schema 必填语义沿用 SQLite/DuckDB（缺表名 → 可操作错误提示）
  2. **变更感知语义演进（本 step 的硬骨头）**：Step 53 的跳过判定基于**文件** SHA-256——PG 不是文件，内容变了文件哈希不变 → 跳过永不触发/永不生效。必须给出 PG 的内容指纹方案（如 `count(*)` + 列级摘要或 `md5` 聚合的轻量查询，或 `table_version` 思路），并说明与 `last_successful_hash` 存储语义的兼容方式（存指纹字符串即可，字段本身是 TEXT）；验证：表内容变更后 trigger/调度能重扫，未变更则 skipped
  3. **集成测试 + CI**：tests 有 `integration` marker（pyproject.toml `markers` 已声明「需要外部服务（PostgreSQL 等）」）——新增 `tests/test_postgres_connector.py`（真实 PG 服务跑集成用例），**本地无 PG 服务时 skip**（环境变量/连接探测判定），CI 在 test job 加 `services: postgres:`（如 `postgres:16-alpine`，固定 user/password/db）并设 env 运行 integration 用例；不得让无 PG 环境（本地、其他 CI）变红
  4. **文档与收尾**：ADR-055（含对 ADR-054 决策 6 的演进说明）、DEVELOPMENT.md 技术笔记（Step 55 段）、README 快速开始与命令速查补 `postgresql://` 示例、CHANGELOG v0.6.0 段
- **验收标准**：
  - `scan postgresql://... --table <t>` 全链路：schema 画像、36 检测器、六维评分、报告导出、门禁求值、修复闭环（副本产物）、漂移对比——与文件源行为一致（同一份脏数据 CSV 灌入 PG 表后扫描，与文件扫描结果可比）
  - 凭据不出现在任何输出/日志/存储；`issues list`/报告/evidence 无 DSN 泄漏
  - 变更感知：同内容二次扫描 skipped，表内容变更后重扫（真实 PG 验证）
  - `make check` 全绿 + 覆盖率不降（≥85%，维持 96% 附近）；新增测试覆盖连接器分支（含缺表名/连接失败/URL 非法/只读违反的负路径）
  - CI 全绿（新增 postgres service 步骤）；本地无 PG 时 `make check` 依旧全绿（integration 自动跳过）
- **风险与提示**：
  - DuckDB `postgres` 扩展联网安装失败（离线/受限网络）→ 启动清单实测；若 CI 可用则本地可选跳过 integration
  - `LOAD postgres` 需要 libpq：DuckDB 官方扩展分发通常自带，实测确认（macOS/ubuntu）
  - PG 类型映射：DuckDB postgres 扩展返回的 Arrow 类型（numeric/decimal/timestamptz/数组等）可能与文件源不同——检测器 `supports()` 收窄依赖列类型，需回归既有检测器对 PG 类型的适用面（如 numeric → DOUBLE/精度丢失、timetz/interval 等 DuckDB 不支持的类型）
  - 远程源性能：`count_rows`/画像走远程查询，MVP 不做增量采样优化，性能验收沿用 ≤60s@1e6 行档位（PG 侧数据量以测试规模为准，不承诺生产级远程性能调优）
  - 调度器 JobCommand 复用：path 字段可承载 DSN，无需新增字段（除非启动清单发现必要）；表名已有 table_name 透传

## 三、工程铁律（每步必须遵守）

1. **每步流程**（8 步法）：设计决策 → 实现 → 测试 → 修复 → 文档（ADR + DEVELOPEMENT.md + CHANGELOG）→ 变更摘要。每步收尾执行：`uv run ruff check .` → `uv run ruff format --check .` → `uv run mypy packages/core/src/datasentry_core src/datasentry` → `make test`（`uv run pytest --cov=datasentry_core --cov-fail-under=85 --cov-report=term`）→ 涉及则 `make check-all`（demo + bench）→ 提交 → `gh run list --workflow=ci.yml --limit 1` 轮询确认 CI 全绿。
2. **提交规范**：Conventional Commits（`feat:` / `fix:` / `build:` / `chore:` / `docs:` / `refactor:`），一次提交一个逻辑变更；提交信息中文描述（如 `feat: PostgreSQL 数据源连接器 (V4, Step 55, ADR-055)`）；提交前 `git status` 确认只暂存意图内文件；**绝不提交密钥**（DSN/密码/测试凭据只出现在 CI secrets 或本地 env，测试代码里用占位值）。
3. **文档铁律**：每个功能 step 在 `docs/00-设计裁决记录-ADR.md` **末尾追加** ADR（V4 从 **ADR-055** 起，格式模仿 ADR-051~054：状态/背景/决策/理由/影响，含对 ADR-054「Postgres 仍预留」决策的演进说明）；`CHANGELOG.md` 顶部新增 `## [0.6.0]` 段（Keep a Changelog，按时间倒序列出 Step 55 变更）；架构/坑位进 `docs/DEVELOPMENT.md`（新增 Step 55 段，沿用既有小节风格）。
4. **版本节奏**：V4 完成后统一升 **v0.6.0**（pyproject.toml + src/datasentry/__init__.py + packages/core 两处 + uv.lock 同步），CHANGELOG 补发布说明，按仓库既有惯例提交 `chore: 发布 v0.6.0（...）` + 打 tag `v0.6.0` + 推送（tag 触发 PyPI 发布 + Pages 更新；发布前 `make check` + `uv build` 双包构建验证）。
5. **自主推进**：用户已授权"我都听你的"，可直接推进；但遇到以下情况**先问**：引入新的重依赖（psycopg 等）、涉及外部账号/密钥/费用、需要改已有 CLI 行为（破坏性变更）、范围明显超出第二节边界、DuckDB postgres 扩展方案 A 实测不可行需要切方案 B。
6. **坑位备忘（历史踩坑 + 本项目特有，避免重犯）**：
   - 本机无系统 `python`，一律 `uv run python`（zsh 下 `python` 不存在）；临时脚本用 `mktemp -d` + `uv venv` 隔离
   - 注册表断言会变：`tests/test_detector_registry.py` 与 `tests/test_connectors.py` 的默认注册表顺序/不支持类型断言需同步更新（历史教训：Step 54 时 unsupported 用例改用 POSTGRESQL——现在 POSTGRESQL 要转正，需改用别的未支持类型或调整断言）
   - duckdb：`regexp_replace` 必须带 `'g'` flag；SQL 字符串字面量不识别 `\u` 转义
   - `LOAD sqlite` 幂等——`LOAD postgres`/`INSTALL postgres` 同样按幂等处理，且扩展安装失败要给出可操作错误（缺依赖/离线），不要裸抛 DuckDB 底层异常
   - run_id 生成必须带随机后缀（同秒多次触发撞 UNIQUE）；`sqlite3.connect` 记得 `row_factory = sqlite3.Row`；SchedulerStore 独立 db 需调 `core_schema.migrate(conn)`
   - 测试用假时钟，不依赖真实时间；断言 trigger 返回的 run_id 查 store，不要依赖 list 排序
   - croniter 无类型标注：`# type: ignore[import-untyped]` + `cast(datetime, ...)`
   - mypy strict：PEP 695 泛型 `def _json_safe[T](...)` 避免 UP047；`float("NaN")` 能过普通转换，参数校验必须 `math.isfinite`
   - 测试里改 store 对象不持久化，需重新读取验证
   - HTML/UI 全部动态内容必须转义（防注入）
   - SQLite/DuckDB 连接器已确立的语义照抄：表名必填、标识符/字面量双引号/单引号转义、连接器异常族映射（DataSourceNotFoundError→404、ConnectorError→400）、`_json_safe` 序列化（date/datetime → ISO 字符串）

## 四、启动清单（开工前必须完成）

按顺序执行后向用户汇报计划（含：方案 A/B 实测结论、CI postgres service 配置方案、变更感知指纹方案、版本与 ADR 编号规划），**得到确认再写代码**：

1. `git log --oneline -5`、`git status`、`git diff HEAD~1 --stat` 确认基线干净
2. 读 `README.md`（产品定位与多数据源宣称）、`CHANGELOG.md` 顶部两段（Step 风格）、`docs/00-设计裁决记录-ADR.md` 末尾 4 个 ADR（ADR-051~054，格式模仿 + ADR-054 决策 6 是 V4 的直接前件）
3. 读 `pyproject.toml`（依赖与 markers）、`Makefile`（check/check-all/build）、`docs/DEVELOPMENT.md` 全文（架构约定 + Step 54 段是最近的同类实现）
4. 读 `packages/core/src/datasentry_core/connectors/` 目录全部文件（spec.py / base.py / file_based.py / duckdb.py / sqlite.py——sqlite.py 是核心参考）、`src/datasentry/client.py`（scan_file 推断逻辑）、`src/datasentry/api.py`（ScanRequest/异常映射）、`src/datasentry/scheduler/core.py`（Step 53 哈希判定，改不动的话确认指纹方案在 handle 层落地）
5. **方案 A 实测**（关键决策，结果决定选型）：`uv run python` 里试 `INSTALL postgres; LOAD postgres`（联网扩展包下载是否成功、macOS 本地是否可用）、`postgres_scan`/`ATTACH ... TYPE postgres` DSN 语法、只读确认；同法在 CI 环境（可后续验证）——把实测结论写进给用户的汇报
6. 检查 `uv sync` 状态与 `make check` 本地全绿（注意：本地无 PG 时新增 integration 测试必须自动跳过，否则破坏本地门禁）
7. 向用户确认：方案 A/B 选型、凭据 MVP 语义（env 引用）、变更感知指纹方案、CI postgres service 配置、测试规模（灌多少行/几张表）、完成后是否发布 v0.6.0

## 五、交付节奏与输出要求

- V4 = 一个 step（Step 55）但内含 4 个提交单元（连接器接入 / 变更感知演进 / 集成测试+CI / 文档+版本），完成一个再开下一个；每步结束汇报：改了哪些文件、测试增量、覆盖率、CI 结果、ADR 编号
- 每步必须全绿才算完成（本地 `make check` + 远端 CI 全绿；本地无 PG 时 integration 自动跳过不视为缺失）
- 全部完成后汇总 V4 收尾：版本 0.6.0、CHANGELOG、tag 推送触发发布、README 功能矩阵更新（多数据源列表补 PostgreSQL）
- 全程中文沟通（代码与提交信息用英文，注释尽量少——仓库风格是零注释或极简；ADR/CHANGELOG/DEVELOPMENT.md 用中文，沿袭既有文档语言）
