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
