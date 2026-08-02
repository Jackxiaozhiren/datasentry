# DataSentry AI

> An evidence-driven, local-first AI copilot for detecting, explaining, validating, and safely
> repairing data quality problems.
>
> 一个以统计证据为基础、以 AI 为辅助、以人工审批为保障的数据质量检测与修复平台。

**状态**：开发中（Step 12/20 — 报告引擎 + 质量门禁完成）。本仓库按《产品设计与开发 Prompt 完整版 v2.0》
推进，一次一个实施步骤，每步执行 8 步法（设计决策 → 实现 → 测试 → 修复 → 文档 → 变更摘要）。

## 目录结构

```
├── packages/core/          # datasentry-core：领域模型 + 连接器 + 执行层 + 核心引擎
│   └── src/datasentry_core/
│       ├── models/         # Step 1：35+ Pydantic v2 领域模型（+ Step 5 检测器接口模型）
│       ├── connectors/     # Step 2：数据源连接器抽象（CSV 已实现）
│       ├── engine/         # Step 3-4：SQL 执行层（DuckDB 只读）+ Profiling engine
│       ├── detectors/      # Step 5-7/13：注册表 + 21 种确定性检测器 + 融合调度
│       ├── scoring/        # Step 8/11/12：Priority Score + 质量总分 + 质量门禁
│       ├── reporting/      # Step 12：报告引擎（26 章：JSON 契约 + Markdown/HTML 渲染）
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

## 首批确定性检测器（Step 6/13/14/15/16/17，36 种，M4 ≥20 达成）

| 类别 | 检测器 |
|------|--------|
| 缺失（11.3/11.4） | excessive_null_rate（阈值 0.05，>0.3→HIGH）、suspicious_missing_token（N/A/null/- 等 11 种标记，>0.005）、sudden_missingness（时间桶缺失率突变，绝对差 ≥0.2）、group_missingness（分组缺失率异常组）、conditional_missingness（A 缺失⟹B 缺失级联，覆盖率 ≥0.8）、correlated_missingness（双列缺失共现，覆盖率 ≥0.5） |
| 唯一（11.4） | uniqueness_violation（GROUP BY 重复值，前 20 例证据） |
| 类别（11.6） | suspicious_placeholder（test/xxx/foo 等 7 种，匹配即报）、rare_category（频数<5 且占比<0.001）、category_explosion（unique_ratio>0.9 且列名非标识符）、inconsistent_case（小写归一化后多形态） |
| 文本（11.7） | leading_or_trailing_whitespace、repeated_whitespace、hidden_control_character、unusual_length（>1024）、invalid_email（仅列名含 email 特征）、invalid_phone（去非数字后长度∉[7,15]）、invalid_url（scheme:// 校验）、invalid_ip（IPv4 严格校验，zip 列豁免） |
| 文本变体（11.7/11.9） | spelling_variant（去分隔符归一化后相同的不同原值，≥2 对）、fullwidth_character（全角字母数字混入）、mojibake_character（U+FFFD 编码损坏）、invalid_numeric（数值语义列中的非数值文本） |
| 数值（11.5） | iqr_outlier（k=1.5/3.0）、modified_zscore（MAD-z>3.5）、tail_probability（<0）、percentile_outlier（<P0.1 或 >P99.9）、histogram_rarity（20 等宽桶，频数<1e-5×n） |
| 日期时间（11.8） | invalid_date（格式非法）、impossible_date（日历不可能，try_strptime NULL）、future_date（> 当前+1 天）、stale_date（< 当前−365 天，birth/hire 等豁免）、mixed_date_format（多格式混用）、duplicate_timestamp（时间戳精确重复） |
| 跨字段（11.10） | cross_field_rule（安全表达式求值，start<=end / min<=max 语义对，ADR-015） |
| 注入 | suspicious_formula_injection（= + - @ tab CR 前缀） |

约定：全部 SQL pushdown 单表实现；`supports()` 按列类型/列名特征收窄适用面；阈值固定为规格默认值，可配置化经契约引擎（V1，ADR-004）接入；证据统一结构化（STATISTICAL_MEASURE/PATTERN_MATCH/DUPLICATE_MATCH）；检测器类属性即注册元数据（ClassVar 声明，Protocol 一致）。

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

## CLI / SDK 闭环（Step 10/18，22-23 章）

- `src/datasentry/`：SDK 客户端 + CLI（`datasentry` 命令已注册到 PATH，`uv sync` 安装）
- SDK（23.1）：`DataSentry(project=...)` 工作区门面 —— `scan_file`（导入→扫描→评分→落库）、`list_issues`（--severity 过滤）、`get_scan`/`get_detector_runs`、`export_report`；构造即确保 `.datasentry/` 目录与 `.gitignore` 条目（ADR-010）
- CLI（22.1）：`init` / `scan` / `issues list|show` / `report export` / `contract validate`；全局 `--project` / `--format text|json` / `--seed` / `--version`
- JSON envelope：`{"ok", "command", "data", "warnings", "llm_usage"}`；退出码 0 成功 / 2 配置错误 / 3 执行错误 / 4 数据源不可用（1 保留质量门禁）
- 检测器类型收窄：字符串类检测器（missing token/placeholder 等）经 `supports()` + `string_columns()` 限定，数值列不再触发 `trim()` Binder 错误

## 多格式文件连接器（Step 18，7.1 + ADR-019）

- `connectors/file_based.py`：`FileDataHandle` 共享基类（schema/read_sample/sql_aggregate/count_rows/fingerprint/warnings 公式注入扫描/close）；CSV 连接器保持独立（专属编码探测/分隔符嗅探，不重构）
- `connectors/parquet.py`：duckdb `read_parquet` 视图 + pyarrow `ParquetFile.iter_batches` 流式批读
- `connectors/jsonl.py`：duckdb `read_json_auto(format='newline_delimited')` 视图；read_batches 用 LIMIT/OFFSET 分页（1e6 行预算内）
- `connectors/xlsx.py`：openpyxl 读取（`data_only=False`——公式单元格返回公式文本，公式注入可检测；计算结果缓存归 V1）→ pyarrow 表注册；混合类型列自动推断失败回退全字符串（int/str 混排不炸）；sheet/header_row 可配（默认首个 sheet、第 0 行）
- `default_registry()`：CSV + Parquet + JSONL + XLSX；数据库型（SQLITE/POSTGRESQL/DUCKDB）归 V1
- SDK `scan_file()` 按扩展名自动推断：`.csv/.tsv`、`.parquet/.pq`、`.jsonl/.ndjson`、`.xlsx`；未知格式抛 FileNotFoundError（format 语义）
- 新增依赖 openpyxl（core 包）；mypy overrides 补 openpyxl（无类型存根）

## 质量总分引擎（Step 11，27 章 + ADR-013）

- `scoring/quality.py`：`QualityScoreEngine.score(issues, ran_dimensions, weights)` —— 6 维度（27.2 默认权重）→ 总分 0-100
- 公式（27.1 归一）：`dimension = 100 × (1 − Σ(weight_issue × severity_norm × affected_ratio) / max_possible)`；`severity_norm` = `SEVERITY_WEIGHTS`（ADR-003）；`max_possible` = 维度 Issue 数 × 1.6（12.4 critical 权重，ADR-013）
- ADR-001 归并：Accuracy Proxy → Validity、Distribution Stability → Integrity；字段关键度 MVP 默认 NORMAL（Contract 覆盖归 V1）
- 无检测器运行的维度 → `None`，权重重新归一化（27.1）；`dimension_contributions` 暴露各 Issue 扣分构成（27.3 可解释性）
- 分数扫描时计算并随 `ScanRun.quality_score` 落库（历史保留原权重与 score_version）；`datasentry score <run_id>` 查看、`report export` 含 `quality` 段

## 报告引擎与质量门禁（Step 12，26/22 章 + ADR-014）

- `reporting/`：`build_report()` = 26.2 规范 JSON 报告（报告头 + scan + detector_runs + issues + quality），SDK `export_report()` 与 CLI `report export --as json` 无差异消费；Markdown 表格化摘要（26.1）、HTML 自包含单文件（内嵌 CSS，8 节：Executive Summary/Quality Score 含 27.3 维度条形图与扣分悬停/Issue Breakdown/Critical Findings 等，Drift/规则/修复历史归 V1）
- 报告默认落点 `<workspace>/.datasentry/reports/<run_id>.<ext>`（ADR-010），`--output` 可覆盖；HTML 全字段转义（XSS 安全）
- `scoring/gate.py`：`QualityGateEvaluator`（22 章场景 C）——`fail_on` 精确严重度集合、`maximum_failed_rows_ratio`（受影响行比例上限，max over issues）、`maximum_issues` 按严重度上限；`require_repair_validation` MVP 不支持时显式失败
- CLI：`scan --fail-on SEV [--max-failure-ratio R]` 激活门禁，失败退出码 1；`report export RUN_ID --as json|markdown|html [--output PATH]`

## 跨字段规则检测器（Step 14，11.10 + ADR-015）

- `detectors/safe_eval.py`：安全表达式求值器（读操作子集）——AST 节点类型显式白名单 + Name 黑名单（eval/exec/open/__import__/getattr/subprocess 等 21 项）+ Call 目标白名单（8 内置函数 + 15 str 读方法）；`eval` 注入空 `__builtins__`；超时 10s（SIGALRM）；编译缓存 + 行级结果缓存（表达式 SHA-256 为键）；None 参与运算 → 「不适用」跳过
- `detectors/cross_field.py`：`cross_field_rule` 检测器——内置规则按列名语义对自动绑定（`{name}_start|begin|from <= {name}_end|finish|to`、`{name}_min|lower <= {name}_max|upper`，前缀式，仅同类型族配对）；每规则一条 Issue（CONSTRAINT_VIOLATION，行级证据前 20 行）
- 融合：`cross_field_violation` → `cross_field_constraint` family（VALIDITY）；SQL/YAML 契约 DSL 与契约引擎保持 V1（ADR-004）；`initial/common.py` 提升为 `detectors/common.py`（消除循环导入）
- 检测器计数 28（M4 ≥20 保持）

## 日期时间检测器族（Step 15，11.8 + ADR-016）

- `detectors/datetime.py`：6 个检测器（11.8 核心 P0 化）——`invalid_date`（ISO 格式校验，MEDIUM）、`impossible_date`（格式合法但日历不可能：`try_strptime(...) IS NULL`，duckdb 对非法日期抛异常故用 try_ 前缀函数，HIGH）、`future_date`（> 当前+1 天，LOW）、`stale_date`（< 当前−365 天，LOW，birth/dob/founded/hire/established/historical/history 列名豁免）、`mixed_date_format`（格式类 iso/slash/dot/compact/other 多类混用，占比>0.02 且 ≥2 类，LOW）、`duplicate_timestamp`（精确重复时间戳，Σ(c−1)，MEDIUM）
- 适用范围：`_TEMPORAL_TYPES = {DATE, TIMESTAMP, TIMESTAMPTZ}`（TIME 排除，避免与日期比较的类型错误）+ 字符串日期提示列（列名含 date/dob/birth，**排除** timestamp/_at——created_at 不判格式）；future/stale 仅日期列（可比较）；mixed/duplicate 仅字符串列
- 边界：duckdb CSV 推断会把可统一解析的混合格式列提升为 DATE（此时 mixed_date_format 不触发，由 invalid_date 覆盖其余）；`strptime` 对非法日期抛 ConversionException，检测器统一用 `try_strptime` 返回 NULL
- 融合：6 个 issue_type → `datetime_anomaly` family（VALIDITY）；`common.py` 新增 `datetime_columns()`/`quote_re()` 供跨检测器复用（textual.py 本地 quote_re 已删除）
- 检测器计数 22 → 28（M4 ≥20 保持）

## 缺失模式检测器族（Step 16，11.4 + ADR-017）

- `detectors/missingness.py`：4 个检测器，全部 SQL pushdown 单/双列聚合、列级统计证据——
  `sudden_missingness`（时间桶缺失率突变：桶缺失率 ≥ max(0.2, 整体+0.2) 且桶样本 ≥10，桶粒度日→月→年自适应，MEDIUM）、`group_missingness`（按类别列分组后目标列缺失率异常组，同阈值，MEDIUM）、`conditional_missingness`（定向级联：A 缺失行中 B 缺失率 ≥0.8 且 A 缺失样本 ≥10，MEDIUM）、`correlated_missingness`（对称共现：双列同时缺失 ≥5 行且覆盖率 ≥0.5，LOW）
- 阈值修正记录：相对 3× 在整体缺失率 >1/3 时永不触发，改用绝对差判定（ADR-017）；`min(a_null,b_null)` 为共现率分母（无 0 除）
- 防组合爆炸预算：列对 ≤50（缺失率 ≥0.02 的列取前 15）、分组列 ≤10 × 目标列 ≤5、目标列 ≤5 × 时间列 ≤3
- 融合：4 个 issue_type → `missingness` family（COMPLETENESS）；检测器计数 28 → 32（M4 ≥20 保持）

## 表示变体与编码检测器（Step 17，11.7/11.9 余项 + ADR-018）

- `detectors/textual_variants.py`：4 个检测器，全部 SQL pushdown、列级统计证据——
  `spelling_variant`（distinct 值 ≤500 采样，lower+去分隔符归一化后同一逻辑值的不同原值 ≥2 对且合计占比 ≥0.001，LOW）、`fullwidth_character`（全角字母数字 U+FF10-19/FF21-3A/FF41-5A 混入，LOW）、`mojibake_character`（U+FFFD 替换符，编码损坏标志，LOW）、`invalid_numeric`（列名含 price/amount/count/qty 等数值特征、物理 VARCHAR，非数值文本占比 ≥0.01 且 ≥2 行，MEDIUM；date/time 列名特征豁免）
- duckdb RE2 不支持 `\u` 转义：unicode 码位用 `\x{HHHH}`（Go 语法）——`[\x{FF10}-\x{FF19}]` 正确匹配全角数字
- 融合：4 个 issue_type → `string_format` family（VALIDITY）；检测器计数 32 → 36（M4 ≥20 保持）

## 修复引擎 MVP（Step 19，12.5/15 章子集 + ADR-020）

- `repair/engine.py`：闭环 propose → preview → apply → rollback（W11 修复闭环）
- 只支持确定性、值级操作（ADR-020 边界）：`leading_or_trailing_whitespace`→TRIM、`inconsistent_case`→NORMALIZE_CASE、`suspicious_missing_token`→REPLACE_MISSING_TOKEN（11 种缺失占位符→NULL）、`invalid_date`/`impossible_date`→SET_NULL（ISO 日期格式+解析双校验）、`iqr_outlier`/`percentile_outlier`/`modified_zscore`→CLIP_VALUE（evidence 的 lower/upper 边界，需单列）；impute/map_category 等推断类归 V1（伪造数据风险）
- 融合家族化后的 Issue：原始类型在 `detector_ids`（与 issue_type 同名），按可修复优先级挑选
- `preview`：临时副本 + 同检测器重跑 → `rule_failures_before/after`、`rows_changed_ratio`、`null_delta`/`unique_delta`、前 10 行 `changed_examples`
- `apply`：原文件永不变，产物在 `<workspace>/.datasentry/repairs/`——修复副本 `<run_id><ext>` + before artifact `<run_id>.before<ext>`；`rollback` = artifact 全量重建 `<run_id>.rolled_back<ext>`（operation log 仅前 500 条样本，回滚不依赖）
- 格式回写按源类型：parquet / jsonl / xlsx / csv；`DataHandle` 协议新增 `source_type`/`source_path` 只读属性；`RepairProposal` 新增 `issue_type` 字段（修复目标类型，规则重跑用）
- 检测器计数保持 36；测试 306 → 313（新增 tests/test_repair_engine.py 7 例：提案映射/预览/应用+回滚指纹一致/CLIP 边界）

## 性能基准（Step 20，20.4 + ADR-021）

- `benchmarks/bench_scan.py`：生成 1e6 行合成 CSV（6 列、注入 0.05%~0.1% 脏数据），度量画像/逐检测器/全量扫描/JSONL 流式读
- 实测（Apple Silicon 开发机）：画像 0.6s、数值异常 1.7s、全量扫描（36 检测器+融合+评分）24.9s、JSONL 流式读 3.2s/1e6 行——全部 PASS(优化档)
- 双档判定（ADR-021）：验收下限仅时间（画像/全量扫描 ≤60s @1e6）；峰值内存增量 677MB 超优化目标 146MB（duckdb 聚合常驻缓冲），不阻塞验收、记入 W10 性能打磨跟踪
- 基准含数据流式生成（不驻留 Python 列表）+ 空表预热基线，`ru_maxrss` 增量衡量数据相关内存
