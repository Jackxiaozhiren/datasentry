# DataSentry AI

> An evidence-driven, local-first AI copilot for detecting, explaining, validating, and safely
> repairing data quality problems.
>
> 一个以统计证据为基础、以 AI 为辅助、以人工审批为保障的数据质量检测与修复平台。

**状态**：开发中（Step 10/20 — CLI/SDK 闭环完成）。本仓库按《产品设计与开发 Prompt 完整版 v2.0》
推进，一次一个实施步骤，每步执行 8 步法（设计决策 → 实现 → 测试 → 修复 → 文档 → 变更摘要）。

## 目录结构

```
├── packages/core/          # datasentry-core：领域模型 + 连接器 + 执行层 + 核心引擎
│   └── src/datasentry_core/
│       ├── models/         # Step 1：35+ Pydantic v2 领域模型（+ Step 5 检测器接口模型）
│       ├── connectors/     # Step 2：数据源连接器抽象（CSV 已实现）
│       ├── engine/         # Step 3-4：SQL 执行层（DuckDB 只读）+ Profiling engine
│       ├── detectors/      # Step 5-7：注册表 + 15 种确定性检测器 + 融合调度
│       ├── scoring/        # Step 8：评分引擎（12.8 公式，ADR-002/003）
│       └── storage/        # Step 9：元数据库（ADR-010/012）
├── src/datasentry/          # Step 10：SDK 客户端 + CLI（22-23 章）
├── docs/                   # 设计材料（一致性检查、风险识别、MVP/V1 划分、SQLite 草案、ADR）
├── tests/                  # 测试（Step 1 起逐步扩展）
├── Makefile                # make lint / type / test / check
└── pyproject.toml          # uv workspace 根
```

## 快速开始

```bash
uv sync
make lint && make type && make test
```

## 设计文档

- `docs/00-设计裁决记录-ADR.md` — 架构决策记录（已确认 11 项）
- `docs/01-设计材料-一致性检查.md` — 需求一致性检查
- `docs/02-设计材料-风险识别.md` — 过度设计/技术/隐私风险
- `docs/03-设计材料-MVP-V1-划分.md` — 功能优先级划分
- `docs/04-设计材料-SQLite-Schema-草案.md` — 元数据 Schema 草案

## 已确认的架构决策（摘要）

- 执行引擎收敛为 DuckDB 单一引擎（ADR-005）
- 质量总分 6 维度，Accuracy Proxy 归 Validity、Distribution Stability 归 Integrity（ADR-001）
- Priority Score 上限修正为 0–100（ADR-002）
- 跨表检测器与契约引擎归 V1（ADR-004）
- MVP 的 AI 范围 = Provider 抽象 + 脱敏 + 降级链（ADR-006）
- CSV 语义约定：DuckDB 路径空串→NULL、PyArrow 路径保留空串，由检测器层统一（Step 6）

## 连接器语义约定（Step 2）

- 编码解析：用户指定 `options["encoding"]` > BOM 探测 > 检测器 > utf-8 回退（短文件检测可能误判，故用户指定为权威通道）
- 读取契约：`read_batches` 为 block 粒度（`batch_size` 是内存提示，不承诺精确行数切割）；`count_rows`/`read_sample` 经只读 SQL 视图（守卫仅允许 SELECT/WITH/SHOW/DESCRIBE/EXPLAIN 单语句）
- 指纹：`full`（文件 SHA-256 + 全文内容哈希）/ `sampled`（采样行哈希）/ `metadata_only`（仅 schema 与统计），同内容同 `dataset_id` 时 `schema_hash` 恒定

## 执行层语义（Step 3，ADR-005）

- `engine/`：`SqlExecutor` 协议（Polars 引擎归 V1，实现同一协议）＋ `DuckDBExecutor`（位置参数 `?`/具名参数 `$name`，统一返回 Arrow 表）
- 安全模型：应用层 `sql_guard` 只读白名单为唯一控制点（DuckDB 的 `enable_external_access=false` 会连带禁用 read_csv_auto 文件访问，故不做连接级沙箱）；连接器视图注册走内部受信路径 `execute_setup`/`register`

## Profiling 语义（Step 4）

- `Profiler`：单次聚合查询完成全部列统计（数值列 min/max/mean/std/q25/median/q75，字符串列 min/max，其余类型仅 min/max），列名/别名均做引号转义
- 约定：空字符串经 duckdb 视图折叠为 NULL（null 统计含空串）；`unique_ratio = distinct / 非空值`；`top_categories` 限 distinct 2~1000 的列取前 10；`examples` 刻意留空（脱敏设施在 Step 15 之后提供）；画像预算 1e6 行 < 60s 由 Step 20 基准验证

## 首批确定性检测器（Step 6，15 种）

| 类别 | 检测器 |
|------|--------|
| 缺失（11.3） | excessive_null_rate（阈值 0.05，>0.3→HIGH）、suspicious_missing_token（N/A/null/- 等 11 种标记，>0.005） |
| 唯一（11.4） | uniqueness_violation（GROUP BY 重复值，前 20 例证据） |
| 类别（11.6） | suspicious_placeholder（test/xxx/foo 等 7 种，匹配即报）、rare_category（频数<5 且占比<0.001）、category_explosion（unique_ratio>0.9 且列名非标识符） |
| 文本（11.7） | leading_or_trailing_whitespace、repeated_whitespace、hidden_control_character、unusual_length（>1024）、invalid_email（仅列名含 email 特征） |
| 数值（11.5） | iqr_outlier（k=1.5/3.0）、modified_zscore（MAD-z>3.5）、tail_probability（<0） |
| 注入 | suspicious_formula_injection（= + - @ tab CR 前缀） |

约定：全部 SQL pushdown 单表实现；`supports()` 按列类型/列名特征收窄适用面；阈值固定为规格默认值，可配置化经 YAML 契约（Step 13）接入；证据统一结构化（STATISTICAL_MEASURE/PATTERN_MATCH/DUPLICATE_MATCH）；检测器类属性即注册元数据（ClassVar 声明，Protocol 一致）。

## 证据融合与调度（Step 7）

- `engine/fusion.py`：`EvidenceFusionEngine.fuse(candidates, scan_run_id, row_count)` 按 (dataset_id, table, columns_set, issue_family) 聚类；evidence 全部保留（provenance 可追溯）；行级 `affected_row_ids` 并集（仅当候选携带行级证据），否则列级取 max；`confidence = 1 − Π(1−cᵢ)`（C-17）；severity/false_positive_risk 取簇内最高；false_positive_risk 浮点→RiskLevel（<0.3/0.6 分档）
- issue_family 归一化：iqr_outlier/modified_zscore/tail_probability→numeric_outlier；excessive_null_rate/suspicious_missing_token→missingness；uniqueness_violation→uniqueness；suspicious_placeholder/rare_category/category_explosion→categorical_anomaly；文本/格式 5 种+formula→string_format；未知 issue_type 原样通过
- `detectors/runner.py`：`ScanRunner`（注册表 + ScanConfig.detectors 白名单过滤）逐个执行 → `DetectorRun`（completed/skipped/failed，含时长与候选数，单检测器失败不中断扫描）→ 融合为 Issue
- 占位待 Step 8：`priority_score=0.0`、affected_ratio 由调度层传 row_count 计算

## 评分引擎（Step 8，12.8 + ADR-002）

- `scoring/weights.py`：`SEVERITY_WEIGHTS`/`CRITICALITY_WEIGHTS` 唯一来源（ADR-003，从 enums.py 迁入）；`criticality_norm` = (w−0.6)/1.0 ∈ [0,1]
- `scoring/engine.py`：Priority Score = 10×severity + 25×confidence + 15×affected_scope + 10×criticality + 5×reproducibility + 15×agreement + 10×novelty + 10×repairability；affected_scope=min(1, ratio/0.05)；agreement=min(1, n_detectors/3)；clamp [0,100]；`ScoreBreakdown` 供 UI 条形分解（12.8）
- MVP 项默认值（SDK 可覆盖）：criticality=NORMAL（语义推断 V1）、reproducibility=1.0（确定性检测器）、novelty=1.0（无历史）、repairability=0.5（中性，修复引擎 Step 15+）
- `ScanRunner` 融合后自动评分（`ScoringEngine.apply` 返回副本，不修改原 Issue）；C-02 完整算例已入单测（85.0 精确复算）

## 元数据存储（Step 9，ADR-010/012）

- 布局（ADR-010 二元化）：项目数据 → `<workspace>/.datasentry/metadata.db`；全局配置/缓存 → 平台数据目录 `datasentry/`（macOS: `~/Library/Application Support/datasentry/`，`DATASENTRY_HOME` 可覆盖）
- `storage/schema.py`：docs/04 草案 20 表 DDL 冻结（MVP 占位表建表无写路径）+ `SCHEMA_VERSION`（PRAGMA user_version）幂等迁移，Alembic 归 V1（ADR-012）
- `storage/store.py`：`MetadataStore`（WAL + foreign_keys ON + 单连接写锁）——`save_scan` 单事务落 scan_runs/detector_runs/issues/evidence；`list_scan_runs`/`get_scan_run`/`get_detector_runs`/`get_issues`；数据集自动注册（local 项目占位）；级联删除由 DB 外键保证
- `detectors/runner.py` 新增 `run_scan()`：完整扫描入口，组装 `ScanRun`（fingerprint/ReproducibilityInfo/issues_count/任一检测器失败→status=failed）
- ADR-012 偏差记录：scan_runs.fingerprint 冗余 JSON 列（草案经 dataset_versions 关联，MVP 无版本写路径）

## CLI / SDK 闭环（Step 10，22-23 章）

- `src/datasentry/`：SDK 客户端 + CLI（`datasentry` 命令已注册到 PATH，`uv sync` 安装）
- SDK（23.1）：`DataSentry(project=...)` 工作区门面 —— `scan_file`（导入→扫描→评分→落库）、`list_issues`（--severity 过滤）、`get_scan`/`get_detector_runs`、`export_report`；构造即确保 `.datasentry/` 目录与 `.gitignore` 条目（ADR-010）
- CLI（22.1）：`init` / `scan` / `issues list|show` / `report export` / `contract validate`；全局 `--project` / `--format text|json` / `--seed` / `--version`
- JSON envelope：`{"ok", "command", "data", "warnings", "llm_usage"}`；退出码 0 成功 / 2 配置错误 / 3 执行错误 / 4 数据源不可用（1 保留质量门禁）
- 检测器类型收窄：字符串类检测器（missing token/placeholder 等）经 `supports()` + `string_columns()` 限定，数值列不再触发 `trim()` Binder 错误
