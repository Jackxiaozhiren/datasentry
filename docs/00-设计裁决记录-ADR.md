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

## 待定（Proposed）

| 编号 | 议题 | 状态 |
|------|------|------|
| — | Ollama Provider 的具体接入版本（V1 实现） | Proposed，W13 前置 |
| — | 插件 API 版本 1 的稳定性承诺 | Proposed，V1 前置 |
