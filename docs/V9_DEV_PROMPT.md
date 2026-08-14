# V9 开发计划书：大文件性能（抽样扫描 + 扫描管线瘦身）（Step 71~73）

- 目标版本：v0.11.0（阶段收尾统一升版，参照 v0.10.0=b7ba267 惯例）
- 前置基线：V8（Step 68~70，ADR-068~070）已发布 v0.10.0（tag
  `v0.10.0`，PyPI + Pages + CI 全绿），候选清空
- 文档约定：ADR-071 起追加到 `docs/00-设计裁决记录-ADR.md` 末尾；
  `CHANGELOG.md` 顶部维护 `## [Unreleased]`；架构/坑位进
  `docs/DEVELOPMENT.md`
- 通信语言：中文（代码/提交信息英文，注释极简——仓库风格零注释或极简）

## 一、目标与边界

### 背景（勘察结论）

V7/V8 勘察已确认：engine/duckdb 是完整 SQL 下推执行层，40/40 默认
检测器主路径全部经 `context.handle.sql_aggregate(...)` 消费（只对
小结果集 to_pylist），惰性视图 + `read_batches` 流式 + `read_sample`
reservoir REPEATABLE(seed) 可复现抽样**基础设施已就绪**。真正问题：

1. **`SamplingConfig` 是死配置**：`models/scan.py:19-30` 定义完整
   （method/sample_size/ratio/seed/stratified_columns/time_column/
   generalizable），`ScanConfig.sampling` 默认实例化且对外 export，
   **但扫描管线零消费**——runner/client/profiler/检测器均不读取；
   入口构造（cli/api/mcp/scheduler）一律不传。
2. **seed 两套脱钩**：`ScanConfig.seed`（默认 42，仅 anomaly_ml 消费）
   vs `spec.options["seed"]`（read_sample 消费，恒默认 42，app 层从不
   设置）——抽样可复现但无法由用户 seed 控制。
3. **重复全扫**：`count_rows()` 每检测器一次（runner.py:213）+ 契约
   规则一次（runner.py:135）+ 融合一次（runner.py:67）+ 画像一次
   （profiler.py:76）≈ O(检测器数+3) 次引擎侧全扫；CSV 每次重扫文件。
4. **anomaly_ml 全列物化**（detectors/initial/anomaly_ml.py:156-161）：
   整列 `SELECT col AS v` → to_pylist → numpy，**采样发生在物化之后**
   （>20k 行才 Python 侧 rng.sample），唯一检测器侧全列物化点。
5. **内存峰值超标（W10 待打磨）**：ADR-021 实测 1e6 行峰值内存
   677MB vs 优化目标 146MB；非 utf-8 CSV（csv.py:134-140）整文件入
   内存、xlsx（xlsx.py:38-74）整 sheet 入内存、mysql.py:165 显式关闭
   聚合下推（潜在整表拉取）。
6. **抽样元数据载体已定义即闲置**：`DetectorRun.sampling`（SamplingInfo）、
   `DatasetProfile.sampling`、`DetectionContext.sample_rows` 全部
   定义但从未赋值——V9 正好复用为「sampled 标注」。

### 目标

1. **Step 71（ADR-071）抽样配置接线**：`ScanConfig.sampling` 成为
   扫描管线一级公民——runner 按 capability（`supports_sampling`，
   detectors/models/detector.py:26-33 已声明）调度：sampling 开启时
   抽样支撑检测器走 `handle.read_sample(...)` 数据面（sampled 视图），
   非抽样支撑检测器保持全量 SQL 下推；seed 统一（`ScanConfig.seed`
   注入 `spec.options["seed"]`，消除脱钩）；`SamplingInfo` 落库
   （DetectorRun.sampling + ScanRun 级标注），26 章报告/JSON/UI 标注
   sampled 状态与参数（用户可复现）。
2. **Step 72（ADR-072）扫描管线瘦身**：runner 一次 `count_rows()`
   注入 DetectionContext（消除每检测器重复全扫）；anomaly_ml 物化
   前 SQL 侧抽样（`USING SAMPLE reservoir ... REPEATABLE(seed)` 下推
   到查询内，替代 Python 侧后采样）；画像 Profiler 复用 run 计数。
3. **Step 73（ADR-073）内存峰值打磨（W10 收编）**：非 utf-8 CSV
   编码转换流式化或明示大文件降级提示；xlsx 行预算上限 + 文档标注
   （ADR-019 预算 1e6 行内）；mysql 聚合下推调研恢复或文档化；
   `fingerprint` sampled 档下探（已有 mode="sampled"，接线到抽样
   扫描默认档）。

### 边界

- **不动检测器语义**：抽样只影响「抽样支撑检测器」的输入数据面，
  各检测器 SQL/阈值逻辑零改动；非抽样支撑检测器恒全量（结果不失真）。
- **默认全量**：`--sampling` 不传时行为与 v0.10.0 完全一致（基准
  回归面隔离，既有 885+ 测试不受影响）。**显式触发**（用户决策：
  默认全量、传参才抽样；不做大文件自动触发）。
- **sampled 视图实现**（用户决策）：runner 为抽样支撑检测器建惰性
  `data_sampled` 视图（`SELECT * FROM data USING SAMPLE reservoir(...)
  REPEATABLE(seed)`，检测器零改动继续 sql_aggregate）；非抽样支撑
  检测器保持全量 `data` 视图；能力判定走 `supports_sampling`
  capability 声明。
- **抽样即标注**：任何抽样路径都必须落 `SamplingInfo`（method/size/
  ratio/seed/generalizable），报告与 JSON 可见，防止结果漂移误读。
- **可复现**：抽样一律 `REPEATABLE(seed)`，seed 取自 `ScanConfig.seed`
  （默认 42），同文件同参数两次扫描结果一致。
- **不进 V9**：报告正文翻译、CLI 全局 --lang、MCP 配置透传
  （V8 计划书 §四 剩余候选，V10 再议）。

### 验收标准（基准扩展，benchmarks/bench_scan.py）

- 1e6 行 CSV：`--sampling-size 200000`（reservoir，seed 42）全量扫描
  耗时 ≤ 15s 优化档 / ≤ 60s 验收档；质量分漂移 |full − sampled| ≤ 5。
- 抽样峰值内存 ≤ 300MB 为优化目标（**仅跟踪不阻塞验收**，ADR-073
  口径修正：高水位为 duckdb 缓冲不回收 + ru_maxrss 单调下 40 检测器
  顺序累积，非抽样算法瞬时内存；与 ADR-007 全量档口径一致）。
- 既有基准（无抽样）保持 PASS 优化档（画像 <20s / 数值异常 <20s /
  全量扫描 ≤60s @1e6）。
- 既有全套测试零改动通过（默认路径行为不变）。

## 二、落地清单（逐 step 更新：ADR + 测试 + CHANGELOG + 计划书）

### Step 71（ADR-071）抽样配置接线（引擎 + 模型 + 报告标注）

- **models/scan.py**：SamplingConfig 语义固化（reservoir 为默认
  method，none = 全量）；ScanConfig.sampling 透传。
- **connectors**：`file_based.py`/`csv.py` `read_sample` seed 优先取
  `spec.options["seed"]`（app 层注入）——或 handle 层加
  `set_sample_seed()` 由 runner 显式注入（选型在 ADR）。
- **detectors/runner.py**：capability 调度——sampling 开启时：
  抽样支撑检测器 context.handle 替换为 sampled 句柄（惰性
  `CREATE VIEW data_sampled AS SELECT * FROM data USING SAMPLE
  reservoir(...) REPEATABLE(seed)`，检测器零改动继续 sql_aggregate）；
  非抽样支撑检测器仍全量句柄；`SamplingInfo` 填 `DetectorRun.sampling`
  与 ScanRun 级字段。
- **报告/JSON/UI**：reporting（26 章头 + HTML/Markdown）+ ui.py 标注
  `sampled: {method, size, ratio, seed}`；api.py `/scan` 响应携带。
- **测试**：connectors seed 注入、runner capability 调度（抽样支撑
  vs 非支撑）、SamplingInfo 落库/读回、报告标注、seed 可复现两次
  扫描一致、`--sampling none` = 全量等价。
- **影响**：models + connectors + runner + reporting + ui + api +
  cli（--sampling/--sampling-method/--sampling-size/--sampling-ratio/
  --sampling-seed）+ tests + ADR + CHANGELOG + DEVELOPMENT + 计划书。

### Step 72（ADR-072）扫描管线瘦身（重复全扫 + anomaly_ml 物化）

- **runner.py**：`count_rows()` 一次注入 DetectionContext（`rows_scanned`
  复用；融合/契约规则不再重复 count）；DetectorRun.rows_scanned 语义
  = 全量行数（或采样档标注 sampled 行数——ADR 定）。
- **anomaly_ml.py**：`_detect_column` 物化前 SQL 侧 reservoir 抽样
  （`USING SAMPLE reservoir({n} ROWS) REPEATABLE({seed})` 下推，
  n = min(sampling.sample_size, max_samples)），Python 侧 rng 保留
  仅作兜底；列抽样数进 SamplingInfo 标注。
- **engine/profiler.py**：复用 run 计数（不重复 SELECT count(*)）。
- **测试**：count_rows 调用次数断言（mock handle 计数，如 ≤2 次/
  扫描）、anomaly_ml 抽样路径结果可复现 + 与全量漂移 ≤ 阈值、
  rows_scanned 语义测试。
- **影响**：runner + anomaly_ml + profiler + tests + ADR + CHANGELOG
  + DEVELOPMENT + 计划书。

### Step 73（ADR-073）内存峰值打磨（W10 收编）

- **sampling.py**：抽样句柄改 TEMP TABLE 物化（首次数据访问建
  `sampled_data` 表，检测器查询直接读内存表，消除每检测器重复
  reservoir 重扫：抽样全量 52.2s → 3.0s）✅
- **xlsx.py**：行预算上限（默认 1e6 行，超限抛 ConnectorError 提示
  拆 sheet 或 --sampling，ADR-019 预算显式化）✅
- **csv.py**：非 utf-8 路径整文件入内存（pyarrow 无流式编码读取），
  >512MB 预置 LoadWarning 提示 --sampling 或转码 ✅
- **mysql.py**：调研 `mysql_aggregate_pushdown_enabled` 关闭原因
  （DuckDB 1.5.x 绑定 bug "Failed to bind column reference"，
  ADR-056 记录）→ 维持关闭并文档化 ✅
- **fingerprint**：抽样扫描默认 `mode="sampled"`（变更检测语义，
  免整文件 SHA-256）✅
- **fuzzy_duplicate**：支持抽样（supports_sampling=True）✅
- **基准**：bench_scan.py 加 `--sampling-size` 档（抽样耗时 + 峰值
  内存 + 质量分漂移；内存仅跟踪，ADR-007/073 口径）✅
- **测试**：xlsx 预算错误、CSV 非 utf-8 大文件提示/小文件无提示、
  fingerprint sampled 档（runner 级）✅
- **影响**：sampling + uniqueness + csv + xlsx + runner + client +
  benchmarks + tests + ADR + CHANGELOG + DEVELOPMENT + 计划书。

## 三、落地状态

（开工后逐 step 更新：ADR + 测试 + CHANGELOG + 本计划书；收尾统一
升版 v0.11.0：pyproject + 双 `__init__.__version__` + CHANGELOG 收口
+ tag + GitHub release，参照 v0.10.0=b7ba267 惯例）

- Step 71（ADR-071）✅ 已落地（commit `9cd571f`）：SamplingConfig 接线 +
  SampledDataHandle（SQL 重写 + 守卫）+ capability 调度 + SamplingInfo
  落库标注 + cli/api 参数 + 报告/UI 标注；测试 14 例
  （tests/test_sampling_scan.py），门禁全绿（覆盖 94.89%）
- Step 72（ADR-072）✅ 已落地（commit `5531b06`）：count_rows 一次注入 +
  anomaly_ml SQL 侧 reservoir + Profiler 复用计数；测试新增 5 例，
  门禁全绿（覆盖 94.91%）
- Step 73（ADR-073）✅ 已落地（待 commit）：抽样物化表（52.2s→3.0s）+
  xlsx 行预算 + CSV 非 utf-8 提示 + fingerprint sampled 档 + fuzzy
  支持抽样 + bench --sampling-size 档；实测 1e6 行抽样 3.0s ✅、
  漂移 3.7 ✅、峰值 525MB（仅跟踪）；门禁全绿（覆盖 94.97%）

## 四、候选后续（未排期，供下一阶段决策）

- 报告正文翻译（issue title/描述/建议）：依赖 LLM 或字典，成本高
- CLI 全局 `--lang`（score/issues/scan 等 text 输出本地化）
- MCP 工具再扩展（scan 配置透传 detector_params、sampling 参数）
- 增量画像（sidecar 增量维护，跨 run 复用画像）——V9 后自然延伸
