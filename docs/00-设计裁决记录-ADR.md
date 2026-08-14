# DataSentry AI — Architecture Decision Records（ADR）

> 记录格式：编号 / 状态（Accepted·Proposed·Superseded）/ 日期 / 决策 / 理由 / 影响。
> 依据「36、开源要求」与「01 一致性检查」约定建立。

---

## ADR-001：质量总分维度口径（对应 C-01）

- **状态**：Accepted（2026-08-01）
- **决策**：质量总分维持 6 维度（Completeness/Validity/Uniqueness/Consistency/Integrity/Timeliness），
  Accuracy Proxy 类 Issue 归入 **Validity**，Distribution Stability 类归入 **Integrity**
  （漂移为 V1，届时再评）。
- **理由**：8 维度公式会破坏 27.2 已定权重与全部历史对齐；两个额外维度在 MVP 期检测器
  覆盖面不足，硬塞会稀释可解释性。
- **影响**：27.2 权重表不变；Issue 模型增加 `quality_dimensions` 字段（见 ADR-008）；
  9.9 映射矩阵加注归并规则。

## ADR-002：Priority Score 上限修正（对应 C-02）

- **状态**：Accepted（2026-08-01）
- **决策**：`criticality` 项归一化为 `10 × (criticality_weight − 0.6) / 1.0`，使该项取值
  ∈ [0, 10]；其余项按 12.8 原公式；最终结果 clamp 到 [0, 100]。
- **理由**：原公式在 critical 字段时理论上限 106 > 100，破坏「0–100 分制」契约。
- **影响**：12.8 公式表更新；评分引擎（Step 8）按此实现，附完整算例单测。

## ADR-003：Severity 权重单一来源（对应 C-03）

- **状态**：Accepted（2026-08-01）
- **决策**：统一使用 `SEVERITY_WEIGHTS = {info: 0.1, low: 0.25, medium: 0.5, high: 0.75, critical: 1.0}`，
  27.1 的 `severity_norm` 表废弃，维度得分公式引用同一常量。
- **理由**：两套取值并存必然漂移。
- **影响**：`datasentry_core/scoring/weights.py` 作为唯一来源。

## ADR-004：跨表检测器与契约引擎归入 V1（对应 C-04）

- **状态**：Accepted（2026-08-01）
- **决策**：MVP 仅支持单表/单文件；跨表检测器（11.11）、数据契约 DSL 全功能（16 章）为 V1；
  MVP 仅提供 `contract validate`（YAML 格式校验命令）。场景 F 标注为 V1 场景。
- **理由**：跨表检测器依赖契约声明（外键/对账表达式），契约引擎本身依赖版本机制，链条过长，
  与 7.2 原意一致。
- **影响**：12 周计划 W13+ 排期；模型层 Contract 模型仍先定义（V1 用），不做迁移。

## ADR-005：执行引擎收敛为 DuckDB（对应 R-OD-02）

- **状态**：Accepted（2026-08-01）
- **决策**：MVP 执行层只集成 DuckDB（SQL pushdown + dataframe 调用面双形态），不引入 Polars。
- **理由**：双引擎增加抽象成本与行为不一致风险；DuckDB 单文件嵌入式、零运维、覆盖分析需求。
- **影响**：17.1 推荐清单修订；Polars 列为 V1 性能优化选项。

## ADR-006：AI 能力 MVP 边界（MVP 范围差异）

- **状态**：Accepted（2026-08-01）
- **决策**：MVP 的 AI 相关仅含 Provider 抽象（OpenAI-compatible + Mock + None）、PII 脱敏管线、
  五分区模板、结构化输出校验、Token 预算与降级链；AI 解释/规则生成/语义 Phase B/契约推断
  全部移入 V1（W10 相应变为性能打磨与回归）。
- **理由**：42 章 MVP 验收不要求 AI；R-OD-03 防止研究目标挤占产品闭环。
- **影响**：12 周计划 W9 范围收窄、W10 内容调整；DQBench 消融 E5/E6 顺延。

## ADR-007：性能预算双档口径（对应 C-05）

- **状态**：Accepted（2026-08-01）
- **决策**：20.4 为优化目标档，3.1/42.1（≤60s @1e6 画像）为验收下限档；
  `benchmarks/` 脚本同时输出两档判定。
- **理由**：三处数值不一致，须固化语义。

## ADR-008：Issue 增加 quality_dimensions 字段（对应 C-16）

- **状态**：Accepted（2026-08-01）
- **决策**：`Issue.quality_dimensions: list[QualityDimension]`（枚举：8 个维度），
  由融合引擎在 Step 7 填充，评分引擎（Step 8）按 ADR-001 归并。
- **理由**：兑现 9.9「Issue 可追溯维度」承诺。

## ADR-009：表达式安全校验（对应 C-07）

- **状态**：Accepted（2026-08-01）
- **决策**：规则表达式以 AST 白名单为唯一强制校验（11.10），14.3 黑名单仅作快速预筛
  （命中即提前拒绝并提示），不做安全断言。
- **理由**：黑名单可被编码绕过；白名单才构成安全边界。
- **影响**：Step 6/Step 13 实现时引用本 ADR。

## ADR-010：存储布局二元化（对应 C-09）

- **状态**：Accepted（2026-08-01）
- **决策**：全局配置/缓存（凭据、全局设置、LLM 缓存）→ `~/.local/share/datasentry/`
  （macOS: `~/Library/Application Support/datasentry/`）；项目数据（数据集元数据、扫描、
  报告、契约、审计）→ 项目工作区 `.datasentry/`。
- **理由**：SDK 工作区（23.1）与全局初始化（51）两套表述必须归一。

## ADR-011：执行引擎与检测器的可扩展约束（对应 4.8 裁决规则）

- **状态**：Accepted（2026-08-01）
- **决策**：确认 4.8 冲突裁决规则直接作为工程约束：隐私 > 效果；证据 > 速度；确认 > 自动；
  统计 > LLM；可复现 > 完整性。
- **理由**：冲突场景在实现期必然出现，预先定序避免逐案争论。

## ADR-012：元数据迁移策略与 fingerprint 冗余列（对应 docs/04 草案）

- **状态**：Accepted（2026-08-02）
- **决策**：
  1. MVP 迁移以 `PRAGMA user_version` 递增 + 幂等 DDL（`CREATE TABLE IF NOT EXISTS`），
     旧代码打开新库（version 超限）抛错；Alembic 基线归 V1。
  2. `scan_runs` 增加 `fingerprint` 冗余 JSON 列（草案原经 `dataset_versions` 关联，
     MVP 无版本写路径，无法回读 `ScanRun.fingerprint` 必填字段）。
- **理由**：草案说明 4「Alembic 基线在 Step 后续建立」= V1 事项；MVP 避免引入迁移框架依赖。
  fingerprint 为只读冗余（同数据集同内容时 schema_hash 恒定，无漂移风险）。
- **影响**：docs/04 草案按本 ADR 修订两处；`storage/schema.py` 的 `SCHEMA_VERSION` 为唯一版本号；
  字段兼容由模型单测守护（草案说明 4）。

## ADR-013：质量总分实现归一（27.1 公式的工程化）

- **状态**：Accepted（2026-08-02）
- **决策**：
  1. `severity_norm` 采用 `SEVERITY_WEIGHTS`（ADR-003），27.1 的 severity_norm 表废弃；
     12.4 字段关键度权重（0.6/1.0/1.3/1.6）即 `CRITICALITY_WEIGHTS`。
  2. `max_possible = 该维度 Issue 数 × 1.6`（12.4 critical 权重 × 最坏取值全 1.0），
     即「critical 字段 100% 受影响」为单 Issue 的理论最坏影响 → NORMAL 字段维度得分下限 37.5，
     critical 字段可至 0（保证字段关键程度参与扣分而非相消）。
  3. 字段关键度 MVP 默认 NORMAL=1.0（与 Step 8 评分引擎一致；语义推断与 Contract 覆盖归 V1）；
     覆盖调节（规则覆盖范围）MVP 固定 1.0，归 V1。
  4. 无相关检测器运行的维度得分 None，不参与加权，权重重新归一化（27.1）；
     Issue 标注多维度时对每个维度计分（MVP 检测器为单维度，防御性约定）。
  5. 总分在扫描时计算并随 `ScanRun.quality_score` 落库（复用 schema 既有列），
     历史报告保留原权重与 `score_version`（27.2 趋势重算归 V1 UI）。
- **理由**：27.1 公式含三处需工程裁决的取值（severity_norm 口径、max_possible、覆盖调节）；
  ADR-003 已定 severity 权重单一来源，max_possible 固定基准保证分数随字段关键度单调。
- **影响**：`scoring/quality.py` 为唯一实现；`QualityScore` 模型增 `dimension_contributions`
  （27.3「该维度由哪些 Issue 扣分」悬停数据，JSON 列向后兼容）。

## ADR-014：报告引擎与质量门禁 MVP 归一（26/22 章）

- **状态**：Accepted（2026-08-02）
- **决策**：
  1. 26.2 报告头（`report_schema_version`/`datasentry_version`/`scan_run_id`/`generated_at`/
     `reproducible`/`llm_used`）为所有格式共享；`build_report()` 为 JSON 机器契约唯一构造点，
     CLI `report export --as json` 与 SDK `export_report()` 输出同一结构（无差异消费）。
  2. 报告格式参数命名 `--as {json,markdown,html}`（规格 `--format` 与全局输出 envelope
     `--format text|json` 冲突，取工程化命名，ADR 记录偏差）；默认落点
     `<workspace>/.datasentry/reports/<run_id>.<ext>`（ADR-010），`--output` 可覆盖。
  3. HTML 为自包含单文件（内嵌 CSS、无外部资源），MVP 渲染 8 节
     （26 章 12 节中 Drift/Suggested Rules/Repair History 归 V1；
     Column Profiles 并入 Dataset Overview 列签名区）。
  4. 质量门禁（22 章场景 C）在扫描结果上求值：`scan --fail-on SEV --max-failure-ratio R`，
     失败退出码 1（`EXIT_GATE_FAILED`）；`fail_on` 为精确严重度集合（非「及以上」），
     `maximum_failed_rows_ratio` 用失败项 `affected_ratio` 的最大值作上限近似
     （行可能同属多个 Issue，求和会重复计数）；契约规则执行与 `require_repair_validation`
     归 V1（ADR-004）——设为 True 时门禁显式失败而非静默忽略。
  5. `reproducible=True`（MVP 确定性检测器 + 无 LLM 调用，报告头声明）。
- **理由**：26 章输出契约与 22 章退出码均已定稿，需归一「envelope --format vs 报告格式」
  与「无契约规则时门禁依据」两处空隙。
- **影响**：`reporting/`（build_report + markdown/html 纯函数渲染）与 `scoring/gate.py`
  为唯一实现；`export_report()` 返回结构升级为 26.2 规范（含报告头）。

---

## ADR-015：跨字段规则检测器的安全表达式求值（11.10 读操作子集）

- **状态**：Accepted（2026-08-02）
- **决策**：
  1. MVP 表达式 DSL 为 **Python eval 模式子集**（`ast.parse(mode="eval")`）；
     SQL 表达式 / YAML 契约 DSL 归 V1（ADR-004）。列名须为合法 Python 标识符方可绑定。
  2. `detectors/safe_eval.py` 三重防护（规格 11.10 约束 2）：
     节点类型显式白名单（字面量/名称/比较/布尔/算术/三元/`in`/`is`；语句、
     赋值、lambda、推导式、属性读取除白名单方法外一律拒绝）；
     Name 黑名单（eval/exec/open/__import__/getattr/subprocess 等 21 项）；
     Call 目标白名单（8 个内置函数 + str 读方法 15 个）；
     `eval` 注入 `__builtins__={}` 仅暴露白名单函数。
  3. 超时 10s（SIGALRM/ITIMER_REAL，规格约束 3）；纯表达式不可循环，
     超时仅作纵深防御。
  4. 缓存以表达式 SHA-256 前 16 位为键（规格约束 4）：AST 编译缓存 +
     行级结果缓存（上限 1e6 条目，超限清空）。
  5. None 语义：参与运算的行返回「不适用」（跳过），不算违规
     （缺失值归缺失检测器，避免双重计数）。
  6. 内置规则按列名语义对自动绑定（前缀式，与规格示例一致）：
     `{name}_start|begin|from <= {name}_end|finish|to`、
     `{name}_min|lower <= {name}_max|upper`；仅同类型族（数值/日期）配对；
     每规则一条 Issue，行级证据前 20 行。
  7. 性能：MVP 行级 Python 求值（`ROW_NUMBER()` SQL 下推取数）；
     全量下推/抽样路径归 V1。
- **理由**：11.10 为 MVP 表唯一未实现的「引擎类」检测器，且是 V1 契约引擎
  规则执行的种子（契约规则可复用同一求值器与 family）。
- **影响**：新增 `cross_field_constraint` Issue family（VALIDITY）；
  检测器计数 22；`initial/common.py` 提升为 `detectors/common.py`
  （消除 cross_field ↔ initial 循环导入）。

---

## ADR-016：日期时间检测器族的判定边界（11.8 核心 P0 化）

- **状态**：Accepted（2026-08-02）
- **决策**：
  1. 适用范围两类（`_TEMPORAL_TYPES = {DATE, TIMESTAMP, TIMESTAMPTZ}`，
     TIME 排除——与日期比较会类型错误）＋字符串日期提示列
     （列名含 date/dob/birth；**排除** timestamp/_at，created_at 等
     审计时间戳不判格式非法）。
  2. `future_date`/`stale_date` 仅作用于日期列（可直接比较）：
     `> current_date + INTERVAL 1 DAY`、`< current_date − INTERVAL 365 DAY`；
     stale 豁免列名 hints = birth/dob/founded/hire/established/historical/history。
  3. `impossible_date` 判定 = `regexp_matches(ISO 模式) AND try_strptime(...) IS NULL`：
     duckdb 的 `strptime` 对非法日期（2024-02-30）抛 ConversionException
     而非返回 NULL，必须用 `try_strptime`（返回 NULL）。
  4. `mixed_date_format` 按**行**计数（非 distinct），格式类
     iso/slash/dot/compact/other，`len(set(values)) < 10` 跳过，
     占比 > 0.02 且 ≥2 类才报（防孤立个例）。
  5. `duplicate_timestamp` affected = Σ(c−1)（每组 >2 的重复计数），
     上限 20 组行级证据。
  6. 严重度：impossible HIGH（数据不可用）；invalid/duplicate MEDIUM；
     future/stale/mixed LOW。
- **理由**：11.8 为 P0 需求（C-13 核心列），日期是数据质量最高频问题域之一；
  try_strptime 行为是 duckdb 实际语义的规避记录。
- **影响**：新增 `datetime_anomaly` Issue family（VALIDITY）；
  检测器计数 22 → 28（M4 ≥20 保持）；`common.py` 新增
  `datetime_columns()`/`quote_re()`（textual.py 本地 quote_re 删除）。
  已知边界：duckdb CSV 推断把可统一解析的混合格式列提升为 DATE，
  mixed_date_format 仅对解析失败（VARCHAR 残留）的列触发。

---

## ADR-017：缺失模式检测器的判定边界（11.4 核心 P0 化）

- **状态**：Accepted（2026-08-02）
- **决策**：
  1. 新增 4 个检测器（11.4 Missingness 6 核心中的 4 个，余
     excessive/token 已有）：
     - `sudden_missingness`：时间桶缺失率突变。桶粒度日→月→年自适应
       （桶数 <5 升级）；判定 = 桶缺失率 ≥ max(0.2, 整体 + 0.2) 且桶样本 ≥10。
     - `group_missingness`：按类别列（distinct ∈ [2,50]）分组的目标列缺失率
       异常组（同阈值），组样本 ≥10。
     - `conditional_missingness`：定向级联缺失——A 缺失行中 B 缺失率 ≥0.8
       且 A 缺失样本 ≥10（主记录缺失 ⟹ 子记录缺失）。
     - `correlated_missingness`：对称共现——双列同时缺失 ≥5 行且共现率
       `both_null / min(a_null, b_null)` ≥0.5（分母为 0 时跳过）。
  2. **阈值用绝对差而非相对倍数**：相对 3× 在整体缺失率 >1/3 时
     阈值 >1 永不触发（如整体 0.5 时桶需 1.5）；绝对差
     `≥ max(0.2, overall + 0.2)` 恒有解且语义直观。
  3. 双列/分组/时间桶场景**只取列级统计证据**，不取行级证据：
     行级行号（rowid）在视图上可用但成本高、价值低，行级证据仅保留
     单列逐行场景（uniqueness 等，D-02）。
  4. 性能预算（防组合爆炸）：列对 ≤50（缺失率 ≥0.02 的列截断前 15）、
     分组列 ≤10 × 目标列 ≤5、目标列 ≤5 × 时间列 ≤3；每条候选一条
     单表聚合 SQL。
  5. 严重度：correlated LOW（模式提示）；sudden/group/conditional MEDIUM
     （采集断点/级联缺失，修复成本高）。
- **理由**：11.4 为 MVP 表「Missingness 6 核心」中最大的缺口；
  突变/分组/级联缺失是实际数据管线中断的最常见信号。
- **影响**：4 个 issue_type → `missingness` family（COMPLETENESS）；
  检测器计数 28 → 32（M4 ≥20 保持）；`common.py` 无改动
  （缺失统计 helper 均为 missingness.py 内部实现）。

---

## ADR-018：表示变体与编码检测器的判定边界（11.7/11.9 余项）

- **状态**：Accepted（2026-08-02）
- **决策**：
  1. 新增 4 个检测器：
     - `spelling_variant`（11.9）：无参考字典时拼写判定不可行，
       MVP 定义为**表示变体**——distinct 值 ≤500 采样后
       `lower + 去分隔符（非 [0-9a-z]）`归一化，同一逻辑值的不同
       原值 ≥2 对且每组合计占比 ≥0.001 才报。覆盖 "A-10000"/"A10000"、
       "1,000"/"1000" 等；大小写变体归 inconsistent_case（不重复报）。
     - `fullwidth_character`（11.7 余项）：全角字母数字
       （U+FF10-19/U+FF21-3A/U+FF41-5A）混入；CJK 中文本身不在
       这些码位，不误报。
     - `mojibake_character`（11.7 余项）：U+FFFD 替换符（无效 UTF-8
       解码的标志）。
     - `invalid_numeric`（11.7 余项）：列名含数值语义 hint
       （price/amount/count/quantity/qty/salary/money/fee/total/
       cost/age/num）但物理 VARCHAR 的列中非数值文本
       （`^[+-]?[\d,.\s]+$` 之外），占比 ≥0.01 且 ≥2 行才报；
       date/time/timestamp 列名特征豁免（日期字符串不算非数值）。
  2. duckdb RE2 不支持 `\u` 转义：unicode 码位统一用 `\x{HHHH}`
     （Go/RE2 语法）；`\xFFFD` 两字节形式会被当成 UTF-8 多字节
     序列处理而失效，必须 `\x{FFFD}`。
  3. 全部列级统计证据（延续 ADR-017 决策 3）；spelling_variant
     的 distinct 采样 cap 500 防超大列（性能预算）。
  4. 严重度：invalid_numeric MEDIUM（数值语义列被文本污染，消费端
     解析必炸）；spelling/fullwidth/mojibake LOW（规范性问题）。
- **理由**：11.7 文本族为 MVP 最大族群（8 个），补齐编码/表示类
  后文本类检测器达 12 个；invalid_numeric 覆盖国内数据常见
  「面议/—/空串外写法」混入金额列场景。
- **影响**：4 个 issue_type → `string_format` family（VALIDITY）；
  检测器计数 32 → 36（M4 ≥20 保持）。

---

## ADR-019：多格式文件连接器的读取语义（7.1 文件型三件套）

- **状态**：Accepted（2026-08-02）
- **决策**：
  1. 新增 Parquet/JSONL/XLSX 三个连接器（7.1 六种数据源中的文件型；
     SQLITE/POSTGRESQL/DUCKDB 数据库型归 V1——需连接级沙箱与凭据
     管理，MVP 不做）。
  2. `connectors/file_based.py` 共享基类：schema（DESCRIBE 视图）/
     read_sample/sql_aggregate/count_rows/fingerprint/warnings
     （公式注入扫描，11.7）/close。**CSV 连接器不重构**（编码探测/
     分隔符嗅探等专属逻辑已稳定，避免回归）。
  3. Parquet：duckdb `read_parquet` 注册视图（SQL pushdown 统一入口）+
     pyarrow `ParquetFile.iter_batches` 流式批读（warnings 扫描路径）。
  4. JSONL：`read_json_auto(format='newline_delimited')` 视图；
     read_batches 用 LIMIT/OFFSET 分页（duckdb 无 JSONL 流式读接口，
     1e6 行画像预算内重复扫描可接受，Step 20 基准验证）。
  5. XLSX：openpyxl `data_only=False`——公式单元格返回**公式文本**
     （公式注入前缀可被 warnings 检测，电子表格场景价值最高）；
     公式计算结果的缓存读取归 V1。混合类型列（int/str 混排）
     自动推断失败时回退全字符串（`str(value) if value is not None
     else None`，不炸表）；sheet（名称/索引）与 header_row 可配。
  6. SDK `scan_file()` 按扩展名推断 source_type
     （.csv/.tsv/.parquet/.pq/.jsonl/.ndjson/.xlsx）；未知格式抛
     FileNotFoundError（与文件不存在同一语义，CLI 退出码 4）。
- **理由**：XLSX 是数据质量工具的日常输入（业务人员导出）；Parquet
  是湖仓标准格式；JSONL 是日志/事件流标准。三个连接器共享基类避免
  三份 schema/抽样/指纹重复实现。
- **影响**：default_registry() 4 个连接器（原 1 个）；新增依赖
  openpyxl（core）；mypy overrides 补 openpyxl（无类型存根）；
  旧断言更新（PARQUET 从「不支持」变「支持」）。

---

## ADR-020：修复引擎 MVP 的操作边界与回滚语义（15 章）

- **状态**：Accepted（2026-08-02）
- **决策**：
  1. MVP 只支持**确定性、值级**修复操作（12.5 章第 8 类中的
     5 类）：TRIM_WHITESPACE / NORMALIZE_CASE /
     REPLACE_MISSING_TOKEN / SET_NULL / CLIP_VALUE。**推断类操作
     归 V1**（impute 的均值/中位数填充、map_category 的类别合并——
     伪造数据风险，C-04 契约引擎归 V1 同构）。
  2. Issue → 操作映射按**检测器级 issue_type**（
     leading_or_trailing_whitespace 等）。融合家族化后原始类型
     在 `Issue.detector_ids`，propose 按优先级挑选首个可修复的；
     `RepairProposal.issue_type` 新增字段承载该原始类型（规则重跑
     目标，`RepairProposal.issue_id` 不携带类型信息）。
  3. CLIP_VALUE 仅当 evidence 提供 lower/upper 边界（IQR 等数值
     离群证据）且**单列**时提案——多列离群各列边界不同，MVP
     不支持异构参数。
  4. 原文件**永不修改**；修复产物在 `<workspace>/.datasentry/repairs/`：
     修复副本 `<run_id><ext>` + before artifact `<run_id>.before<ext>`
     （源文件完整副本，非 diff）。
  5. **回滚 = artifact 全量重建** `<run_id>.rolled_back<ext>`。
     RepairOperationRecord（行级 before/after）仅存前 500 条样本
     （日志与展示用），**不依赖 operation log 回滚**——全量重建
     消除了部分回滚的补丁顺序问题。
  6. `rule_failures_before/after` = 在原始/修复副本上重跑同一检测器
     的候选数（preview 用临时目录副本，无副作用）。
  7. `DataHandle` 协议新增 `source_type`/`source_path` 只读属性
     （CSV 与 file_based 基类实现），供引擎按源类型写回与定位
     before artifact。
- **理由**：确定性操作可预览、可审计、零歧义；推断类操作在无
  契约引擎（C-04/V1）约束时等于替用户做猜测。原文件不可变 +
  artifact 全量重建保证回滚是精确的「时间倒流」。
- **影响**：新增 `repair/` 包与 `storage/paths.project_repairs_dir()`；
  8/15 章的 8 类修复中 5 类落地，其余（cast_type/set_null 已含、
  map_category/impute）V1；CLI 侧接入（`ds repair` 命令）归
  Step 后续/报告集成。

---

## ADR-021：性能基准的验收/优化双档判定（20.4）

- **状态**：Accepted（2026-08-02）
- **决策**：
  1. `benchmarks/bench_scan.py` 为 Step 20 基准入口：生成 1e6 行
     合成 CSV（6 列：id/price/category/event_date/name/status，
     注入 0.05%~0.1% 脏数据：离群价/非法日期/前后空白/n/a 占位），
     度量画像（Profiler）、逐检测器、全量扫描（ScanRunner +
     融合 + 评分）、JSONL 流式读（LIMIT/OFFSET 分页，ADR-019）。
  2. **双档判定**（ADR-007 口径）：
     - 验收下限档（3.1/42.1，仅时间）：画像 ≤60s、全量扫描 ≤60s
       @1e6，**内存不阻塞验收**；
     - 优化目标档（20.4 表）：画像 <20s、数值异常 <20s、
       峰值内存 ≤ 数据量×3。
  3. **实测结果**（Apple Silicon 开发机，2026-08-02）：
     - 画像 0.6s、数值异常 1.7s、全量扫描 24.9s——全部 PASS(优化)；
     - JSONL 全量流式读 3.2s/1e6 行——ADR-019 分页路径达标；
     - 峰值内存增量 677MB vs 优化目标 146MB：**超出优化档**。
- **理由**：时间预算远优于验收下限（24.9s < 60s）；内存超档由
  duckdb 聚合常驻缓冲（36 个检测器逐一全列读入）引起，MVP 验收
  不含内存门禁，故记入 ADR 供 W10 性能打磨回归跟踪（候选：
  数值列先落 parquet 再聚合、或限制 PRAGMA threads）。
- **影响**：`benchmarks/` 目录成为回归基线；CI 可后续挂接对比。

---

## ADR-022：覆盖率门禁固化（37.5 阶段 4 / M8）

- **状态**：Accepted（2026-08-02）
- **决策**：`make test` 一律挂 `--cov=datasentry_core --cov-fail-under=85`；
  覆盖率低于 85% 时退出码非 0，门禁即失败。
- **理由**：M8（核心包覆盖率 ≥85%）在 Step 22 首次实测即达 95%，
  但门禁未含覆盖率，后续回归无约束；固化后任何新代码必须在
  `make check` 内同时满足 lint/type/test/coverage 四道门。
- **影响**：`storage/paths.py`（73%）与 `connectors/file_based.py`
  （80%）为达到门禁补齐测试，核心包总览 96.42%（3298 stmt）。
  计数口径：tests/test_paths.py（三平台布局/override/expanduser）
  + file_based 边界（path=None、sampled 指纹、警告缓存与截断、
  抽样 fallback、close 后使用、抽象契约）。

---

## ADR-023：REST API 的单工作区门面与同步端点（22/23 章 HTTP 面）

- **状态**：Accepted（2026-08-02）
- **决策**：
  1. `src/datasentry/api.py` 的 `create_app(project=None)` 绑定单个
     `DataSentry` 实例（项目工作区门面），所有端点复用 CLI/SDK 同一条
     导入→扫描→落库→修复闭环，不引入第二套领域逻辑；
  2. MVP 只提供**同步端点**（FastAPI 默认并发已覆盖多数用法）；异步
     Job 队列（任务提交 + 轮询状态）归 V1——MVP 扫描 ≤60s@1e6（ADR-021）
     无需长任务基础设施；
  3. 错误映射：FileNotFoundError/KeyError→404、ValueError→422、
     其余→500，body 统一 `{"ok": false, "detail": ...}`；
  4. 修复端点以 `source_path`（源文件路径）定位数据，`run_id` 仅作
     业务锚定（与 CLI `repair` 子命令同语义，ADR-020）。
- **理由**：MVP 划分（docs/03）将 REST API 列为 MVP，但闭环优先原则
  （「导入→扫描→Issue→评分→报告」）已由 CLI/SDK 闭环实现；REST 面
  是对同一闭环的 HTTP 暴露，同步模型避免为「秒级扫描」引入任务队列
  的过度设计（与 ADR-004/R-OD-01 范围熔断一致）。
- **影响**：新增依赖 fastapi/uvicorn（运行）、httpx（测试）；端点清单
  13 个；`DataSentry` 新增 `list_scan_runs()`（此前仅 store 层有此方法）。

---

## ADR-024：Web UI 的服务端渲染边界（docs/03 1.2 五个核心页）

- **状态**：Accepted（2026-08-02）
- **决策**：
  1. UI 走 FastAPI 服务端渲染（`src/datasentry/ui.py`），与
     `reporting/html.py` 同一零依赖风格——内嵌 CSS、无前端构建链、
     无 JS 框架；所有输出统一 `escape()` 转义（XSS 安全）；
  2. MVP 只实现三个核心页：首页（扫描概览 + 新建扫描）、
     Dataset Overview + Issue Center（severity 过滤）、修复工作台
     （15 章 propose→preview→apply→rollback 闭环）；Column Explorer
     与跨扫描趋势归 V1；
  3. 表单提交用 POST + 303 重定向（PRG 模式），页面无状态。
- **理由**：MVP 划分（docs/03 1.2）要求五个核心页，但「修复工作台」是
  唯一必须形成闭环的页面（12.5/15 章）；Column Explorer 依赖画像
  逐列下钻，价值密度低于问题定位闭环，且单页应用化（前端框架）会
  引入构建链，与 MVP 离线单文件原则冲突（ADR-023 同源）。
- **影响**：新增 `src/datasentry/ui.py`（约 270 行渲染函数）+
  `DataSentry.get_issue()` + python-multipart 依赖；XSS 测试以列名
  注入路径覆盖（Issue title 为模板化标题，不含原始值）。

---

## ADR-025：M9 Demo 的可复现单脚本形态（34 章）

- **状态**：Accepted（2026-08-02）
- **决策**：
  1. `examples/demo/demo.py` 为 M9 验收入口：单脚本、无 LLM、
     完全离线，跑通「生成脏数据 → 全量扫描 → 报告 → 修复闭环 →
     回滚」全流程，结尾打印各阶段耗时并判定 < 3 分钟预算
     （34 章）；
  2. 数据生成内置在脚本内（5000 行、固定种子 42），不依赖外部
     数据集文件——任何环境一条命令可复现；
  3. 子进程测试（tests/test_demo.py）以硬预算断言守护回归：
     测试本身带 timeout（预算 + 30s），超时即失败。
- **理由**：M9（42 章验收「Demo 数据集完整走通 < 3 分钟」）需要
  可交付、可复现的演示入口；固定种子保证数据/报告可对比，内嵌
  生成器避免 demo 数据成为仓库的二进制资产（与 ADR-021 基准数据
  流式生成同一思路）。
- **影响**：实测 5000 行全流程 5.4s（余量 33×，1e6 行级数据仍有
  充足余量）；demo 输出含 report.json/report.html/customers.csv，
  工作区 `.datasentry/` 自动 gitignore（ADR-010）。

---

## ADR-026：容器与 CI 形态（42 章收尾）

- **状态**：Accepted（2026-08-02）
- **决策**：
  1. 镜像基于 ghcr.io/astral-sh/uv 多阶段构建：builder 阶段
     `uv sync --frozen --no-dev`，runtime 阶段仅拷贝 .venv/src/
     packages（editable 安装路径一致即可运行），不包含测试与
     dev 依赖——镜像体积与攻击面最小化；
  2. 服务入口为新增 `datasentry-server` console script
     （`datasentry.api:main`），默认 0.0.0.0:8000；工作区路径由
     `DATASENTRY_PROJECT` 环境变量注入（api.create_app 读取），
     容器内默认 /app/workspace 并卷挂载宿主 workspace/ 目录；
  3. CI 单 job 十阶段（GitHub Actions）：lint → format → mypy
     --strict → pytest+覆盖率门禁 → 覆盖率工件上传 → M9 demo
     → 1e6 行基准（≤60s 档，实测 17.4s）→ CLI smoke → API+UI
     smoke → 产物存在性检查；基准与 demo 纳入 CI 防止回归；
  4. `make type` 修正为 mypy 同时检查 datasentry_core 与
     datasentry（此前仅 core，与门禁声明不符——Step 23 起门禁
     实际已含 src，本次对齐）。
- **理由**：42 章要求「有明确 License 和贡献指南」；MVP 划分 1.6
  要求 Docker 一键启动与 CI 十阶段。单 job 顺序执行避免并发 runner
  成本，demo/bench 进 CI 守住 M3/M9 预算（34 章）。
- **影响**：容器实测 health/UI/API 全通；`make check-all` 一条命令
  覆盖 lint+type+test+demo+bench；CI 全流程约 5 分钟内完成。

---

## ADR-027：LLM 能力分层与脱敏边界（38 章安全子集）

- **状态**：Accepted（2026-08-02）
- **决策**：
  1. **分层**：`datasentry_core/llm/provider.py` 只定义 Protocol 与
     数据结构（**零网络依赖**，core 保持纯离线可测试）；
     HTTP 实现（OpenAI-compatible / Ollama）在应用层
     `src/datasentry/llm_providers.py`（httpx 进 main 依赖）；
  2. **脱敏前置**：所有入 LLM 的文本必须先经
     `privacy/redactor.py`（38 章：AI 不接收未经授权的完整数据）。
     掩码确定性（同输入同输出）保证 LLM 缓存可复用；映射表只在
     进程内传递、不落盘——落盘加密存储归 V1 后续迭代；
  3. **显式降级**：未配置提供方 = `NullProvider`，任何调用抛
     `LLMNotConfiguredError`，调用方走降级路径（现有离线行为
     完全不变，ADR-014 的可复现性承诺不受影响）；
  4. **审计**：每次调用写 `llm_invocations` 表（13.11），只存
     字段名/统计量/掩码样本数/注入标记，**不存 prompt 原文**；
     CLI 提供 `llm status` / `llm invocations` 查询；
  5. PII 识别采用**确定性正则启发式**（email/手机号/身份证/IPv4/
     URL），姓名等无可靠正则的类别不识别——宁可漏报不误伤
     （误伤会破坏修复语义与 LLM 输出一致性）。
- **理由**：MVP 划分将 38 章安全子集列为 MVP 期唯一遗留项；
  分层保证 core 可独立测试、应用层可换 provider；确定性掩码 +
  不落盘映射表在「可复现」与「隐私」之间取明确边界。
- **影响**：新增 72 → 72 文件 mypy 覆盖；未配置 LLM 时
  `datasentry llm status` 显示 configured=false，扫描流程零改动；
  httpx 由 dev 依赖升为 main 依赖。

---

## ADR-028：规则引擎预运行与 NL→候选审批闭环（14.3/14.4）

- **状态**：Accepted（2026-08-05）
- **决策**：
  1. **期望语义取反**：`Rule.when` 表达的是「数据应当满足的
     期望条件」，引擎在预运行阶段自动取反生成违规子句
     （equals→`col <> $1`、gt→`col <= $1`、between→
     `col NOT BETWEEN`、in→`col NOT IN`、not_null→`col IS NULL`
     等）。模型提示词/人工创建都用期望语义书写，避免两套心智；
  2. **预运行即试算**：`rules/engine.py` 的 `run_preflight` 在
     **批准之前**对样本执行违规子句，产出
     `RulePreflightReport`（schema 校验 / 列存在 / 危险规则
     标记 / 抽样试算）。`dangerous = failures > 0 且 ratio > 0.5`
     时标注，CLI 必须显式确认才可批准；参数绑定用 DuckDB 命名
     参数（`$1`…），值以 `{"1": ...}` dict 传入（已有公开
     `sql_aggregate`/`read_sample` 封装，不依赖私有视图）；
  3. **候选落库待批**（14.4）：LLM 生成的候选规则直接
     `enabled=False` 入库，`rules approve` 才置 1（
     `activate_rule` UPDATE 后重读，保证返回激活后状态）——
     未批准规则永不出现在扫描执行集中；
  4. **脱敏复用 ADR-027 管线**：候选生成 prompt 由脱敏后的
     profile（掩码占位符）构建，占位符内嵌列级 PII 掩码统计，
     LLM 只见字段名/分布/掩码值；
  5. **缓存与审计**：prompt 哈希（sha256 前 16 位）命中
     `llm_cache`（`expires_at` 哨兵 `9999-12-31T23:59:59+00:00`
     表示不过期）则跳过调用；每次生成记审计（复用 ADR-027
     的 `llm_invocations`），未配置 LLM 时整条链路显式降级
     （`LLMNotConfiguredError`，exit 3）。
- **理由**：14.3/14.4 要求规则可预运行验证、AI 生成的规则需
  人工确认才生效；预运行在批准前暴露坏规则（列不存在/全行
  违规）是安全阀门；候选与生效规则同表用 enabled 区分，
  避免两套存储。
- **影响**：测试 381 → 394（tests/test_rules_ai.py 13 例，
  覆盖率 96.39%）；CLI 新增 `rules propose`/`rules approve`/
  `rules list`；store 新增规则与 LLM 缓存读写；
  14.3/14.4 闭环完成，AI 生成规则零改动不出现在扫描中。

---

## ADR-029：Ollama 具体接入与超时重试统一（W13 前置项落地）

- **状态**：Accepted（2026-08-05）
- **决策**：
  1. **Ollama 原生 /api/generate 接入定案**：`OllamaProvider`
     以原生 generate 端点（非 OpenAI 兼容层）作为 V1 接入方式，
     零密钥、默认 `http://localhost:11434`；模型必填校验在
     Provider 构造期（`ValueError`）；
  2. **重试策略统一**：把 OpenAI Provider 的 `_post` 提炼为
     共享 `_post_with_retry`（超时按 `max_retries` 重试，HTTP
     状态错误/网络错误/JSON 解析错误不重试），Ollama 复用——
     修正 ADR-027「超时重试」承诺在 Ollama 侧未落实的不一致；
     重试上限错误消息统一为 `timeout after N attempts`；
  3. **真实接入验收**：本地 HTTP 服务模拟 /api/generate 走
     完整链路（真实 TCP + 真实 provider）——propose 预运行
     1 违规行 → 候选落库 → approve 激活 → `llm_cache` 命中
     不再打服务器 → 审计 1 条 `provider_id=ollama`；CLI
     子进程冒烟同样全通（`DATASENTRY_LLM_PROVIDER=ollama` +
     `DATASENTRY_LLM_BASE_URL` 环境变量注入）；
  4. Ollama 响应校验维持 ADR-027：缺 `response` 字段或非字符串
     抛 `LLMSchemaError`（测试中的缺包装响应被正确拦截）。
- **理由**：ADR 待定表「Ollama Provider 的具体接入版本（V1 实现）」
  为 W13 前置项；本地部署是 38 章安全子集的最自然形态（数据
  不出机器），原生端点比 OpenAI 兼容层少一层转换。
- **影响**：测试 394 → 398（Ollama 超时重试 2 例、HTTP 不重试
  1 例、真实接入 E2E 1 例）；`llm_providers.py` 结构：共享
  `_post_with_retry` + 两 Provider 各自解析响应形态。

---

## ADR-030：危险规则批准安全阀门（14.4 强制确认流落实）

- **状态**：Accepted（2026-08-05）
- **决策**：
  1. **approve 真实复核**：`service.approve(rule_id, data_path=None,
     force=False)` 提供 `data_path` 时对目标数据**重跑预运行**
     （而非信任 propose 时快照）——数据在提案与批准之间可能变化，
     复核保证批准时状态即生效时状态；
  2. **危险规则拦截**：复核 `dangerous`（违规行占比 > 0.5）且未
     `force` → 抛 `RuleApprovalBlockedError`（携带 rule_id 与
     reason，含失败行统计），规则保持 `enabled=0`；CLI 报错
     exit 3 并提示 `--force`；`--force` 是用户显式知情确认
     （14.4 用户批准的安全阀门，非绕过）；
  3. **不带 --file 保持原语义**：无数据路径时跳过复核直接激活
     （兼容 ADR-028 既定 approve 行为，API 不破坏）；
  4. **store 新增 `get_rule`**：单条读取供复核使用（此前只有
     list_rules 全量 + activate_rule 写后读）。
- **理由**：ADR-028 仅实现 dangerous 标记展示，README 声称的
  `--force` 确认流未实现（自述与实现不一致）；14.4「先预运行 +
  用户批准」要求把预运行结果纳入批准决策，真实复核优于快照。
- **影响**：测试 398 → 401（拦截/强批/安全复核/无文件跳过 4 例）；
  CLI `rules approve` 新增 `--file`/`--force`；rules_ai.py
  docstring 同步修正（候选落库 enabled=0，非「只展示不落库」）。

---

## ADR-031：检测器插件 API v1 与加载语义（4.8 扩展约束）

- **状态**：Accepted（2026-08-05）
- **决策**：
  1. **加载机制**：`datasentry_core/plugins.py` 的
     `load_plugin_detectors(registry, dirs)` —— 目录扫描
     `*.py`（跳过 `_`/`.` 前缀），`importlib` 动态加载模块，
     发现实现 `Detector` 协议的类（runtime_checkable），无参
     实例化并注册；文件按名称排序（加载确定性）；返回新注册
     detector_id 列表；
  2. **稳定性承诺（插件 API v1）**：`Detector` 协议
     （detector_id/detector_version/quality_dimension/supports/
     detect/metadata）、`DetectionContext` 字段与
     `DetectorRegistry` 接口为稳定面，不破坏性变更；
     `load_plugin_detectors` 签名与 `PluginLoadError` 保持；
  3. **失败即报错**：import 失败/实例化失败/ID 冲突 → 抛
     `PluginLoadError`（含文件定位），不静默吞掉——插件是
     用户主动引入的代码，静默跳过会隐藏配置错误；
  4. **自动加载**：`DataSentry` 打开工作区时加载
     `<workspace>/plugins/`（不存在则跳过）；CLI 新增
     `datasentry detectors` 展示注册表（内置+插件，含
     quality_dimension/version/enabled）；
  5. **安全边界**：插件与本机内置检测器同权（**非沙箱**，
     本地可信代码）；11.10/ADR-015 的受限表达式求值只约束
     规则表达式，不适用于插件模块——文档与示例明确声明。
- **理由**：4.8「检测器可扩展」需最小可信加载面；目录约定
  （workspace/plugins）零配置、复制即用；协议化发现免注册
  清单维护；ADR 待定表「插件 API 版本 1 的稳定性承诺」
  （V1 前置）自此落地。
- **影响**：测试 401 → 408（加载/跳过/冲突/坏导入/缺目录/
  端到端扫描命中/列表展示 7 例）；新增
  examples/plugins/example_detector.py（负值检测示例，接入
  端到端测试）；mypy 覆盖 75 文件。

---

## ADR-032：发布工程形态（0.1.0 里程碑）

- **状态**：Accepted（2026-08-05）
- **决策**：
  1. **双包发布**：`datasentry-core`（领域模型与引擎，零网络
     依赖）与 `datasentry`（CLI/API/UI 门面）分别构建发布，
     均走 hatchling；版本同步为 0.1.0（MVP 里程碑），发布顺序
     先 core 后主包（依赖声明 `datasentry-core>=0.1.0`）；
  2. **元数据面**：两包补 readme（PyPI 首页）/keywords/
     classifiers/urls（Homepage/Source/Issues）；
     `datasentry` 声明 Development Status Alpha 与 Python
     3.12/3.13 支持（requires-python >= 3.12）；
  3. **构建产物**：`uv build` 验证主包 + core 包 sdist/wheel
     四产物；构建物不入库（.gitignore 已有 dist/）；
  4. **干净环境验收**：全新 venv（Python 3.12）安装本地 wheel
     → `datasentry --version`、`scan`（6 issues / score 94.8）
     → `report export` HTML 全链路通过；元数据检查
     （keywords/classifiers/readme）通过 importlib.metadata；
  5. **变更记录**：CHANGELOG.md（Keep a Changelog 格式，
     0.1.0 汇总 Step 1–31）。
- **理由**：产品走向可用需可安装、可验证、可追溯的发布形态；
  双包隔离让 core 可独立消费（插件/扩展生态，ADR-031）；
  本地 wheel 冒烟在发布前捕获打包遗漏（本次发现 core 缺
  readme 导致构建失败即为例证）。
- **影响**：新增 CHANGELOG.md、packages/core/README.md；
  pyproject 元数据补齐；`uv build` 四产物验证通过；
  测试 408 例不变（发布面无代码逻辑变更）。

---

## ADR-033：README 产品化与开发笔记分离（发布面）

- **状态**：Accepted（2026-08-05）
- **决策**：
  1. **README 双层结构**：顶部为产品主页（为什么选择/快速
     开始/命令速查/架构总览/竞品位置/路线图），尾部为
     「开发附录（实施过程笔记）」——实施步骤记录保留可追溯，
     但不遮挡用户视角；
  2. **用户视角重写**：快速开始从 `uv sync && make check`
     改为 `pip install datasentry` + 一条命令扫描 + 报告 +
     门禁；LLM 辅助与插件作为可选路径单列；
  3. **竞品定位段**：vs pandas-profiling（只报不修）、
     vs Great Expectations（自动发现 + AI 生成规则 + 审批，
     免手写 expectation）、vs 商业 Observability（本地优先/
     数据不出机器/开源）——V2 云侧能力如实标注为路线图而非
     现状；
  4. **PyPI 首页一致性**：README 即 wheel 元数据 readme，
     重建 + 干净环境 importlib.metadata 校验产品化内容进入
     首页（hero/快速开始/路线图三锚点断言）。
- **理由**：0.1.0 发布工程（ADR-032）后 README 仍是开发笔记
  形态，与产品定位不符；双层结构同时满足用户与开发者两类读者。
- **影响**：README 头部重写（357 行，附录降级为子章节）；
  测试 408 例不变；双包 wheel 重建验证通过。

---

## ADR-034：场景示例的修复闭环语义（Step 34）

- **日期**：2026-08-08
- **状态**：Accepted
- **决策**：
  1. **门禁是修复闭环的最终裁决**：场景示例断言「修复前
     gate 拦截（passed=False）→ 修复后 gate 放行
     （passed=True）」，不强求质量总分单调上涨——`set_null`
     以缺失换错误，分数可能不升反降，属真实权衡而非缺陷；
  2. **修复副本链式推进**：修复引擎写副本不原地改文件
     （ADR-020 产物语义），复扫对象 = 上一轮修复副本
     （`.datasentry/repairs/<run_id>.csv`）；同轮修复互不
     叠加（每次 apply 均以本轮输入为源），轮末最后一个副本
     作为下一轮输入，直至无可修复（上限 3 轮防死循环）；
  3. **evidence.data JSON 序列化守卫**：DuckDB 将全合法日期
     列推断为 DATE 类型后，检测器把原始 `date` 对象写入
     evidence.data 导致 `save_scan` 崩溃——uniqueness_violation
     与 rare_category 统一经 `_json_safe`（date/datetime →
     ISO 字符串）后再落库；回归测试断言 JSON 往返无异常且
     无日期对象泄漏；
  4. **示例脚本可复现性**：固定种子 + 同 seed 两次运行输出
     逐行一致（排除 uuid/路径/耗时行），预算 180s 硬断言。
- **理由**：34 章场景 B/C 此前只有单文件 demo（Step 25）；
  电商双文件场景同时覆盖多文件扫描、门禁、修复闭环、报告
  导出四条产品主线，且暴露并修复了真实序列化缺陷。
- **影响**：新增 examples/ecommerce/run_showcase.py、
  tests/test_ecommerce.py（3 例）、tests/test_evidence_json.py
  （2 例）；核心包 2 个检测器文件修复；测试 408 → 413。

---

## 待定（Proposed）

| 编号 | 议题 | 状态 |
|------|------|------|
| — | （无待定项——V1 前置项全部落地） | — |

## ADR-035：契约驱动门禁与修复证据放行（Step 35）

- **状态**：已确认（Step 35）
- **背景**：22 章场景 C 的门禁此前只能经 `QualityGate` 硬编码
  在客户端代码里，数据契约（16 章）与门禁互不相通；且「修复
  后放行」只是示例脚本里的叙事，没有系统级证据机制。
- **决策**：
  1. **契约绑定门禁**：`Contract` 新增可选 `gate` 段
     （`fail_on` / `maximum_failed_rows_ratio` / `maximum_issues`，
     默认 `fail_on: high`）；`scan --contract` 载入后以契约
     门禁求值，命令行 `--gate` 可覆盖契约门禁；契约缺失/无效
     文件 → 退出码 2（配置错误），JSON 输出为 `contract
     validate` 失败 envelope。
  2. **修复证据即门禁豁免**：`QualityGate.require_repair_validation`
     MVP 落地——求值时间点（客户端调用或 `scan --validate`
     时）工作区存在已应用的修复运行（`repair_runs` 状态
     = applied）则追加豁免 reason，放行一次；无证据则拦截。
     纯粹状态化（不看 diff），零耦合修复引擎，保证「扫描→
     修复→门禁」闭环的产品语义可表达、可测试。
  3. **CLI 失败路径显式化**：`repair_apply` 失败的 issue
     过滤出 `failed_rules` 写入 RepairRun；退出码表补齐
     `EXIT_CONFIG=2`、`EXIT_GATE=3`。
- **理由**：契约是用户表达质量预期的自然入口，门禁随契约
  下发（含覆盖）后，"契约即门禁"语义完整；修复证据豁免让
  治理流程可解释——拦截原因 + 修复动作 + 放行依据均可查证。
- **影响**：`datasentry_core.models.contract` 新增 `QualityGate`
  嵌入 + `ContractGate` 装配；`cli.py` 契约载入/求值/退出码
  改造；新增 tests/test_gate.py（6 例）、test_repair_cli.py
  （5 例）、test_cli.py 契约三例；测试 413 → 422。

## ADR-036：JUnit / SARIF CI 报告导出（Step 36）

- **状态**：已确认（Step 36）
- **背景**：V1 交付物清单首项为 CI 集成（GitHub Action、
  JUnit/SARIF）。现有 report export 只产出 JSON/Markdown/HTML，
  无法接入 CI 测试汇总与 GitHub Code Scanning。
- **决策**：
  1. **JUnit XML**（`reporting/junit.py`）：一次 scan → 单个
     `<testsuite>`（name=`datasentry:<dataset_id>`）；issue 存在
     即质量问题 → 每个 issue 一个 failure testcase（type=严重度，
     message=title，body=severity/confidence/affected/detectors），
     errors 保留给平台级错误；properties.overview 放数据集概览。
     与门禁判定解耦（CI 阈值判定走 `scan --fail-on` 退出码，
     本导出只忠实呈现检测结果）。
  2. **SARIF 2.1.0**（`reporting/sarif.py`）：一次 scan → 一个
     run；issue_type 去重注册为 rules（defaultConfiguration.level
     按严重度），每个 issue → result（critical/high→error、
     medium→warning、low/info→note）；无行号证据时 location 落
     文件级（dataset_id 作 artifact uri），列名/受影响行数进
     message 与 properties；`automationDetails.id` = scan_run_id。
  3. **CLI 接入**：`report export --as junit|sarif [--output]`，
     与 json/markdown/html 同一输出路径约定（默认
     `.datasentry/reports/<run_id>.junit/.sarif`）。
- **理由**：CI 集成是 V1 首项交付，JUnit 满足测试汇总生态、
  SARIF 满足代码扫描生态；两个导出器均纯函数式、以已有
  26.2 规范报告为唯一输入，零状态零耦合。
- **影响**：新增 `reporting/junit.py`、`reporting/sarif.py`，
  `reporting/__init__.py` 导出便捷入口；CLI `--as` 选择扩到
  5 种；tests/test_reporting.py +9 例（XML 转义、空报告绿套件、
  规则去重、级别映射）、test_cli.py +1 例；测试 422 → 430。

## ADR-037：契约导出器（Pandera / Great Expectations）（Step 37）

- **状态**：已确认（Step 37）
- **背景**：V1 交付物清单「契约导出器（Pandera/GE）」。契约
  YAML 是质量声明的单一来源，但既有数据栈（Pandera 校验、
  GE ExpectationSuite）无法直接消费 DSL。
- **决策**：
  1. **零依赖导出**：`datasentry_core/contracts/exporters.py`
     纯函数式生成，不引入 pandera / great_expectations 运行时
     依赖——Pandera 输出为可直接复制执行的 Python 代码字符串
     （DataFrameSchema + pa.Column + pa.Check），GE 输出为
     完整 ExpectationSuite 文档（expectation_suite_name /
     expectations / meta）。
  2. **映射收敛**：列契约（nullable/unique/min/max/allowed_
     values/regex/format）与内嵌 checks（regex/range/
     allowed_values/not_null/unique）一一映射；顶层 Rule 中
     NOT_NULL/UNIQUENESS/VALUE_RANGE/ALLOWED_VALUES/REGEX
     可表达，CONDITIONAL_*/AGGREGATE/COLUMN_COMPARISON 不可
     表达——Pandera 侧降级为注释列出，GE 侧零贡献（不伪造
     语义）；语义类型 → Pandera dtype / GE type_ 映射表收敛，
     未知回退 object/String。
  3. **CLI 接入**：`contract export <path> --as pandera|ge
     [--output]`，默认落点 `<stem>.py` / `<stem>.ge.json`，
     契约无效 → 退出码 2，文件缺失 → 退出码 4。
- **理由**：保持「DSL 单一来源」的写入侧（validate/scan），
  导出侧让契约落地 Pandera（校验即代码）与 GE（套件即文档）
  两大生态；零依赖保证安装面不膨胀；不可表达规则显式降级
  而非静默丢失。
- **影响**：新增 `contracts/exporters.py` + `contracts/__init__.py`
  便捷入口；CLI `contract export` 子命令；tests/test_contract_
  export.py（10 例：结构/属性/规则映射/降级/GE envelope）+ CLI
  1 例；测试 430 → 441。

## ADR-038：DuckDB 文件连接器（Step 38）

- **状态**：已确认（Step 38）
- **背景**：MVP 把数据库型数据源（SQLITE/POSTGRESQL/DUCKDB）
  划归 V1（ADR-019）。数据团队的质量扫描对象常是库表而非
  导出文件；DuckDB 已是唯一执行引擎（ADR-005），本地
  `.duckdb` 文件是零依赖、可测试的切入点。
- **决策**：
  1. **连接器形态**：`connectors/duckdb.py` —— READ_ONLY
     ATTACH 文件 + `CREATE VIEW data AS SELECT * FROM src_db.<table>`，
     完全复用 FileDataHandle 共享实现（schema/read_sample/
     sql_aggregate/count_rows/fingerprint/warnings）；表名必填
     （spec.table_name），schema 名经 options 传入，标识符双引号
     转义（防表名/schema 名注入）。
  2. **流式执行原语**：`DuckDBExecutor.execute_stream(sql,
     batch_size)` —— to_arrow_reader 按批产出 RecordBatch，
     与现有 execute（整表 Arrow）并存，SQL 同过只读守卫。
  3. **接入面**：`.duckdb` 扩展名 → DUCKDB；`scan_file(..., 
     table_name=None)`（scan 时记录最近表名，repair_open 复用）；
     CLI `scan --table NAME`；缺表名 → DataSourceNotFoundError
     → 退出码 2（参数配置错误）；无表名校验进 open（supports
     只判格式，避免误导性「格式不支持」）。
  4. **默认注册表**：default_registry() 注册 DuckdbConnector。
- **理由**：数据库型数据源是 V1 交付物，「DuckDB 文件」形态
  成本最低且覆盖协议面（多表工作区的种子）；共享基类复用
  保证检测器层零改动；惰性视图 + 转义 + 只读 ATTACH 满足
  安全模型。
- **影响**：engine/duckdb.py +execute_stream（弃用
  fetch_record_batch → to_arrow_reader）；connectors/duckdb.py
  新增；registry/__init__/client/cli 接入；tests/test_duckdb_
  connector.py 12 例（协议/流式/schema 表/注入/registry/client/
  缺表名）；测试 441 → 453。

## ADR-039：漂移引擎（历史版本比较）（Step 39）

- **状态**：已确认（Step 39）
- **背景**：V1 交付物「漂移引擎 + 历史版本比较 + 趋势 UI」。
  drift 模型（18.2）MVP 已先行定义；scan 历史与 issues 已随
  每次扫描落库——比较引擎无需新存储，纯读历史即可。
- **决策**：
  1. **纯函数比较**：`drift/engine.py` `compare_scans(ref,
     cur, ref_issues, cur_issues)` → `DriftReport`，不落库、
     零状态。四类信号：
     - schema 变更：column_signature 逐列 diff，只报告可确证
       的 added/removed/dtype_changed/order_changed（renamed
       需相似度启发式，MVP 不伪造）；
     - 行数漂移：变化率 ≥ row_ratio_threshold（默认 20%）；
     - 质量漂移：overall 变化 ≥ score_threshold（默认 5 分），
       方向 decrease/increase；
     - issue 分布漂移：issue_type 计数增减——出现=新问题
       （HIGH）/消失=已解决，增/减方向。
  2. **接入面**：SDK `drift_compare` / `drift_latest`
     （最近两次 completed 扫描，不足两次 ValueError）；CLI
     `drift compare <a> <b>` / `drift latest <dataset>`，
     阈值可传参，缺扫描 → 退出码 2。
  3. **趋势 UI 归 V1 后续**：引擎先行，UI（跨扫描趋势图）
     待可视化迭代，README 注明范围。
- **理由**：漂移的核心价值在「版本间可解释差异」；比较引擎
  只依赖已有 scan 历史，交付风险最低，且为后续趋势 UI 提供
  唯一数据源。诚实边界（不伪造 renamed）保证信号可信。
- **影响**：新增 `drift/engine.py` + `drift/__init__.py`；
  client 两方法；CLI drift 子命令组；tests/test_drift_engine.py
  （10 例）+ tests/test_drift_integration.py（6 例）；测试
  453 → 469。

## ADR-040：跨表外键完整性检测（契约 references）（Step 40）

- **状态**：已确认（Step 40）
- **背景**：integrity 维度长期空缺（quality_score 断言 consistency 等
  为 None）；V1 清单含「跨表检测器 + 多表工作区」。检测器协议
  （11 章）限定单数据源句柄，跨表需新的数据通道。
- **决策**：
  1. **契约声明式引用**：`Contract.references: list[TableReference]`
     （name/path/table/schema/columns 主列→引用列），跨表关系随契约
     版本化管理，检测器零配置发现。`schema` 键因遮蔽 pydantic
     BaseModel 属性，字段名 `schema_name` + alias。
  2. **引用由调用方显式声明**：references 属调用方信任域，检测器
     自建只读 DuckDBExecutor（ATTACH/read_* 视图）执行 LEFT JOIN，
     属协议「检测器不得自行打开数据源」的显式例外（只在调用方声明
     引用时激活）；路径/标识符全转义防注入。
  3. **外键语义**：主表列非 NULL ∧ 引用表列无匹配（引用列 NULL 不
     参与匹配，LEFT JOIN ON 附加 IS NOT NULL）→ 孤儿行；列级 issue，
     evidence=CONSTRAINT_VIOLATION（孤儿数/比例/引用名），HIGH。
  4. **融合注册**：`foreign_key_violation` → family
     `integrity_constraint` → INTEGRITY 维度（此前无任何家族）。
  5. **接入面**：SDK `scan_file(references=...)`；CLI `scan --contract`
     自动透传契约 references。
- **理由**：跨表完整性是「质量」最硬的信号之一（孤儿行 = 业务断裂）；
  契约承载引用使多表工作区成为契约的天然扩展，而非新 API 面。
  自建 executor 换来了零侵入的单表架构不动，成本可控（只读、显式
  生命周期）。
- **影响**：契约模型 + DetectionContext.references + DataHandle
  Protocol.table_name（CSV 等返回 None）+ 新检测器（37 个）；
  fusion 新增 family/dimension/title 映射；测试 469 → 478（+9）。
  已知边界：XLSX 引用文件不支持、duckdb 引用需 table 名、多列
  映射逐列独立比较。

## ADR-041：模糊重复检测器（uniqueness Level 3）（Step 41）

- **状态**：已确认（Step 41）
- **背景**：V1 清单「模糊重复（Level 3）」。精确重复（uniqueness_violation）
  无法捕获大小写/空白/标点变体——脏数据最常见的重复形态。
- **决策**：
  1. **归一化分组（SQL 下推）**：`lower(regexp_replace(col,
     '[^0-9A-Za-z\u4e00-\u9fff]', '', 'g'))` 归一化后 GROUP BY。
    选择字符类归一化而非编辑距离：O(n) 可下推、确定性强、
     中文天然支持；编辑距离两两比较 O(n²) 且需采样，MVP 不做。
    注意 duckdb regexp_replace 需 'g' flag 才全局替换（踩坑记录）。
  2. **判定**：组大小 ≥ 2 ∧ 组内 DISTINCT 原始值 ≥ 2 ∧ 归一化键
     长度 ≥ 2（中文名 2 字常见，阈值不设 3）。可去重行数 =
     Σ(组行数 − 1)，即每组保留一行。
  3. **输出**：列级 issue，evidence=DUPLICATE_MATCH（归一化键 +
     原始样例 ≤ 3）；置信 0.9 / FPR 0.15（归一化可能误并，如
     「AC/DC」类有语义的标点差异）；融合并入既有 uniqueness 家族
     （标题统一，维度归 UNIQUENESS，不新建 family）。
  4. **范围**：仅字符串列（VARCHAR 族），组上限 50 防长尾。
- **理由**：字符类归一化以最低复杂度覆盖最高频的模糊重复形态，
  全量可下推、无采样噪声；与精确重复共用 uniqueness 家族保证
  融合/评分语义一致。
- **影响**：检测器 37 → 38；FAMILY_MAP 加 fuzzy_duplicate→
  uniqueness；测试 478 → 484（+6）。
  已知边界：不处理拼写变体（level-3 语义相似），仅归一化变体；
  跨列复合键重复（两列联合）不在本检测器。

## ADR-042：模型异常检测器（IF/LOF）（Step 42）

- **状态**：已确认（Step 42）
- **背景**：V1 清单「Isolation Forest / LOF」。数值异常现有统计法
  （IQR/z-score/百分位/直方图）假设分布形态；模型法捕获任意形状
  分布中的偏离点，且 distribution_stability 维度此前无检测器。
- **决策**：
  1. **单列单变量建模**：每数值列独立 fit_predict。IsolationForest
     默认（n_estimators=100），LocalOutlierFactor 可选
     （ScanConfig.detector_params["model"]）。
  2. **显式 contamination=0.02**：sklearn "auto" 用 MCD 估计，对
     单变量数据系统性过度标记（实测 200 正态点标 13 个，6.4%，
     远超直觉），显式小值 + anomaly_ratio 上限 5% + min_anomalies 3
     三重护栏。
  3. **物化路径**：capabilities 标 supports_sampling +
     requires_row_materialization（非 SQL 下推，打破全局
     pushdown 假设的既有测试断言，改为结构断言）。
  4. **模型信号 = LOW 提示**：confidence 0.7 / FPR 0.3，severity
     LOW——模型输出是「值得人工看」的信号，不是确证异常；样例
     异常值进 evidence 便于人工复核。
  5. **性能**：>20k 行采样（seed 可复现）；LOF n_neighbors 随
     样本自适应。
  6. **新 family** `distribution_anomaly` → DISTRIBUTION_STABILITY
     （fusion 三表同步扩展）。
- **理由**：与统计法互补而非替代；把模型不确定性诚实编码进
  严重度与置信；采样+护栏保证大表可用性。
- **影响**：新检测器（39 个）+ fusion 家族映射；core 新增
  scikit-learn≥1.5 依赖（IF/LOF 标准实现，不手写）；ScanConfig
  新增 detector_params 通道；测试 484 → 491（+7）。

## ADR-043：MCP stdio 服务器（Step 43）

- **状态**：已确认（Step 43）
- **背景**：V1 清单「MCP Server」。LLM 生态（Claude Code 等）经
  MCP 挂载工具是 Agent 化数据质量的接入面；项目已有 CLI/REST
  双面，MCP 是第三面。
- **决策**：
  1. **零依赖自实现**：JSON-RPC 2.0 over stdio（每行一个 JSON），
     实现 MCP 2024-11-05 核心子集——initialize /
     notifications/initialized / ping / tools/list / tools/call。
     不引入 mcp SDK：协议子集小且稳定，自实现 ~300 行可控，
     与 SafeExpressionEvaluator、drift 引擎同风格。
  2. **单工作区门面**：McpServer 持有一个 DataSentry 实例（与
     REST create_app 同构），工具复用 SDK 方法，CLI/REST/MCP
     三面同源。
  3. **工具面**：scan_file / list_issues / quality_score /
     drift_compare / drift_latest / detectors_list /
     contract_validate——覆盖「扫描→查询→比较→验证」闭环，
     工具 schema 手写 JSON Schema（type/properties/required）。
  4. **JSON 安全**：datetime/Path/set 统一 _json_safe 序列化；
     工具内部异常映射 -32603，未知工具 -32602，未知方法
     -32601；notification 无响应（id 缺失不回复）。
  5. **CLI 集成**：`datasentry mcp` 子命令（--project）。
- **理由**：零依赖换取协议子集锁定（不在依赖升级中漂移）；
  与 SDK 同源保证行为与 CLI/REST 一致；测试含真实子进程
  stdio 循环，协议符合性可回归。
- **影响**：新增 src/datasentry/mcp_server.py + CLI 子命令；
  测试 491 → 502（+11）。
  已知边界：只实现 tools 能力（无 resources/prompts）；
  单进程阻塞读 stdin，无并发（MCP 客户端串行调用工具）。

## ADR-044：AI 修复候选（Step 44）

- **状态**：已确认（Step 44）
- **背景**：V1 清单「AI 修复候选」。RepairEngine.propose 仅对 5 类
  issue 给确定性提案；其余 issue 无修复建议。LLM 面（rules_ai）
  已有 38 章脱敏 + 审计骨架可复用。
- **决策**：
  1. **上下文白名单操作集**：AI 只能从 `_CONTEXT_OPS`（按 issue
     detector_ids 派生，与规则引擎 `_PROPOSAL_MAP` 同款 5 操作）
     中选择——LLM 不新增表达式能力，杜绝任意 SQL/代码注入。
  2. **参数白名单**：仅 clip_value 接受数值 lower/upper（且
     isfinite + lower<=upper 校验），其余操作强制空参数；与
     `RepairEngine._after_expr` 支持面严格一致（语义复用 engine
     的 `_after_expr`，不复制实现防漂移）。
  3. **全流程安全**：画像 + 样例整体脱敏（mask_profile，映射表
     不落盘）；evidence 摘要中 str 值就地 `{{REDACTED}}`；
     llm_cache + llm_invocations 审计（task_type=repair_candidate）；
     JSON 严格 pydantic 校验；受影响行数落库前真实估算（<=0 拒绝）。
  4. **落库语义**：候选 save_repair_proposal（status=proposed），
     与规则提案对称；apply 仍走确定性引擎（AI 候选是顾问性质）。
  5. **CLI**：`repair propose --ai`；未配置 LLM → LLMNotConfiguredError
     清晰提示（与 rules propose 同行为）。
- **理由**：把「修复建议」从确定性枚举扩展到 LLM 兜底，同时把
  注入面锁死在 5 操作 + 2 参数内；复用已有脱敏/缓存/审计设施，
  不新建机制。
- **影响**：新增 src/datasentry/repair_ai.py + client.repair_propose_ai
  + CLI `--ai`；测试 502 → 514（+12）。
  已知边界：AI 候选不直接 apply（apply 由确定性引擎重新 propose）；
  复杂操作（impute/map_category 等）未开放给 LLM。

## ADR-045：跨扫描趋势 UI（Step 45）

- **状态**：已确认（Step 45）
- **背景**：V1 清单「趋势 UI」。漂移引擎（Step 39）已有完整信号，
  Web UI（Step 24）只有单扫描视图；「跨扫描趋势」是 V1 收官项。
- **决策**：
  1. **纯函数数据层**：`trends.build_trends(scans)` 只消费 ScanRun
     列表（quality_score 随扫描落库，历史保留原权重与 score_version），
     输出每数据集的时间升序 ScanPoint 序列 + delta/direction/latest
     汇总——不碰 store、可离线测试。
  2. **过滤语义**：只收 status=completed 且 quality_score 非空；
     非 completed/未打分扫描不进趋势（与 drift 引擎口径一致）。
  3. **方向阈值**：delta >= +0.5 → up，<= -0.5 → down，否则 flat
     （展示用 0.5，与 drift 的 score_threshold=5.0 是不同层语义）。
  4. **渲染层**：ui.render_trends 复用 _page/_CSS（XSS 转义一致），
     内联 CSS 条形图（宽度=score%）+ 历史表 + 徽章，零 JS 依赖；
     nav 增加 Trends 入口；`/ui/trends` 路由在 api.create_app 内。
  5. **与漂移引擎分工**：趋势页是轻量概览（score/issues 序列）；
     行数/覆盖/异常等完整信号仍走 drift compare/latest，不重复实现。
- **理由**：质量分随 ScanRun 落库使趋势可纯函数推导（零新存储）；
  纯函数 + 服务端渲染保持项目零前端构建链风格；UI 与漂移引擎
  边界清晰不重叠。
- **影响**：新增 src/datasentry/trends.py + /ui/trends 路由 +
  render_trends；测试 513 → 522（+9）。

## ADR-046：V1 发布收尾（Step 46）

- **状态**：已确认（Step 46）
- **背景**：V1 功能收官（Step 45）后发现 dist/ 中 datasentry_core
  wheel 是 Step 40 之前的旧产物（缺 TableReference 等新模型），
  隔离安装冒烟直接 ImportError —— 发布工程（Step 32）只做过一次
  本地构建，没有可复现的构建流程与 CI 验证。
- **决策**：
  1. **CHANGELOG 补齐**：[0.1.0] 条目扩展覆盖 Step 32–45（漂移/
     跨表/AI 修复/MCP/趋势 UI 等 14 项），日期更新至发布日。
  2. **Makefile build 目标**：`make build` = `uv build` + 
     `uv build packages/core`——双包固定顺序构建，消除
     workspace 单包构建漏包问题。
  3. **CI 第 11 阶段**：wheel 构建 + 隔离 venv 安装 + CLI 冒烟
     （scan 真实执行），此后任何 commit 的 wheel 可安装性都受门禁。
- **理由**：发布物必须是 CI 验证的产物而非一次性本地命令；双包
  结构要求构建步骤显式覆盖 workspace 成员。
- **影响**：CHANGELOG + Makefile + CI；本地重建 dist 双 wheel
  并通过隔离安装冒烟（datasentry 0.1.0 + core 0.1.0）。

## ADR-047：开源呈现（Step 47）

- **状态**：已确认（Step 47）
- **背景**：项目将推送到 GitHub 开源，需要开源门面：
  仓库首页（README）与项目主页（GitHub Pages）的呈现形式。
- **决策**：
  1. **README 开源化**：技术开发笔记移入 docs/DEVELOPMENT.md，
     README 重写为英文为主的开门结构——徽章行（版本/许可证/
     Python/CI）、一句话定位 + 中文导读、功能矩阵、Mermaid
     架构图、快速开始、真实 Demo 报告截图 + 链接、文档索引、
     贡献指南（强调 human-in-the-loop 不变式）。
  2. **GitHub Pages 主页**：docs/index.html 自包含单文件（无
     CDN/构建链，深色 GitHub 风格），Hero + 能力卡片 + 真实
     Demo 报告 iframe（10KB 自包含 HTML，零外部资源）+ 快速
     开始终端块 + 文档链接；Pages 由 pages.yml workflow
     （actions/configure-pages + deploy-pages）部署 docs/。
  3. **真实素材优先**：Demo 报告是真实扫描产物（200 行注入
     15 类问题的订单数据 → report export --as html），非手写
     示例；缩略图由 qlmanage 生成，README 直接引用。
  4. **品牌**：docs/assets/logo.svg 手写 SVG（盾牌 + 数据条 +
     对勾 + 状态点），复用 GitHub 蓝绿品牌色系。
- **理由**：开源项目的信任来自「真实产物 + 可复现命令」；
  零构建链的静态站点与项目本地优先哲学一致；docs/ 目录
  已有（设计文档），Pages 直接发布 docs/ 零迁移成本。
- **影响**：README 重构、docs/index.html + pages.yml +
  logo + demo 素材。待办：推送后替换 docs/index.html 中的
  GitHub 链接占位与 README 徽章 URL。

## ADR-048：PII 加密还原（V2-A，AES-GCM 可逆脱敏）（Step 48）

- **状态**：已确认（Step 48）
- **背景**：38 章脱敏（ADR-027）是**不可逆**的：`mask_profile` 的
  映射表只在进程内传递、不落盘，LLM 回复中引用掩码值时（如修复
  rationale、规则 when 值）原文丢失。V2 计划「脱敏时生成加密映射
  并安全存储，LLM 回复后还原」——不出机器 + 信息不丢 + 可审计还原。
- **决策**：
  1. **AES-256-GCM**（cryptography 库 `AESGCM`，仅主包新依赖，
     core 零依赖）：密钥材料任意字符串，sha256 派生 32 字节；
     密文 = base64(nonce 12B || ciphertext || tag)，nonce 随机。
  2. **密钥来源优先级**：`DATASENTRY_ENCRYPTION_KEY` env →
     `<user-config>/datasentry/vault.key`（0600，rotate 写入）→
     内置开发密钥（key_source=dev，CLI 显式告警，仅本机开发）。
  3. **持久化**：schema v3 新增 `pii_mappings` 表
     （session_id/ciphertext/key_version/created_at，明文不落盘）；
     `llm_invocations` 增 `pii_session_id` 审计列（幂等迁移）。
     session_id 由映射内容确定性派生（sha256 前 16 hex）：
     同一映射复用同一加密会话，与 llm_cache 语义一致。
  4. **还原闭环**：`repair_ai` / `rules_ai` 的 propose 在脱敏后
     加密落库 mapping；LLM 输出的占位符经 `vault.restore_text` /
     `restore_value`（递归 dict/list）还原后落库（rationale、
     description、when 值）；每次还原动作写审计
     （task_type=pii_restore），密钥轮换审计 pii_key_rotate。
  5. **缺密钥优雅降级**：解密失败（密钥丢失/不匹配/数据损坏）
     统一抛 `VaultKeyMissingError`，拒绝还原并提示配置 env 或
     执行 rotate-key；不静默降级、不泄露部分明文。
  6. **报告默认打码**：HTML/Markdown 人类可读输出对自由文本
     字段过 `mask_text_pii`（命中 PII → `[REDACTED]`，复用 38 章
     识别正则）；JSON 机器契约保留完整证据链（本地文件、无网络
     传输）。UI 标题同样防御性打码。
  7. **CLI**：`llm restore`（列表 / 会话摘要含掩码→原文预览 /
     `--text` 还原 / `--delete`）、`llm rotate-key [--new-key]`
     （重加密全部映射 + 写 key 文件）、`llm status` 增 pii_vault
     状态（key_source / mappings 数）。
- **理由**：AES-GCM 是标准认证加密原语（密文带 tag 可验篡改），
  与「本地优先 + 不出机器」哲学一致；确定性 session_id 使加密层
  与 llm_cache/审计自然对齐；打码策略区分人类可读面（默认打码）
  与机器契约面（完整证据），避免破坏证据链。
- **影响**：schema v3（迁移幂等）；新增 src/datasentry/pii_vault.py
  + store pii_mappings CRUD；repair_ai/rules_ai 改造（构造可注入
  vault，默认从 store 构建，向后兼容）；reporting 增 mask_text_pii；
  pyproject 主包加 cryptography 依赖；测试 522 → 553（+31）。
  已知边界：dev 密钥不落盘（进程重启后 dev 加密数据仍可解——
  密钥是常量）；还原仅覆盖 AI 输出引用（不覆盖文件本身）。

## ADR-049：HTML 报告交互（V2-B，筛选/排序/分页/趋势，纯原生 JS）（Step 49）

- **状态**：已确认（Step 49）
- **背景**：26 章 HTML 报告是静态快照（审计驱动、零外部链接、单文件）。
  V2 计划升级为可交互：severity/维度筛选、列排序、issue 详情折叠展开、
  分页、迷你趋势图（复用 trends.py 数据）；server 模式下报告联动 REST
  API（点击 issue 跳转修复工作台）。
- **决策**：
  1. **零依赖原生 JS，全部内联**：交互逻辑内嵌 `<script>`（无前端框架、
     无外链、离线可用）；动态数据以 `<script type="application/json">`
     内嵌，经 `json_script` 转义（`<`/`>`/`&` → `\uXXXX`），杜绝
     `</script>` 注入；JS 只以 `textContent` 写单元格，severity 样式类
     经白名单映射，双保险防 XSS。
  2. **可测纯函数层**（`reporting/interactive.py`）：`issue_rows`
     （视图模型，title/description PII 掩码）、`filter_issues`（severity /
     维度 / 搜索，大小写不敏感）、`sort_issues`（priority / severity rank /
     affected / title）、`paginate`（page 越界钳制）与 JS 行为一一对应，
     作为单测与快照断言的语义参照（验收要求）。
  3. **迷你趋势图**：`render_trend_svg` 消费 trends.py 序列化结构
     （`DatasetTrend.to_report_dict()`）生成内联 SVG 折线；`render_html`
     增可选参数 `trends`（列表）——core 不反向依赖应用层，CLI/API
     注入数据。不足两点不渲染。
  4. **server 联动可选**：`render_html(..., server_base_url=...)` 非空时
     JS 为每行生成「workbench」链接（跳转修复预览端点）；默认 None =
     纯离线报告，无任何链接。新增 `GET /scans/{run_id}/report.html`
     （注入请求 base_url + 趋势）。
  5. **报告仍是审计产物**：元数据/方法/可复现节保留，交互仅视图增强；
     JSON 机器契约不变。
- **理由**：交互逻辑与数据分离（JS 只渲染），Python 纯函数提供可测试的
  同一语义；转义策略复用 Step 48 的「人类可读面打码」原则；无新依赖、
  不破坏现有 CLI 行为（全部新参数可选），符合 V2 边界。
- **影响**：新增 reporting/interactive.py（纯函数 + JS 常量）；html.py
  集成交互表格/趋势区/CSS；trends.py 加 to_report_dict；cli.py report
  export html 注入趋势；api.py 加 report.html 端点；测试
  tests/test_reporting_interactive.py（新增 ~30 例）+ test_reporting.py /
  test_api.py 更新。

## ADR-050：插件 entry point 自动发现（V2-C，importlib.metadata entry points）（Step 50）

- **状态**：已确认（Step 50）
- **背景**：V2-C 目标为第三方 detector / 报告格式 / 连接器 / 修复操作
  可插拔且不侵入核心。已有 `plugins/` 目录复制即用机制（Step 31，
  ADR-031），但目录插件不进包、不可依赖第三方库、无法 pip 分发；
  需统一发现机制与插件协议。
- **决策**：
  1. **entry points 自动发现**：core 用 `importlib.metadata.entry_points`
     扫描 `datasentry.detectors` 组（未来可扩展 `datasentry.reporters` /
     `datasentry.connectors`）；`PluginDiscoveryReport` 汇总
     loaded / failed / errors，逐个失败优雅降级（缺失依赖等错误记录
     并继续，不崩整个扫描）；目录插件（Step 31）与内置检测器保持
     原样，entry points 是叠加层。
  2. **三形态 entry 值**：entry 值可为 Detector 实例 / 无参类 /
     无参工厂（`lambda: ...`），统一 `_coerce_entry_value` 收敛；
     陷阱：`isinstance(cls, Detector)`（runtime-checkable Protocol）
     对类对象误判，必须先判 `isinstance(value, type)` 再实例化。
  3. **来源标记**：注册表快照/`plugin list`/`list_detectors` 输出
     `source` 字段（`builtin` / `dir` / `entrypoint`），供 CLI/UI
     展示与审计。
  4. **示例插件包**：`examples/plugins/datasentry-sample-detector`
     独立 pyproject（依赖 `datasentry-core`），声明 entry point，
     以 `uv pip install -e` 演示安装 → 自动发现 → 扫描自动启用
     （扫描 run 数 = 内置 + 插件，无需配置）。
  5. **安全边界**：仅加载已安装分发（entry points 本身就是白名单
     安装物），不执行任意代码路径；插件接口保持最小（复用
     `Detector` Protocol 与 `DetectorMeta`）。
- **理由**：entry points 是 Python 标准机制（`uv pip install -e`
  即注册），零核心改动、无新依赖；优雅降级保证第三方插件缺陷
  不拖垮核心扫描；来源标记满足审计诉求（哪些插件在跑、来自哪）。
- **影响**：packages/core/src/datasentry_core/plugins.py 新增
  `discover_entrypoint_detectors` / `PluginDiscoveryReport`；
  cli.py `plugin list` 增 `--format json` + source/errors；
  client.py `list_detectors()` 增 source 字段；示例插件包 +
  README；测试 tests/test_plugins.py（发现/注册/CLI）+
  tests/test_sample_plugin.py（包形态，新增 ~25 例）。

## ADR-051：本地调度器（V2-D，cron + SQLite 队列 + worker + webhook）（Step 51）

- **状态**：已确认（Step 51）
- **背景**：V2-D 目标为定时/远程执行扫描与质量门禁、多项目编排、
  结果汇聚。边界明确：**先做本地调度器**（cron 表达式 + SQLite
  持久化任务队列 + 简单 worker 循环，跑在 datasentry-server 内），
  不做分布式/K8s/Celery，留执行器抽象接口。
- **决策**：
  1. **cron 语义用 croniter**：5 字段 cron，注册/更新时校验
     （非法表达式 422 拒绝）；`next_run` 计算与「重试间隔」分离。
     croniter 为纯 Python 零传递依赖，属轻依赖。
  2. **任务队列 = 独立表 + 同一 SQLite**：schema v4 新增
     `scheduled_jobs`（任务定义 + cron + retry + webhook + 状态机）
     与 `job_runs`（attempt 级执行记录），复用 core schema 迁移
     （`migrate`）保证 DDL 同源；任务随 metadata.db 持久化，
     服务重启后任务/执行历史不丢。
  3. **状态机**：idle → running → idle（成功）→ dead（重试耗尽）。
     失败且 attempt ≤ retry_attempts → 60s 后重试（固定间隔）；
     超过 → 死信（dead，last_result 保留错误）。成功 → 按 cron
     计算下一次 next_run_at。
  4. **并发互斥 = SQLite 原子抢占**：`claim_due_jobs` / `claim_job`
     在 `BEGIN IMMEDIATE` 事务内「条件更新」抢占（enabled 且
     非 running 且到期），SQLite 单写者锁保证同一任务同一时刻
     只有一个执行者；手动触发执行中返回 409。
  5. **重启恢复**：startup 时 `recover_interrupted` 把 running
     任务置回 idle、run 标记 failed/interrupted，下次 tick 重调度。
  6. **ScanExecutor 协议**（扩展点）：`execute(JobCommand) -> JobResult`；
     默认 `LocalScanExecutor`（新建 DataSentry 执行 scan_file）；
     未来可换云函数/SSH 远端，调度器不感知。
  7. **webhook 可关**：job.webhook_url 为空即不通知；执行结束
     （成功/失败）POST 结果 JSON，失败仅记日志（尽力而为，
     不阻塞调度、不重试）。
  8. **worker 线程**：FastAPI startup 起 daemon 线程循环 tick
     （1s 间隔 + 可停 Event），shutdown 优雅退出；tick 抛异常
     不退出 worker。
- **理由**：SQLite 单写者锁天然提供跨进程互斥与持久化，避免引入
  消息队列/分布式依赖；状态机收敛为两表 + 原子抢占，可纯函数测试；
  执行器抽象满足 V2-D「未来可换云函数」边界而不过度设计。
- **影响**：schema v4（scheduled_jobs/job_runs + 索引）；新增
  src/datasentry/scheduler/{models,store,core}.py；api.py 增
  /jobs 端点族（POST/GET/PATCH/DELETE + trigger）+ lifespan worker；
  pyproject 增 croniter；测试 tests/test_scheduler.py（19 例）+
  tests/test_api_jobs.py（14 例）。

## ADR-052：调度质量门禁 + MCP 调度工具（V2-D 收尾，Step 52）

- **状态**：已确认（Step 52）
- **背景**：V2-D 声明含「定时/远程执行扫描**与质量门禁**」，Step 51
  只交付了调度执行，门禁语义悬空；同时 MCP 工具面只有扫描/查询类，
  调度（jobs）对 LLM 代理不可达，与 REST `/jobs` 不对等。
- **决策**：
  1. **门禁是业务判定，不是执行失败**：`scheduled_jobs` 增
     `gate_quality_min`（0-100，NULL=关）。run 完成后若
     `score.overall < gate_quality_min` → run 状态仍 completed
     （任务不重试、不进 dead），仅 summary 与 webhook 载荷带
     `gate: {passed, min, score}`；`passed=false` 由下游
     （webhook/代理）决定处置，调度器不越权。
  2. **契约约束**：JobCreate/JobUpdate 校验 `0 ≤ gate_quality_min
     ≤ 100`（越界 422）；PATCH 语义与 webhook_url 一致——传
     None = 字段不变（API 层不支持显式清空，避免 PATCH 二义性）。
  3. **MCP 调度工具**：mcp_server.py 新增 `jobs_list` / `job_create`
     / `job_trigger` 三个工具，与 REST `/jobs` 同源（直接复用
     SchedulerStore + Scheduler + LocalScanExecutor，经
     `project_db_path(workspace)` 落同一 metadata.db），`job_create`
     非法 cron 返回 ok:false + 错误文案而非抛异常（工具面友好）。
- **理由**：门禁按「判定不阻断」设计使状态机零改动、webhook 语义
  清晰；MCP 复用同一存储/执行路径保证 LLM 与 REST 看到一致状态，
  避免在 MCP 面再造一套调度 API。
- **影响**：schema v5（scheduled_jobs.gate_quality_min，DDL +
  migrate `version<5` 补列）；scheduler/{models,store,core}.py
  （GateResult、evaluate_gate、view() 透出）；api.py 透传；
  mcp_server.py +3 工具；测试 tests/test_scheduler.py 25 例 +
  tests/test_api_jobs.py 18 例 + tests/test_mcp_server.py 15 例。

## ADR-053：变更感知增量调度（文件哈希缓存）（Step 53，V2-D 后置）

- **状态**：已确认（Step 53）
- **背景**：调度器按 cron 反复扫描同一数据文件，内容未变化时重复
  全量扫描浪费算力并制造大量重复 issue 记录；门禁/告警也被无意义
  地反复触发。目标：仅当内容变化才真正重扫、重判门禁。
- **决策**：
  1. **文件级 SHA-256 缓存**：`job_runs` 增 `file_hash` 列（schema
     v6）。每次执行前流式计算目标文件哈希（1MiB 块读取，大文件
     友好）；与「最近一次成功且未跳过」的 run 哈希一致 → 本轮
     **跳过**：不建 scan_run、不执行扫描器、不重判门禁。
  2. **skipped 是记录不是状态**：跳过仍写入一条 completed run
     （`skipped=1`、`scan_run_id=NULL`、summary 与 webhook 载荷带
     `skipped:true` + `file_hash`），next_run_at 照常按 cron 推进，
     任务状态 idle。门禁字段缺席（gate:null）——无新扫描即无新判定。
  3. **跳过基准只看"成功且未跳过"**：`last_successful_hash` 查询
     排除 skipped 与 failed 记录，避免「跳过自身」成为基准（防
     一旦误判跳过便永久跳过）。
  4. **失败兜底**：文件缺失/不可读（OSError）→ 不跳过，走正常
     执行路径（让既有错误处理决定重试/死信）。
  5. **显式触发同样生效**：手动 trigger 与 tick 走同一 `_run_job`
     判定——重复触发同内容文件第二次返回 skipped run（202）。
- **理由**：哈希比对在调度层完成（执行前），执行器无需感知跳过
  语义，保持 `ScanExecutor` 抽象纯净；跳过作为 run 记录保留完整
  审计轨迹（何时跳过、跳过几次、基准哈希）；webhook 载荷携带
  skipped 供下游（代理/告警）去重。
- **影响**：schema v6（job_runs.file_hash + skipped，DDL + migrate
  `version<6` 补列）；scheduler/{models,store,core}.py（JobResult/
  JobRun 字段、`last_successful_hash`、`file_sha256`、`_finish_skipped`）；
  API/MCP 视图自动透出（JobRun.view）；测试 tests/test_scheduler.py
  TestChangeAware 5 例 + tests/test_api_jobs.py TestChangeAwareApi 2 例。

## ADR-054：SQLite 数据源连接器（V3 多数据源第一落点）（Step 54）

- **状态**：已确认（Step 54）
- **背景**：V3 蓝图「多数据源」——scan 目前只覆盖本地文件
  （CSV/Parquet/JSONL/XLSX/DuckDB）。`DataSourceType` 早预留
  SQLITE/POSTGRESQL 枚举与 `connection_ref` 字段但无连接器实现。
  第一步落地零依赖可读的 SQLite 文件源（`.db`/`.sqlite`/`.sqlite3`）。
- **决策**：
  1. **经 DuckDB sqlite 扩展读 SQLite**：`SQLiteDataHandle(FileDataHandle)`
     在 `_ensure_view` 里 `LOAD sqlite` + `sqlite_scan(path, table)` 注册
     只读 data 视图——schema/read_sample/sql_aggregate/count_rows/
     fingerprint/warnings 全部复用共享实现，零专用代码。
  2. **表名必填**：与 DuckDB 连接器同语义（sqlite_scan 需要表名），
     缺失抛 DataSourceNotFoundError（REST 404，detail 提示 table_name）。
  3. **路径/表名字符串字面量转义**（单引号翻倍），防 SQL 注入；
     文件缺失在 open 时报错（not found）。
  4. **扩展名映射进 client**：`.db`/`.sqlite`/`.sqlite3` →
     SQLITE；REST `POST /scans` 请求体增 `table_name` 字段透传。
  5. **调度器天然联动**：JobCommand 已有 table_name，LocalScanExecutor
     透传——SQLite job 直接可调度，Step 53 文件哈希缓存同样生效
     （表内容变更 → 文件字节变更 → hash 变化 → 重扫）。
  6. **Postgres 仍预留**：枚举与 connection_ref 不动，等凭据管理
     成熟再接（不引入 psycopg2 依赖）。
- **理由**：DuckDB 已是核心依赖且自带 sqlite 扩展，比纯 sqlite3 自研
  handle 少写 80% 代码并保持 fingerprint/抽样等语义一致；只读视图
  保证不写回源库。
- **影响**：新增 packages/core/.../connectors/sqlite.py
  （SQLiteDataHandle + SqliteConnector）；registry 默认注册；
  client.py 扩展名映射；api.py ScanRequest.table_name + 连接器异常
  映射（DataSourceNotFoundError→404、其余 ConnectorError→400）；
  测试 tests/test_sqlite_connector.py 11 例 + api 2 例 + 两处既有
  注册表断言更新。

## ADR-055：PostgreSQL 数据源连接器（V4 多数据源第二落点）（Step 55）

- **状态**：已确认（Step 55, V4）
- **背景**：V4 蓝图——打通 `DataSourceType.POSTGRESQL` 预留位，让
  `scan postgresql://...` 与 `--table` 直达 PostgreSQL 库表，复用
  全部检测器/评分/报告/门禁/修复闭环/漂移能力，与 SQLite（V3）
  并列为多数据源路线完整落地。ADR-054 决策 6「Postgres 仍预留，
  等凭据管理成熟再接」——V4 正面回应：凭据以「CLI 参数/环境变量
  引用（DATASENTRY_ 前缀）」为 MVP 语义，DSN 只走内存态，**依旧
  不引入 psycopg2 依赖**。
- **决策**：
  1. **经 DuckDB postgres 扩展读 PostgreSQL**（方案 A，开工前实测
     通过）：`LOAD postgres` + `ATTACH dsn AS pg (TYPE postgres,
     READ_ONLY)` 注册只读 libpq 视图，`PostgresDataHandle(FileDataHandle)`
     复用 schema/read_sample/sql_aggregate/count_rows/warnings 全家桶；
     表名必填（缺表名抛 DataSourceNotFoundError→404），schema 名经
     options["schema"]，标识符/DSN 字面量双引号/单引号转义。
  2. **凭据红线**：DSN 仅存内存态（spec.options["dsn"]）或
     connection_ref 指向的环境变量名（如 DATASENTRY_PG_DSN）；不落库、
     不进日志/evidence/报告。`_RedactingExecutor` 包装 DuckDB 执行器：
     DuckDB 异常文本含 DSN/密码时先净化（DSN→`postgresql://***`、
     密码→`***`）再转 ConnectorError 传播——CLI 错误面（退出码 4）与
     REST 400 只见净化文本。连接失败/扩展加载失败给可操作提示。
  3. **无文件字节 → 内容指纹**：PG 表没有文件，Step 53 文件 SHA-256
     跳过判定永不生效。`content_fingerprint()`（DataHandle 协议新增
     成员）= 单查询全表哈希：每行 `md5(concat_ws(chr(31), coalesce(col
     ::VARCHAR, chr(0))...))` 聚合 `string_agg(..., ORDER BY rh)`（行序
     无关、NULL 不折叠），并入 schema_hash 与行数得 SHA-256。调度器
    `_source_fingerprint` 源感知：文件源走文件哈希（原语义），PG 源
     走内容指纹——同内容 skipped、内容变更重扫；源不可达返回 None
     不误跳过（走正常失败路径）。`last_successful_hash` 字段本身是
     TEXT，两侧指纹同字段落库，零 schema 变更。
  4. **SDK/REST/CLI/MCP 接入**：`client.scan_file` 对 `postgresql://`/
     `postgres://` 前缀识别为 POSTGRESQL 源（DSN 进 options），
     `--table`/table_name 必填语义沿用 DuckDB/SQLite；CLI 新增
     ConnectorError→退出码 4（源不可用）；MCP scan_file 工具描述同步。
  5. **类型归一化**：DuckDB postgres 扩展返回的 DECIMAL(n,p) 与
     TIMESTAMP WITH TIME ZONE 等物理类型不命中检测器/画像的精确
     类型集合 → FileDataHandle.schema 统一 `_normalize_physical_type`
     （DECIMAL(n,p)→DECIMAL、别名时间→规范名）；evidence 序列化
     兜底 Decimal→float（PG 聚合实测触发，JSON 契约一次放行）。
  6. **集成测试与 CI**：真实 PG 集成用例（integration marker）经
     DuckDB postgres 扩展读写 ATTACH 建表灌数（不引入 psycopg），
     本地无 PG/离线时连接探测失败自动 skip——本地与无 PG 的 CI
     环境保持全绿；CI test job 加 postgres:16-alpine service +
     TEST_POSTGRES_DSN env 跑集成用例。
- **理由**：DuckDB postgres 扩展是唯一同时满足「零新依赖（AD-054
  约束）」「复用 FileDataHandle 共享实现」「只读 ATTACH 不写回源库」
  三条硬约束的路径；内容指纹查询是单语句 pushdown 的轻量聚合，
  远程源 MVP 不做增量采样优化（ADR-055 边界）。
- **影响**：新增 connectors/postgres.py（PostgresConnector +
  PostgresDataHandle + _RedactingExecutor + redact_credentials）；
  FileDataHandle.requires_path ClassVar（PG False）；engine/base.py
  SetupExecutor 协议；registry 默认注册 postgres；scheduler/core.py
  `_source_fingerprint` + LocalScanExecutor 指纹派生；storage/store.py
  Decimal 兜底；SDK/REST/CLI/MCP 接入；测试 28 例单元（FakeExecutor）+
  6 例集成（真实 PG）+ 调度器 3 例 + SDK/CLI/API 负路径。

## ADR-056：MySQL 数据源连接器（V5 多数据源第三落点）（Step 56）

- **状态**：已确认（Step 56, V5）
- **背景**：V5 蓝图——打通 `DataSourceType.MYSQL` 预留位，让
  `scan mysql://...` 与 `--table` 直达 MySQL 库表，复用全部
  检测器/评分/报告/门禁/修复闭环/漂移能力，与 SQLite（V3）、
  PostgreSQL（V4）并列为多数据源路线完整落地（第三落点）。
  沿用 ADR-055 的「DuckDB 扩展直读」路线：**不引入 pymysql/MySQLdb
  依赖**，凭据红线（DSN 仅内存态 / connection_ref 环境变量引用）
  全套继承。
- **决策**：
  1. **经 DuckDB mysql 扩展读 MySQL**（开工前实测通过）：`LOAD mysql`
     + `ATTACH dsn AS my (TYPE mysql, READ_ONLY)` 注册只读表，创建
     `data` 视图后复用 FileDataHandle 共享实现；表名必填（缺表名抛
     DataSourceNotFoundError→404）。MySQL 无独立 schema 层（database
     已在 DSN 内），标识符/DSN 字面量转义沿用 PG 模板。
  2. **凭据红线与净化**：DSN 仅内存态（options["dsn"]）或
     connection_ref（如 DATASENTRY_MYSQL_DSN）；`_RedactingExecutor`
     净化 DuckDB 异常后转 ConnectorError。额外覆盖 mysql 扩展特有
     回显：URL 形式（mysql://user:pass@host/db）整体 → `mysql://***`，
     KV 形式（`host=... passwd=***`）正则打码——两种形态都单测。
  3. **内容指纹与调度变更感知**：`content_fingerprint()` 与 PG 同款
     （行序无关、NULL 不折叠、并入 schema_hash 与行数）；调度器
     `_source_fingerprint` 支持 mysql:// 前缀（远程库路径/源类型
     推导），同内容 skipped、内容变更重扫，源不可达不误跳过。
  4. **已知 DuckDB 1.5.x bug 绕行**：mysql 扩展「mysql-attach 之上的
     VIEW + 聚合（count/groupby）」触发内部绑定错误（"Failed to
     bind column reference"；直连 attach / 全关优化器可过、PG 扩展
     无此问题，实测 1.5.5 复现）。`_ensure_view` 统一
     `SET mysql_aggregate_pushdown_enabled = false`（先于 LOAD）：
     聚合改在 DuckDB 本地执行（语义不变，仅少一项远端聚合下推
     优化），视图 + count/groupby/fingerprint 全部正常。
  5. **SDK/REST/CLI/MCP 接入**：`client.scan_file` 对 `mysql://`
     前缀识别为 MYSQL 源（`_mysql_spec`，DSN 进 options）；`--table`
     必填、ConnectorError→退出码 4（源不可用）沿用 PG 语义；CLI/MCP
     帮助文案同步。registry 默认注册 mysql。
  6. **测试与 CI**：28 例单元（FakeExecutor：净化双形态/DSN 解析/
     setup 序列（SET 守卫顺序断言）/schema 归一化/指纹确定性/
     流式读取/关闭语义）；6 例集成（真实 MySQL：类型归一化、
     文件 vs MySQL 扫描可比、指纹变更+行序无关、调度 skipped、
     凭据不泄漏、缺表 404）经 DuckDB mysql 扩展读写 ATTACH 灌数
     （无 pymysql），无服务自动 skip；CI test job 加 mysql:8
     service（MYSQL_ROOT_PASSWORD=testpass）+ TEST_MYSQL_DSN env。
- **理由**：DuckDB mysql 扩展与 postgres 扩展同为「零新依赖 + 复用
  FileDataHandle + 只读 ATTACH」满足三条硬约束的唯一可行路径；mysql
  扩展（1.5.5）已支持 MySQL 8.4 caching_sha2_password（开工前实测），
  聚合下推 bug 有确定性的会话级 SET 绕行，不阻塞。集成测试灌数路径
  与连接器同路径（同扩展），既验证连接器又验证灌数工具。
- **影响**：新增 connectors/mysql.py（MySQLConnector + MySQLDataHandle
  + _RedactingExecutor + redact_credentials + _KV_PASSWORD_PATTERN）；
  registry 默认注册 mysql；scheduler/core.py 远程库路径/源类型感知；
  SDK/CLI/MCP 接入；测试 28 例单元 + 6 例集成 + 调度器 2 例 +
  SDK/CLI 负路径（缺表 exit 2、连接失败 exit 4 净化断言）。

## ADR-057：云存储文件源连接器（V5 多数据源第四落点，Step 57）

- **状态**：已确认（Step 57, V5）
- **背景**：V5 蓝图——让 `scan s3://bucket/orders.csv`（及 gs://、
  az:// 前缀的 CSV/Parquet/JSONL）直达对象存储文件，复用全部
  检测器/评分/报告/门禁/修复闭环/漂移能力，并支持调度变更感知。
  约束同 ADR-055/056：零新依赖（数据文件读取已在 DuckDB 能力圈内）、
  凭据不入库、复用 FileDataHandle 共享实现。
- **决策**：
  1. **经 DuckDB httpfs 扩展直读对象存储**（开工前实测通过）：
     `LOAD httpfs` + `SET s3_*` 会话配置 + `read_csv_auto/read_parquet/
     read_json_auto(uri)` 注册只读 `data` 视图，复用 FileDataHandle
     全家桶（schema 归一化/抽样/聚合/count/警告扫描）；URI 在
     `spec.path`（类型放宽 `Path | str | None`）。单连接器
     `RemoteFileConnector` 按 URI 前缀分派三种格式；本地文件连接器
     supports 收紧为排除字符串 path，注册序不产生抢占（Registry 断言
     测试覆盖）。gs:// 与 az:// 前缀识别 + httpfs 原生读取（环境变量
     认证，GCS/Azure 高级项不在本步范围）。
  2. **S3 会话配置**：自定义 endpoint（MinIO 等）经
     `options["s3_endpoint"]`（或 env `AWS_ENDPOINT_URL_S3`）传入，
     自动 path-style + 非 SSL（MinIO 必需，实测 vhost-style 404）；
     无 endpoint 走 AWS 默认（vhost-style + SSL），零配置。
     凭据只走进程环境变量（AWS_ACCESS_KEY_ID/SECRET_ACCESS_KEY 等
     httpfs 原生读取），不进 spec/DB/日志/evidence。
  3. **URI 净化**：az:// URI 可含 SAS token 等查询参数——错误文本中
     完整 URI 整体替换为 `<remote-uri>`（`_RemoteRedactingExecutor`
     包装 + `redact_uri`），DuckDB 异常净化后转 ConnectorError；
     DataSourceNotFoundError 文本同样不含 URI。
  4. **内容指纹（快速失效层）**：httpfs 不暴露对象 ETag（glob 仅
     file 列），`content_fingerprint()` = `sha256(uri|size|last_modified)`
     经 `read_blob` 元数据列（HEAD 级开销 ~0.004s，免下载）。
     语义：同内容 → size/mtime 不变 → 指纹相同（调度 skipped）；
     覆盖写 → Last-Modified 必更新 → 重扫。已知局限：同秒同 size
     覆盖的极限窗口会漏判（S3 对象覆盖写语义下可接受）；元数据
     抖动只多扫一次（无害）。调度器 `_source_fingerprint` 支持
     s3:// gs:// az:// 前缀，源不可达返回 None → 正常失败路径
     （绝不误跳过）。
  5. **fingerprint 全档**：云文件无本地字节 → `file_sha256=None`、
     content_sample_hash=快速失效层指纹（与 PG/MySQL 语义一致）。
     read_batches 走 DuckDB execute_stream（无本地路径，不能用
     pq.ParquetFile）。
  6. **SDK/CLI/MCP 接入**：`client.scan_file` 对三前缀识别为远程
     文件源（`_remote_spec`，格式按 URI 后缀推断，缺后缀/非
     csv/parquet/jsonl 抛可操作 FileNotFoundError）；CLI/MCP 帮助
     文案同步；registry 默认注册 remote。扩展名映射表
     `EXT_TO_SOURCE_TYPE` 上移至 connectors/spec.py（单源，SDK 与
     调度器共用）。
  7. **测试与 CI**：22 例单元（FakeExecutor：前缀识别/净化/会话
     序列（LOAD 先行、endpoint 条件 SET）/探测失败 404/快速指纹/
     fingerprint 全档）；6 例集成（真实 MinIO：schema 归一化、
     远程 vs 本地扫描可比、parquet/jsonl 扫描、指纹覆盖写变化、
     缺对象 404）经 httpfs COPY TO 灌数（同路径，无 boto3）；
     调度器 4 例（skipped/恢复、不可达回退、未知后缀回退、句柄
     关闭）。CI test job 加 minio service + mc 建桶步骤 +
     TEST_MINIO_ENDPOINT/凭据 env。
- **理由**：httpfs 是 DuckDB 官方扩展（随发行版分发、离线可预装），
  与 mysql/postgres 扩展同属「零新依赖 + 复用 FileDataHandle」路线；
  快速失效层解决云文件无本地字节时的调度跳过判定（读元数据不读
  数据），成本与正确性平衡，Step 58（远程增量指纹）可在此之上演进。
- **影响**：新增 connectors/remote_file.py（RemoteFileConnector +
  RemoteFileDataHandle + _RemoteRedactingExecutor + redact_uri）；
  spec.path 放宽（本地连接器 supports/构造收窄防御）；registry
  默认注册 remote；scheduler/core.py 云 URI 前缀感知；
  EXT_TO_SOURCE_TYPE 上移 spec.py；SDK/CLI/MCP 接入；测试 22 例
  单元 + 6 例集成 + 调度器 4 例；CI minio service。

## ADR-058：远程源分层增量指纹（V5 增量调度，Step 58）

- **状态**：已确认（Step 58, V5）
- **背景**：Step 55/56/57 后，PG/MySQL 任务每次 tick 都跑内容指纹
  （全表哈希）、云文件任务每次 HEAD 元数据——百万行级表 tick 开销
  可观，且内容层哈希随表增长线性恶化。V5 计划 Step 58 要求远程源
  「分层快速失效」：先用廉价统计判定，再决定是否读内容。
- **决策**：
  1. **两层复合指纹**：`stats_fingerprint()` 协议成员（`FileDataHandle`
     默认实现）= `sha256(schema_hash|row_count)`，DESCRIBE（目录
     查询）+ count，零内容读取。落库 `last_successful_hash` 为定序
     JSON `{"stats": ..., "content": ...}`（TEXT 列兼容，`_composite_hash`
     /`_parse_composite_hash` 辅助）。
  2. **调度第一层**：`_source_fingerprint(command, previous)`——
     统计层与上次复合指纹不一致 → 立即判定变更，返回 content=None
     （**零内容读取**）；一致 → 内容层指纹（PG/MySQL 全表哈希 /
     云文件 size+last_modified 元数据）判跳过。两层都一致才 skip。
  3. **扫描后落库完整复合**：`LocalScanExecutor` 用扫描指纹（
     schema_hash+row_count+content_sample_hash）组装复合落库——
     修正计划缺陷：若统计层变更后落 content=None，下一轮统计层
     比对必不等 → 再扫一次（双重扫描）。扫描自带最新内容层，落库
     后下一轮即两层全跳。
  4. **遗留迁移**：Step 55/56/57 时代的单段 hash 解析失败（统计层
     未知）→ 保守走内容层比对（等价旧语义），成功即迁移为复合。
  5. **不实现采样层**（计划选项 3）：内容哈希抽样存在误跳过风险
     （未采样到变更行），违反「内容变必重扫」硬不变量；增益可被
     option（外部数据源自身变更水印）取代，MVP 不引入。
- **理由**：统计层（目录查询+count）与内容层（全表哈希）成本差
  一个量级（PG 百万行实测 0.20s vs 0.86s，4.2×，判据 ≥3×）；
  两层判定保持「只增不减」语义——统计层变必重扫、统计层不变内容
  变必重扫（内容层兜底），无漏检；复合 JSON 定序序列化保证
  previous == current 判定稳定；本地文件源不参与（沿用 Step 53
  单层 SHA-256，语义零回归）。
- **影响**：base.py DataHandle 协议新增 stats_fingerprint（默认体
  抛 NotImplementedError，本地文件句柄 CsvDataHandle 显式实现
  「不参与」）；file_based.py 默认实现；scheduler/core.py
  `_source_fingerprint` 两层化 + `_run_job` 传 previous +
  LocalScanExecutor 复合落库；scheduler 单测 5 例（两层跳过矩阵/
  统计层变更零内容调用/遗留迁移/复合落库/句柄关闭）+ PG/MySQL
  集成各 2 例（统计层语义 + 调度四阶段）；benchmarks/
  bench_layered_fingerprint.py；DEVELOPMENT.md 章节。

## ADR-059：凭据管理完善（统一解析链 + secrets 子命令）（V5 收尾，Step 59）

- **状态**：已确认（Step 59, V5）
- **背景**：多数据源（PG/MySQL/云文件）时代，DSN 分散在 CLI 参数与
  shell/env，无统一加载与审计；connection_ref 仅解析进程环境变量，
  缺一处可查看/管理的凭据存放面。
- **决策**：
  1. **凭据文件**：`~/.config/datasentry/secrets.env`（
     `DATASENTRY_CONFIG_HOME` > `XDG_CONFIG_HOME` > `~/.config`），
     行格式 `KEY=VALUE`（空行/`#` 注释忽略），键名必须匹配环境变量
     命名 `[A-Z][A-Z0-9_]*`；父目录 700、文件 600 双向强制——读取
     侧权限过松直接拒绝（可操作报错），写入侧整体重写自动修正
     权限。
  2. **统一解析链**：CLI 参数（scan path DSN，最高优先）>
     connection_ref 解析（进程环境变量 > secrets.env 回落），均无
     → DataSourceNotFoundError。连接器 `_resolve_dsn` 统一走
     `lookup_secret`（postgres/mysql 两处收敛），SDK/CLI/MCP/调度器
     天然共享（全部经 spec 落内存态）。
  3. **`datasentry secrets` 子命令族**：`set`（getpass 交互无回显
     输入 + 二次确认，不进 shell history）/ `get`（stdout 原值）/
     `list`（**仅键名**，审计语义）/ `rm`；统一 envelope 输出与
     EXIT_CONFIG 语义（无效键/未设置/确认不一致/权限错误均退出码 2）。
  4. **审计**：`list` 只显示键名；全仓库凭据 grep（URL 密码、KV
     passwd、AWS key 形态）确认零真实凭据——测试/文档仅占位符
     （user:pass / testpass / minioadmin 本地默认）。MinIO 本地
     默认值不进远程凭据语义（变配时替换）。
- **理由**：零新依赖（getpass/标准库）；secrets.env 是 shell env
  的超集语义（可直接 source，也可被 datasentry secrets 管理），
  迁移平滑；600 强制避免「配置即泄漏」的常见失误；连接器层收敛
  保证新增数据源自动获得同一解析链。
- **影响**：新增 packages/core/.../secrets.py（secrets_path/
  load_secrets/lookup_secret/set_secret/remove_secret/write_secrets/
  SecretsFileError）；postgres.py/mysql.py `_resolve_dsn` 换
  lookup_secret（错误文案同步）；cli.py 新增 secrets 子命令族；
  测试 19 例单测 + 连接器回落 4 例 + CLI 7 例；README 凭据一节；
  DEVELOPMENT.md 章节。

## ADR-060：报告内部联动与导航增强（V6，Step 60）

- **状态**：已确认（Step 60, V6）
- **背景**：Step 49（ADR-049）的交互表已覆盖筛选/排序/搜索/分页/折叠，但跨区块
  联动为零——评分条维度只能悬停看扣分构成、Critical Findings 与问题表无关联、
  章节导航仅 footer 锚点、表格缺全局展开/收起。大报告里「评分 → 扣分明细 →
  定位问题」需要多次手动操作。
- **决策**：
  1. **联动契约（原生 JS 内联零依赖，事件委托）**：评分条维度段带
     `data-dim-link`（role=button + tabindex=0，Enter/空格等效）→ 设置维度
     筛选并滚动定位；Critical Findings 条目包 `.finding-link[data-issue-id]`
     锚点 → 聚焦定位（清空筛选 → 重绘 → 展开详情 → 高亮 4s → 居中滚动）；
     交互表导出 `#issues._render` 供联动脚本重绘；脚本缺席时静默降级为普通
     锚点跳转。
  2. **行级锚点与语义参照**：交互表行带 `data-issue-id`；Python 纯函数
     `find_issue_by_id` 与 JS 行定位同语义（延续 ADR-049「纯函数 = JS 行为
     参照」可测模式）。
  3. **导航**：粘性 `.report-nav`（HTML_SECTIONS 7 节目录）+ scrollspy 高亮
     当前章节 + `#back-to-top`（滚动 >600px 出现）；`h2` 设
     `scroll-margin-top` 防导航遮挡。
  4. **表格工具**：`expand all` / `collapse all` 按钮（切换 tbody 内
     `.issue-detail` 的 collapsed 类）。
- **理由**：全部沿用 ADR-049 的零依赖内联与可测参照模式，不引入新依赖；
  事件委托保证脚本顺序无关（联动脚本在 body 尾、表脚本在表格节内）；降级
  路径保证无 JS 环境锚点跳转仍可用；联动语义均为声明式 data 属性 + 纯函数，
  渲染测试可直接断言。
- **影响**：html.py（`_LINKAGE_JS`/`_report_nav`/`_back_to_top`/评分条
  `data-dim-link`/findings 链接/CSS）；interactive.py（行级 `data-issue-id`、
  expand/collapse、`#issues._render` 导出、`find_issue_by_id`）；测试新增
  10 例（纯函数 3 + 表标记 3 + 导航/联动 4）；CHANGELOG [Unreleased]；
  DEVELOPMENT.md reporting 段与 V2 方向标注；docs/V6_DEV_PROMPT.md。

## ADR-061：Column Profiles 画像节（V6，Step 61）

- **状态**：已确认（Step 61, V6）
- **背景**：26 章报告清单包含 Column Profiles 节（每列画像），但 HTML 报告
  从未渲染该节——`reporting/profiles.py` 不存在，`Profiler`（18.2 画像引擎）
  仅在 LLM 修复/规则流（repair_ai/rules_ai）使用，扫描管线不产出画像。
- **方案**：扫描期画像 + 显示侧 sidecar + HTML 交互节，三段式：
  1. **扫描期画像**：`scan_file` 在落库后用 `Profiler`（单条 SQL 聚合下推，
    全源可用，1e6 行预算 < 60s）计算 `DatasetProfile`；无列/聚合异常
    静默跳过（画像为增值信息，扫描主链路不受影响）。
  2. **sidecar 落盘**：`<workspace>/.datasentry/profiles/<run_id>.json`
    （`project_profiles_dir`，ADR-010 布局扩展；`.datasentry/` 已在
    .gitignore）。**不进元数据库、不进 26 章 JSON 报告契约**——报告 JSON
    仍是 store 纯读现拼（`client.export_report`），审计面与 schema 版本
    完全不动；sidecar 缺失/损坏时 HTML 静默降级（不渲染该节）。
  3. **HTML 交互节**：`render_html(..., profiles=...)` 消费
    `DatasetProfile.model_dump(mode="json")`，在 Dataset Overview 后插入
    Column Profiles 节（可选，无画像不渲染、导航不含其锚点）；节内含
    可排序画像表（null/unique/distinct/mean/median/std + name，默认 null
    占比降序最差列置顶）、每列迷你空值条、语义类型/PII 徽标、top 类别
    chips（前 3）。
- **PII 纪律**：`Profiler` 输出 examples 刻意留空、top_categories 为原始值
  （机器 sidecar 保留完整证据链）；显示层 `profile_rows` 对 chip 文本经
  `mask_text_pii` 掩码（email/手机号/身份证/IP/URL → `[REDACTED]`），
  双保险与 26 章 JSON 契约原则一致（机器契约不掩码、人类输出掩码）。
- **交互实现**：沿用 ADR-049/060 模式——Python 纯函数（`profile_rows` /
  `sort_profiles`）作 JS 语义参照（可测）；原生 JS 内联零依赖、数据经
  `json_script` 转义（`</script>` 免疫）、`textContent` 写单元格、
  可排序表头 `data-key` 复用既有 th[data-key] CSS（sorted-asc/desc）。
- **影响**：core 新增 `reporting/column_profiles.py`（序列化/排序/渲染）；
  html.py（`render_html` 新增可选参数 `profiles`、`_column_profiles`、
  `_report_nav(include_profiles=...)`、CSS：bar-track/bar/chip/badge）；
  storage/paths.py（`project_profiles_dir`）；client.py（`profiles_dir`、
  `load_profile`、`_save_profile`、scan_file 挂钩）；cli.py 与 api.py
  导出路径接入 sidecar；测试新增 20 例（core 18 + app 2）；CHANGELOG
  [Unreleased]；DEVELOPMENT.md（reporting 段/V2 方向/存储布局）；
  docs/V6_DEV_PROMPT.md（列画像从候选转落地）。

## ADR-062：修复建议预览内联展开（V6，Step 62）

- **状态**：已确认（Step 62, V6）
- **背景**：修复建议目前只在两条路径出现——server 模式的修复工作台
  （`POST /ui/.../issues/{id}` 需服务在线 + LLM）与 CLI `repair propose`
  （需数据源句柄）。离线 HTML 报告里 Issue 详情行只有描述/置信度/受影响
  行，无任何「这问题怎么修」的线索；V6 候选清单点名「修复建议预览内联
  展开（无 server 场景）」。
- **方案**：确定性建议 + 显示侧纯函数，零存储：
  1. **纯函数 `suggest_repairs(issue)`**（core `reporting/suggestions.py`）：
     按 issue 的 `detector_ids` 反查显示侧映射表（镜像 `repair/engine.py`
     的 `_PROPOSAL_MAP` / `_CLIP_ISSUE_TYPES` 与 `repair_ai.py` 的
     `_CONTEXT_OPS` 知识），输出 ≤3 条去重建议：operation / label /
     rationale / risk（风险分级同 repair_ai：SET_NULL、CLIP_VALUE、
     REPLACE_MISSING_TOKEN 为 medium，其余 low）/ targetColumns。
  2. **下发**：`issue_rows()` 每行挂 `suggestions` 键，随 `issue-data`
     JSON 内嵌（`json_script` 转义）；JS 详情行追加「Repair suggestions:」
     块（textContent 写文本，风格与既有详情行一致）；未知检测器 →
     「No built-in repair suggestion」诚实降级。
  3. **PII 纪律**：label/rationale 经 `mask_text_pii` 掩码（与 title/
     description 同轨）；targetColumns 为模式名不掩码。
- **一致性防护**：不导入 repair 引擎私有常量（显示层自包含），但测试
  用参数化断言覆盖引擎全部可修检测器必有建议——引擎映射扩展漏配时
  测试红（漂移防护）。
- **影响**：core 新增 `reporting/suggestions.py`；interactive.py
  （`issue_rows` 挂 suggestions、`detailRow` 渲染建议块）；测试新增
  18 例；CHANGELOG [Unreleased]；DEVELOPMENT.md；docs/V6_DEV_PROMPT.md
  （修复建议预览从候选转落地）。

## ADR-063：深色模式（V6，Step 63）

- **状态**：已确认（Step 63, V6）
- **背景**：报告为自包含单文件，CSS 全量硬编码亮色 hex，系统深色模式下
  白底页面刺眼；V6 候选清单点名「深色模式（prefers-color-scheme）」。
- **方案**：CSS 自定义属性变量化 + 媒体块，数据层零改动：
  1. **变量化**：`:root` 定义 15 个语义变量（--fg/--fg-muted/--fg-subtle/
     --accent/--border/--surface/--surface-strong/--surface-nav/--on-accent/
     --critical/--high/--medium/--ok/--highlight/--semantic），全部 CSS 规则
     改引用 var()；`color-scheme` 同步切换（深色下浏览器画布/表单控件
     原生适配）。
  2. **深色块**：`@media (prefers-color-scheme: dark)` 覆盖全部变量
     （GitHub-dark 系配色：fg #e6edf3 / surface #21262d / accent #58a6ff…）。
  3. **打印块**：`@media print` 强制亮色变量——防深色页打印出白字黑纸
     经典回归。
  4. **趋势 SVG**：`render_trend_svg` 硬编码 `#0969da` 改走
     `.trend-line`/`.trend-dot` 类（`var(--accent)`），随主题切换。
  5. **评分条六色段**：中间饱和色（#0969da/#1a7f37/#bf8700/#8250df/
     #cf222e/#57606a）双主题可读，保持硬编码（记录在案）。
- **强不变量**：测试断言 `_CSS` 中任何 hex 色值只允许出现在变量定义行
  （`--` 开头）——后续新增硬编码颜色即测试红。
- **影响**：html.py（_CSS 全量重构 + Step 63 文档头）；interactive.py
  （render_trend_svg 类化）；测试新增 8 例（变量定义/不变量/暗色覆盖/
  打印块/SVG 类化/单 style 标签）；CHANGELOG [Unreleased]；
  DEVELOPMENT.md；docs/V6_DEV_PROMPT.md（深色模式从候选转落地）。

## ADR-064：报告间对比（V6，Step 64）

- **状态**：已确认（Step 64, V6）
- **背景**：V6 候选清单最后一个「报告间对比（同数据集多 run 评分/问题数
  并列）」；趋势迷你 SVG 只给单数据集单 run 的时间线，缺少历史 run 的
  评分构成（维度/严重度计数）并列视角。
- **方案**：
  1. **app 侧纯函数**：`trends.py` 新增 `build_comparison(scans,
     dataset_id, current_run_id)` → `list[dict] | None`：过滤同数据集 +
     completed + 有质量分的 run，按完成时间升序（最老在前，当前 run
     最后）；每行携带 run_id / finished_at（ISO）/ overall（1 位小数）/
     delta（对前一 run 的 overall 差值，首行 None）/ dimensions（1 位
     小数或 None）/ issues（按严重度计数）/ current 标记；不足 2 个
     run（无法对比）返回 None。
  2. **core 渲染**：`render_html(..., comparison=...)` 消费 → 静态
     `Run Comparison` 表（`_comparison_section`）：Run（当前 run
     `cmp-badge` 徽标 + 整行 `cmp-current` 高亮）/ Scanned at / Overall
     （Δ 按符号上色：`cmp-up`=var(--ok)、`cmp-down`=var(--critical)、
     0.0 灰 meta）/ 维度分列（跨行并集，`Capitalize score` 表头）/
     严重度列（仅出现过的严重度，critical→info 序）；全部单元格
     escape；节可选——对比数据不足时整节 + 导航锚点不渲染
     （`_report_nav(include_comparison=...)`，与 Step 61 profiles
     同一条件节模式）。
  3. **接线**：CLI `report export --as html` 与 API
     `/scans/{run_id}/report.html` 用 `client.list_scan_runs()` +
     `report["scan"]["dataset_id"]` + 当前 run_id 构建对比（dataset_id
     取自 26 章报告头，杜绝跨数据集串行）。
- **零改动约束**：仅 HTML 消费方变化，26 章 JSON 契约/元数据库/画像
  sidecar 均不动；CSS 只加 `var()` 引用（不破坏 Step 63 hex 不变量）。
- **影响**：trends.py（`build_comparison`）；html.py（comparison 参数 +
  `_comparison_section` + `_report_nav(include_comparison=)` + 4 条新
  类规则）；cli.py / api.py 接线；测试新增 19 例（build_comparison
  过滤/排序/Δ/维度严重度透传 6 例、对比节渲染/转义/Δ 类/当前行/导航
  条件 9 例、CLI 二次扫描集成 1 例 + helper 扩展）；CHANGELOG
  [Unreleased]；DEVELOPMENT.md；docs/V6_DEV_PROMPT.md（报告间对比从
  候选转落地，候选清空 → 阶段收尾升版 v0.8.0）。

## ADR-065：CLI trend list（V7，Step 65）

- **状态**：已确认（Step 65, V7）
- **背景**：趋势数据层 `trends.build_trends`（Step 45）只有 HTML 报告迷你
  SVG 与 `/ui/trends` 页两个消费方；CLI 无任何趋势命令，脚本无法取趋势。
- **方案**：`datasentry trend list [--dataset-id DS]` → envelope 数据面：
  `data.trends` = `DatasetTrend.to_report_dict()` 每项追加 delta /
  direction / latest_score / latest_issues 摘要；`count`；空数据退出 0
  合法空列表（与 issues list 同语义，非错误）；`--dataset-id` 过滤单
  数据集（过滤后为空同样退出 0）。
- **边界**：零引擎改动——直接消费 `build_trends` + `client.list_scan_runs()`
  （CLI 已 import 同款模式）；text/json 输出沿用 `_emit`/`_envelope`。
- **影响**：cli.py（`p_trend` 子命令 + `_cmd_trend_list`）；测试新增 3 例
  （空/分组摘要/过滤）；ADR-065 + CHANGELOG [Unreleased] + V7_DEV_PROMPT。

## ADR-066：REST 趋势与画像数据端点（V7，Step 66）

- **状态**：已确认（Step 66, V7）
- **背景**：`/ui/trends` 只出 HTML；画像 sidecar（Step 61）无任何 REST
  数据面（`client.load_profile` 仅 core 渲染消费）；外部集成方无法取
  趋势/画像 JSON。
- **方案**：
  1. `GET /trends[?dataset_id=]`：JSON `{"trends": [...], "count": n}`，
     结构与 CLI trend list 完全一致（同源 `build_trends`，摘要字段同
     ADR-065）。
  2. `GET /scans/{run_id}/profiles`：画像 sidecar 原样 JSON
     （`load_profile`），缺失/未知 run → 404 `column profiles
     unavailable`（与 `/scans/{run_id}/score` 404 语义同款）。
- **边界**：无 response_model（与 `/scans/{run_id}/report` 的
  `dict[str, object]` 先例一致）；不变更 sidecar 格式。
- **影响**：api.py（2 端点）；测试新增 3 例（空/过滤/404 + 字段断言）；
  ADR-066 + CHANGELOG [Unreleased] + V7_DEV_PROMPT。

## ADR-067：UI 趋势页可视化增强（V7，Step 67）

- **状态**：已确认（Step 67, V7）
- **背景**：`/ui/trends` 页只有条状图（Step 45），无折线趋势；run 表无
  变化方向视图。
- **方案**：
  1. **Sparkline**：每数据集标题下内联 SVG 折线（`_sparkline` 纯函数：
     min-max 归一化 polyline + 首尾端点 circle + aria-label 分数序列），
     零依赖、零 JS；数据点不足 2 不渲染。
  2. **run 行 Δ badge**：Score 后新增 Δ 列——对前一 run 差值，正
     `delta-up`（绿）、负 `delta-down`（红）、0/首行灰 `meta`（—）。
- **边界**：CSS 新增 3 类（`.trend-spark`/`.delta-up`/`.delta-down`），
  ui.py 独立于报告 CSS（Step 63 变量化不变量不适用此文件）。
- **影响**：ui.py（`_sparkline`/`_delta_cell`/render_trends 表头 Δ 列）；
  测试新增 1 例（sparkline/polyline/Δ 列/首行 —）；ADR-067 +
  CHANGELOG [Unreleased] + DEVELOPMENT.md + V7_DEV_PROMPT 落地状态。

## ADR-068：MCP 数据面工具补全（V8，Step 68）

- **状态**：已确认（Step 68, V8）
- **背景**：Step 65/66/67 补全了趋势/画像的 CLI 与 REST 数据面；MCP 侧
  仍只有扫描/作业/契约工具，Agent 无法直接取趋势/画像/对比数据。
- **方案**：
  1. `trends_list(dataset_id=None)`：复用 `build_trends` +
    `client.list_scan_runs()` → `{"trends": [...], "count": n}`，
    摘要字段与 CLI trend list（ADR-065）/ REST（ADR-066）完全一致。
  2. `profiles_get(scan_run_id)`：画像 sidecar 原样 JSON；缺失返回
    `{"ok": False, "error": "profile not found: <id>"}`（与 job_create
    错误返回风格一致，不抛 -32603）。
  3. `comparison_build(dataset_id, current_run_id)`：
    `build_comparison(list_scan_runs(), dataset_id, current)` 输出；
    数据不足返回 `{"ok": True, "comparison": None}`（与 HTML 报告
    「不渲染」同语义）。
- **边界**：零引擎改动——全部复用现有 app 层函数；envelope 契约
  `{"command", "data", "ok"}` 不变；工具清单 10 → 13。
- **影响**：mcp_server.py；测试新增 4 例（工具清单、trends 空/过滤、
  profiles 缺失/存在、comparison 空/有 Δ）；ADR-068 + CHANGELOG
  [Unreleased] + V8_DEV_PROMPT。

## ADR-069：报告与 UI 本地化 --lang zh（V8，Step 69）

- **状态**：已确认（Step 69, V8）
- **背景**：26 章报告（HTML/Markdown）与 /ui 页面全部硬编码英文框架
  文案；中文用户与国内 CI 集成的报告/页面不可本地化。
- **方案**：
  1. **i18n 模块**：新 `packages/core/src/datasentry_core/reporting/
    i18n.py`（core 不能依赖 app 层，故落 core 的 reporting 包内）：
    `L10N = {"en": {...}, "zh": {...}}` 键为框架文案标识（章节标题/
    按钮/徽标/导航/表格头），`t(lang, key)`：未知语言回退 en、未知键
    回退 en 表、en 缺键原样返回键名。
  2. **core 渲染层**：`render_html`/`render_markdown`/
    `render_interactive_issue_table`/`render_trend_svg`/
    `render_column_profiles` 增加 `lang="en"` 参数；HTML 服务端文案与
    JS 交互文案（`data.labels` + `l(key, fallback)`）走 `t()`；正文与
    issue 标题不译；`<html lang>` 同步。
  3. **app 接线**：CLI `report export --lang`（en|zh，默认 en，仅
    HTML/Markdown 面）；API `/ui/*` 与 `/scans/{run_id}/report.html`
    支持 `?lang=`（未知值静默回退 en，不 422——UI 页不该硬失败）；
    26 章 JSON 报告结构化键保持英文不动。
- **边界**：CLI 其他命令 text 输出、JSON/JSONL 报告、元数据库、画像
  sidecar 均不动；RUF001 per-file-ignore 覆盖 i18n 字典（中文全角标点
  属有意为之，与 ui.py 同款）。
- **影响**：i18n.py（新）+ html.py + markdown.py + interactive.py +
  column_profiles.py + cli.py + api.py + ui.py + pyproject.toml；
  测试新增 4 例（report.html zh、无效 lang 回退、CLI --lang zh、
  ui zh 导航）+ 既有 2 例 nav 文案断言随 i18n 标签更新；ADR-069 +
  CHANGELOG [Unreleased] + DEVELOPMENT.md + V8_DEV_PROMPT。

## ADR-070：调度报告推送（V8，Step 70）

- **状态**：已确认（Step 70, V8）
- **背景**：webhook 通知只带 JSON 摘要（总分/严重度计数），集成方拿不
  到完整报告；人工补导出无自动化入口。
- **方案**：
  1. **任务字段**：`ScheduledJob.export_report: bool = False`，schema
    v7 幂等迁移 `ALTER TABLE scheduled_jobs ADD COLUMN export_report
    INTEGER NOT NULL DEFAULT 0`；`JobCreate`/`JobCommand`/`JobResult`
    同步携带（JobResult 增 `report_path`/`report_size`）。
  2. **执行导出**：`LocalScanExecutor` 在 `client.close()` 之前（同一
    连接可用窗口内）导出 HTML 到
    `<project>/.datasentry/reports/<run_id>.html`；失败仅记录日志，
    不影响 run 状态（与 webhook 尽力而为一致）。
  3. **通知载荷**：`_notify` 在结果含报告时追加 `report_path`（相对
    project，如 `.datasentry/reports/<run_id>.html`）与 `report_size`
    （字节）；不存在/错误路径不携带（payload 剔除 None 键）。
- **边界**：job 面为 API-only（无 CLI job 命令），`export_report` 经
  `POST /jobs` 透传；只导 HTML（复用 `render_html` 无 server 模式的
  自包含单文件），Markdown/JSON 导出留给集成方自取。
- **影响**：schema.py（v7）+ models.py + store.py + core.py + api.py；
  测试新增 4 例（create 落库字段 + 默认 False、export 成功写 HTML +
  webhook 带 report_path/size、导出失败不影响 run 状态、未开
  export_report 无 report 键）；ADR-070 + CHANGELOG [Unreleased] +
  DEVELOPMENT.md + V8_DEV_PROMPT。

## ADR-071：抽样扫描（V9，Step 71）

- **状态**：已确认（Step 71, V9）
- **背景**：`SamplingConfig` 定义完整但扫描管线零消费（全量扫描无条件）；
  `anomaly_ml` 是全列物化点；W10 内存峰值超标。`read_sample`（reservoir
  REPEATABLE seed 可复现）与 capability 声明（supports_sampling）均
  已就绪未接线。
- **方案**：
  1. **显式触发**：`--sampling-size N` / `--sampling-ratio R` 任意给定
    即开启抽样（method 默认 reservoir，可 `--sampling-method` 覆盖）；
     不传时行为与 v0.10.0 完全一致（默认全量，回归面隔离）。REST
     `POST /scans` 请求体 `sampling` 字段同语义透传。
  2. **sampled 视图（SQL 重写）**：新建 `connectors/sampling.py`
     `SampledDataHandle`——与底层句柄共享 executor 与视图，把检测器
     SQL 顶层 `FROM data` 重写为 `FROM (SELECT * FROM data USING SAMPLE
     reservoir({n} ROWS) REPEATABLE ({seed}))` 子查询（仓库内 50 处
     SQL 均为该单形态，重写完备性有守卫：残留裸 `FROM data` 拒绝）。
     零连接器改动、零检测器改动；非抽样支撑检测器保持全量句柄。
  3. **capability 调度**：`ScanRunner.run()` 按
     `metadata().capabilities.supports_sampling` 分发——抽样支撑检测
     器注入 `context.with_handle(sampled_handle)`，`DetectorRun.sampling`
     填 `SamplingInfo`（method/sample_size/full_size/generalizable）；
     融合/评分仍用全量行数（抽样只影响检测器输入数据面）。
  4. **seed 统一**：抽样 seed 取 `SamplingConfig.seed`（默认 42），
     REPEATABLE 保证同参数两次扫描结果一致；`ScanConfig.seed` 保持
     检测器内部随机（anomaly_ml）。
  5. **抽样即标注**：HTML/Markdown reproducibility 节 + UI 扫描详情
     页标注抽样参数（badge）；JSON 报告经 `scan.config.sampling`
     自动携带；实际抽样判定（method != none 且 size/ratio 给定）在
     runner 与渲染侧一致。
- **边界**：不做大文件自动触发（显式优先）；MCP scan 透传 sampling
  不在 V9；`count_rows` 去重/anomaly_ml SQL 侧抽样/内存打磨归
  Step 72/73。
- **影响**：connectors/sampling.py（新）+ detectors/base.py
  （with_handle）+ detectors/runner.py + cli.py + api.py + ui.py +
  reporting/html.py + reporting/markdown.py；测试新增 14 例
  （test_sampling_scan.py）；ADR-071 + CHANGELOG + DEVELOPMENT +
  V9_DEV_PROMPT。

## ADR-072：扫描管线瘦身（V9，Step 72）

- **状态**：已确认（Step 72, V9）
- **背景**：`count_rows()` 每检测器一次 + 契约规则一次 + 融合一次 +
  画像一次 ≈ O(检测器数+3) 次引擎侧全扫（CSV 每次重扫文件）；anomaly_ml
  整列物化到 numpy 后才做 Python 侧抽样（>20k 行）。
- **方案**：
  1. **count 一次注入**：`ScanRunner.run()` 开头一次
     `full_count = context.handle.count_rows()`，`_run_detector` 与
     `_run_contract_rules` 接收 `rows_scanned` 参数（抽样检测器 =
     `min(sample_size, full_count)`，全量 = `full_count`）；融合/评分
     复用 `full_count`。扫描全程恰 1 次全扫 count。
  2. **anomaly_ml SQL 侧抽样**：`SELECT {q} AS v FROM data WHERE ... 
     USING SAMPLE reservoir({max_samples} ROWS) REPEATABLE ({seed})`
     物化前限制行数；行数 < max_samples 时 reservoir 等价全量（语义
     不变，既有小数据测试零变化）；Python 侧 rng 兜底保留（幂等）。
  3. **画像复用计数**：`Profiler.profile(row_count=None)` 可选参数，
     `client._save_profile` 传 `scan_run.fingerprint.row_count`
     （指纹已在 run_scan 内计算），画像不再重复 count。
- **边界**：`SampledDataHandle.count_rows()` 保留（协议完整性，测试
  用），但 runner 不再逐检测器调用；无抽样时行为与 v0.11.0-dev 一致。
- **影响**：runner.py + anomaly_ml.py + profiler.py + client.py；
  测试新增 5 例（count 恰 1 次×2、抽样 rows_scanned、profiler 复用
  row_count、anomaly_ml reservoir 可复现）；ADR-072 + CHANGELOG +
  DEVELOPMENT + V9_DEV_PROMPT。

## ADR-073：抽样扫描内存打磨（V9，Step 73）

- **状态**：已确认（Step 73, V9）
- **背景**：基准暴露三个大内存点——①非 utf-8 CSV 整文件入内存
  （pyarrow 无流式编码读取）；②xlsx 整 sheet 入内存（read_only
  流式但 list() 全收）；③抽样扫描峰值（子查询重写方案每检测器重复
  reservoir 重扫 1e6 行：52s / 449MB）。
- **方案**：
  1. **抽样物化表**：SampledDataHandle 首次数据访问把抽样子集物化
     为 TEMP TABLE（`CREATE OR REPLACE TEMP TABLE sampled_data AS
     SELECT * FROM data USING SAMPLE reservoir(n) REPEATABLE(seed)`，
     建表前 count_rows() 触发惰性视图），后续检测器查询直接读内存
     表；无 `_executor` 的连接器退回抽样子查询重写（协议兜底）。
     基准：抽样全量 52.2s → **3.0s**（优化档）。
  2. **xlsx 行预算**：`_XLSX_ROW_BUDGET = 1_000_000` 行预算，超限
     抛 ConnectorError（提示拆 sheet 或 --sampling）；ADR-019 预算
     显式化。
  3. **非 utf-8 CSV 提示**：文件 > 512MB 且非 utf-8（整文件入内存）
     时预置 LoadWarning（提示 --sampling 或转码），阈值可注入测试。
  4. **fingerprint 抽样档**：抽样扫描用 `mode="sampled"`（head 1000
     + reservoir 100000 REPEATABLE(42) 变更检测语义），免整文件
     SHA-256。
  5. **fuzzy_duplicate 支持抽样**：20 万行 groupby/string_agg 为
     抽样档峰值主因之一，capabilities 增 supports_sampling=True
     （抽样下大组仍可检出，generalizable）。
  6. **mysql 聚合下推**：维持关闭（ADR-056 DuckDB 1.5.x 绑定 bug
     "Failed to bind column reference"），文档化不恢复。
- **内存口径**：抽样峰值高水位（实测 525MB）为 duckdb 缓冲池不回收
  + ru_maxrss 单调下 40 检测器顺序执行累积，非抽样算法瞬时内存
  （瞬时构成 ≈ 物化表 ~126MB + 单检测器最大增量 ~124MB）；沿用
  ADR-007 全量档口径：**内存仅跟踪不阻塞验收**，300MB 为优化目标。
  V9 验收 = 抽样全量耗时 ≤15s（优化）/≤60s（验收）+ 质量分漂移 ≤5。
- **影响**：sampling.py（物化表）+ uniqueness.py + csv.py + xlsx.py
  + runner.py（指纹档）+ client.py + bench_scan.py（--sampling-size
  档 + 子进程外测量）；测试 3 例新增/3 例更新；ADR-073 + CHANGELOG
  + V9_DEV_PROMPT（验收节修正）。

## ADR-074：CLI 全局 --lang（V10，Step 74）

- **状态**：已确认（Step 74, V10）
- **背景**：V8（ADR-069）`--lang` 仅挂 report export 局部；scan/
  issues/score 等 text 输出英文硬编码。用户选定 CLI 全局 --lang。
- **方案**：全局 `--lang {en,zh} default=en`（cli.py 全局参数区）；
  删除 report export 局部参数（argparse 子命令局部 default 会覆盖
  全局值）；散文输出（issues 计数 / score 三行 / llm cache·proposed·
  rejected）经 `t(args.lang, "cli.*")`；i18n.py 增 cli.* 键域
  （en 权威表 + zh 镜像，`{n}`/`{score}` 占位由调用侧 .format）。
- **边界**：JSON envelope 数据面不译（机器契约）；report export
  报告语言经全局 --lang 统一接管（API ?lang= 不受影响）；
  ADR-069「CLI 其他命令 text 输出不动」边界更新。
- **影响**：cli.py + i18n.py + 测试（1 例更新——局部 --lang 改全局
  位置；3 例新增）；ADR-074 + CHANGELOG + DEVELOPMENT + 计划书。

## ADR-075：报告正文翻译（V10，Step 75）

- **状态**：已确认（Step 75, V10）
- **背景**：V8（ADR-069）只译报告框架文案，issue 标题/描述/建议/检测
  器名仍英文。用户选定报告正文翻译。
- **方案**：渲染层翻译（零数据面改动）——fusion.py / suggestions.py
  英文原文不动；新增 `reporting/translate.py` 三函数
  （translate_title / translate_description / translate_suggestion），
  HTML / Markdown / UI 渲染前映射；en 短路原文（逐字不变），zh 查
  i18n 键域：families.*（9 归一化 family）/ issue_types.*（39 原始
  issue_type）/ suggestions.*（5 operation）/ issue.title_template /
  issue.description_template（zh 模板，数据位 cols/detector_id/count
  不译）；键完全缺失回退英文原文（_lookup 语义区别于 t() 返回键名）。
- **边界**：证据级动态描述（含计数 f-string 40+ 处）不译（候选 V11）；
  JUnit XML / JSON 机器契约不译（CI 机器消费）；detector display_name
  仅 client meta 输出（机器面）不译；UI issue 卡片 title 随 lang 译
  （ui.py 接线）。
- **注意**：fusion 输出的 issue_type 已是归一化 family（FAMILY_MAP），
  title 翻译按 issue_type 查 families.*；description 内嵌的 issue_type
  是检测器原始值，查 issue_types.*。
- **影响**：i18n.py（en 权威 + zh 镜像共 ~120 键）、translate.py 新增、
  interactive/markdown/html/ui 4 处接线、tests/test_reporting_translate.py
  （9 函数 + 6 渲染，含 en 逐字不变断言）、pyproject RUF001 豁免 1 条。

## ADR-076：MCP scan 配置透传（V10，Step 76）

- **状态**：已确认（Step 76, V10）
- **背景**：MCP scan_file 仅透传 seed；REST /scans 已支持
  sampling/detectors/seed/tags。用户选定 MCP 透传。
- **方案**：scan_file 工具 schema 增 sampling_size / sampling_ratio /
  sampling_method（random|reservoir|none，默认 reservoir，与 CLI
  同源）/ sampling_seed / detectors（array）/ tags（object）；
  handler 构造 ScanConfig(seed, detectors, scan_tags) + 条件构造
  SamplingConfig（size 或 ratio 非空时启用，镜像 CLI _cmd_scan
  逻辑）；无参数行为不变（sampling=None，全量）。
- **边界**：method choices 校验由 pydantic Literal 承担（无效值
  → -32603）；SamplingInfo 不落 seed（与 CLI 落库一致，等价性
  按 method/sample_size/full_size 断言）。
- **影响**：mcp_server.py scan_file + 测试 3 新增（透传采样 /
  detectors+tags / 与 CLI 等价性：MCP 落库 run 的 SamplingInfo
  经 client.get_detector_runs 校验）。

## ADR-077：增量画像（V10，Step 77）

- **状态**：已确认（Step 77, V10）
- **背景**：重复 `scan_file` 相同文件会产生全新 scan_run，全量重扫 +
  重建画像（V9 基准：medium CSV 全扫 ~2s 量级）。目标：文件未变更
  时秒级返回，画像（按 scan_run_id 命名的 sidecar）自然复用。
- **方案**：`client.scan_file(..., incremental=False)` 新参，默认
  False 行为与旧版完全一致。incremental=True 时：仅本地文件路径
  参与（远程 DSN/URI 直接降级全扫）；构造 JobCommand 调调度器共享
  `scheduler.core._source_fingerprint`（Step 53 同源：本地 = 文件
  SHA-256 单层），与最近一次 completed 且 file_sha256 非空的
  scan_run 指纹比对；相等 → 返回上次 (scan_run, detector_runs,
  issues) 不建新 run（与调度器 `_finish_skipped` 同语义，不落库）；
  不等或基准缺失 → 全量扫描。
- **边界**：基准缺失 = 首次扫描 / 上次为抽样 sampled 档指纹
  （fingerprint.mode="sampled" 时 file_sha256=None，csv.py）/
  上次异常 → 全扫，绝不误跳过；指纹计算 OSError → 全扫；增量
  仅文件级 SHA-256，不做列级 diff（候选 V11）；远程源增量诚实
  降级（调度器已覆盖远程跳过，客户端不重复实现）。
- **影响**：client.py `scan_file` 签名 + `_incremental_cached`
  私有方法（复用 list_scan_runs / get_detector_runs / get_issues，
  零新增 store API）；tests/test_incremental.py 6 例（未变更复用 /
  变更重扫 / 首次全扫 / 远程降级 / sampled 档降级 / 默认零影响）。

## ADR-078：证据级动态描述（V11，Step 78）

- **背景**：检测器证据 description 为硬编码 f-string（29 处），
  zh 报告下证据节仍是英文；修复引擎/交互面板依赖描述文本参数。
- **决策**：新增 `ev(key, base=None, **params)` 生成端函数——en 渲染
  文本（str 子类 EvText，携带 key/params/base）；`make_evidence`
  识别 EvText 自动把 `base + _text_key/_params` 并入 evidence.data
  （原 data 语义零变化）；渲染端 `translate_evidence_desc(lang, data,
  description)` 用 zh 模板 + 同源 params 渲染，回退原文。
- **边界**：落库 description 保持 en 原文；JSON/Markdown/JUnit/SARIF
  数据面不译（机器契约，延续 ADR-075）；zh 仅覆盖交互 detail 面板
  证据节；历史数据（无 meta）/模板缺失/参数缺失一律回退原文
  （诚实降级，不中断扫描）。
- **影响**：detectors/common.py `make_evidence`（公共插件 API，EvText
  为 str 子类向后兼容）；i18n.py 增 `evidence_desc.*` 29 组 en/zh 键；
  tests/test_evidence_desc.py 13 例（en 逐字快照 / zh 同参数 /
  历史回退 / 降级 / 交互行集成）；交互 JS 证据节渲染。

## ADR-079：调度任务 ScanConfig 透传（V11，Step 79）

- **背景**：CLI/MCP 已支持 sampling/detectors/tags，调度任务（Step 51
  JobCommand）仅 project/path/dataset_id/table_name/export_report，
  计划任务不能配扫描参数。
- **决策**：JobCommand 增 `config: ScanConfig | None = None`
  （core 包 ScanConfig，pydantic JSON 序列化 store 落库自动）；
  executor.execute 传 `config=command.config`（None 与旧版等价）；
  API POST /jobs 请求体增 config 字段（JobCreate，FastAPI 嵌套解析）。
- **边界**：跳过判定仍为文件级指纹（Step 53），config 不参与——
  文件未变 + config 不同仍跳过（不重扫）；config 变更需文件变更或
  手动触发不依赖增量路径（CLI 全量扫描侧不受影响）。
- **影响**：scheduler/models.py + api.py `_job_command_from` +
  core.py LocalScanExecutor.execute；tests/test_job_config.py 7 例
  （落库回显 / 无配置 None / 非法 422 / 重启持久 / trigger 生效
  scan_run.config 一致 / 默认等价 / 指纹跳过与 config 无关）。

## ADR-080：增量画像列级 diff（V11，Step 80）

- **背景**：加列场景下全量画像重复计算未变列（N 列聚合 + 每列
  top_categories），宽表加列成本线性浪费。
- **决策**：`_save_profile` 读上次 completed 扫描的画像 sidecar
  （排除当前 run——已落库但 sidecar 未写），比较当前 schema 列签名
  （名字+物理类型）：一致 → 全量画像（数据可能变更，画像必变，
  不优化）；有增/删/改 → 同名同类型交集列复用上次 ColumnProfile，
  仅对新增/变更列发起单条 SQL 聚合（Profiler.reuse，保持下推特性）；
  无 sidecar/损坏 → 全量。行数（count(*)）始终最新。
- **边界**：数据行变更（列集合不变）仍全量画像——画像随数据变化，
  无漏检；列改名视为删+增（重算）；复用列 dataset_id 漂移时重建。
- **影响**：Profiler.profile 增 `reuse` 参数（默认 None 行为不变）；
  client.py `_save_profile` + `_column_reuse_candidates`（私有）；
  tests/test_profile_reuse.py 12 例（复用同一性 / 仅新列重算 /
  删列剔除 / 类型变更重算 / dataset_id 重建 / 行数最新 / client
  加列·删列·全量·无 sidecar·契约稳定）。

## ADR-081：插件清单与安装管理（V12，Step 82）

- **背景**：插件 API v1（Step 31 目录版 / Step 50 entry points 版）
  只有"加载"能力，无清单元数据（作者/版本/许可）、无安装/卸载
  管理、无自测设施；plugins/ 目录只能手动平铺 .py。
- **决策**：清单化 + 管理面补齐：
  - `plugin.yaml` 清单（name/version 必填、author/license/description
    可选，name 限 `[a-zA-Z0-9_-]`）；清单非法抛 `PluginManifestError`
    （含文件与原因）
  - 目录加载兼容：顶层平铺 `*.py`（旧布局零迁移）与清单目录
    `<dir>/<name>/*.py` 均加载；无 plugin.yaml 的子目录忽略（避免
    误加载任意嵌套）；加载顺序确定性（目录序 + 文件名序）
  - `plugin install <path|dir>` → `workspace/plugins/<name>/`（目录
    整体复制、单 .py 生成占位清单；同名已存在拒绝）；`plugin
    uninstall <name>` 删除；`plugin list` 增 manifests 字段
    （清单级视图，与检测器级列表并存）
- **边界**：清单与检测器无强制绑定（一个插件目录可含多个检测器
  模块）；list_plugins 不因清单非法中断（记入 errors）；
  entry points 插件无清单（包管理面）；安全模型不变
  （本机可信代码，ADR-031/050 延续）。
- **影响**：plugins.py（PluginManifest/read_plugin_manifests/加载扩展）
  + client.py（install_plugin/uninstall_plugin/plugins_dir/list_plugins
  增 manifests）+ cli.py（plugin install/uninstall）+ tests/
  test_plugin_manifest.py 17 例（清单解析 5 / 扫描加载 4 /
  安装卸载列表 8）。

## ADR-082：插件完整性校验与信任锚（V12，Step 83）

- **背景**：目录插件加载前无内容校验，import 即执行代码；插件被
  篡改/替换无感知（装完删改文件照常加载）。
- **决策**：SHA-256 锁文件 + 加载前校验：
  - 锁文件 `<workspace>/.datasentry/plugin_locks.json`（version 1 +
    name → {version, files{relpath: sha256}, installed_at}）；
    `plugin install` 写锁、`plugin uninstall` 删锁、`plugin reaccept
    <name>` 按当前内容重锁（用户确认后放行）
  - 加载前（import 之前）校验：无锁条目自动建锁（覆盖本功能之前
    的旧插件，锁定首次见到内容）；被篡改插件**跳过加载**并记入
    errors（校验失败仅限该插件，不影响内置与其他插件——与
    entry points 优雅降级同构，ADR-050 扩展）；校验/锁定在
    import 前完成（先验后载）
  - 单元枚举与加载语义一致（plugin_units：清单目录 name=manifest
    name + 平铺 *.py name=stem）；排除衍生文件（__pycache__/*.pyc/
    .DS_Store，避免 import 产生缓存后误判篡改——回归测试覆盖）
- **边界**：不引入签名公钥体系（本机信任模型不变，ADR-031/050
  延续）；entry points 插件由包管理器负责完整性；锁损坏回退空锁
  （视为 no_lock 自动重建）；`load_plugin_detectors` 签名不变，
  新增 `load_plugin_detectors_excluding`（v1 稳定承诺保持）。
- **影响**：新模块 plugin_locks.py（PluginLocks/PluginLock/build_lock/
  integrity_report/compute_sha256）+ plugins.py（plugin_units 公开 +
  excluding 加载）+ client.py（初始化校验接线 + reaccept_plugin +
  list_plugins integrity 字段）+ cli.py（plugin reaccept）+ tests/
  test_plugin_locks.py 20 例（锁读写 6 / 校验报告 7 / 排除加载 2 /
  集成 5，含 pycache 回归）。

## ADR-083：插件测试夹具（V12，Step 84）
- **背景**：插件是"本机可信代码"，但无任何回归防护——安装前
  无法验证插件在新数据/新版本上是否仍按预期工作；手工冒烟成本高、
  不可重复。
- **决策**：清单内嵌声明式夹具 + 隔离执行：
  - plugin.yaml 增 `fixtures` 段（可选）：每个条目声明数据文件与
    期望（detector/issues/dimension），非法值（缺失数据、负数）
    安装/解析期即抛错；
  - `plugin test <name>`：为被测插件构建隔离注册表（内置 + 该
    插件），数据文件经标准连接器管线（SQL guard 与 README 纪律
    照常适用），ScanRunner 全流程执行后按期望断言；
  - 期望匹配采用"过滤器"语义：仅统计检测器 ID 命中的 Issue
    （插件之外的检测器命中不计）；dimension 缺省放行；
  - 结果三态：全过=exit 0；任一失败=exit EXIT_GATE_FAILED；
    无 fixtures=跳过（视为通过，exit 0）；
  - 不落库（scan_run 不写入 history），fixtures 数据不入
    workspace 数据目录，仅存在于插件安装目录。
- **依据**：Step 82 清单契约 + Step 31 插件 API 载荷不变
  （FixtureExpectation 仅用 Issue 模型既有字段）。

## ADR-086：CLI job 子命令面（V13，Step 86）
- **背景**：调度能力（Step 51/52）只有 MCP 管理面（jobs_list/
  job_create/job_trigger），CLI 主入口与 HTTP API 均无法管理
  任务；生命周期缺 remove/update。
- **决策**：CLI 增 `job` 子命令族，全部直连 SchedulerStore
  （<workspace>/.datasentry/metadata.db，与 MCP/API 同源）：
  - `job list [--status]`：任务视图列表；
  - `job create NAME PATH --cron`（可选 dataset-id/table-name/
    retry-attempts/webhook-url/gate-quality-min/export-report）：
    复用 validate_cron + next_run，语义与 MCP job_create 一致；
  - `job trigger ID`：Scheduler + LocalScanExecutor 同步立即执行
    （已在运行拒绝，退出码 2）；
  - `job status ID`：任务视图 + 最近 5 条运行历史；
  - `job remove ID`：delete_job。
- **错误语义**：cron 非法 / job 不存在 / 正在运行 → EXIT_CONFIG=2
  + error 字段；成功 → EXIT_OK。
- **依据**：既有 CLI 惯例（_envelope/_emit + 退出码）+ MCP
  job_create 先例；未引入新存储/新依赖。

## ADR-087：HTTP API /jobs 运行历史与 webhook 验证（V13，Step 87）
- **背景**：/jobs 路由族在 Step 51 已存在（POST/GET/PATCH/DELETE/
  trigger，422/404/409 语义已定）；V13 勘察确认缺口为：运行历史
  无独立端点、webhook 协作链路无法验证（notify 仅尽力而为日志）。
- **决策**：
  - `GET /jobs/{job_id}/runs?limit=N`（默认 20，1..200）：任务
    运行历史独立视图（get_job 内联 runs 保留不变，兼容既有调用）；
  - `POST /jobs/{job_id}/test-webhook`：向任务 webhook 发送
    `event=job.test` 样例负载（JobResult 空值样本），返回远端
    HTTP 状态码与耗时；无 webhook_url → 422；连接/发送失败 → 502
    （Bad Gateway 语义）；远端 ≥400 不视为异常，返回
    `notified=false` + 状态码（用户可判读）。
- **错误语义延续**：未知 job → 404；pydantic/业务校验 → 422。
- **依据**：API 既有错误映射惯例（404/400/422）+ WebhookNotifier
  负载形状（JobResult 序列化），无新依赖。
