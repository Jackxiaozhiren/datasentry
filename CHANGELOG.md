# Changelog

本项目的所有显著变更按时间倒序列出。格式基于
[Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)，
版本号遵循 [Semantic Versioning](https://semver.org/lang/zh-CN/)。

## [0.14.0] - 2026-08-14

V12 插件生态治理：清单 + 安装管理 + 完整性校验 + 测试夹具。

### 新增（Step 82，插件清单与安装管理，ADR-082）

- **插件清单（plugin.yaml）**：目录插件可携带 name/version/author/
  license/description 元数据；`plugin list` 展示清单级视图
  （manifests 字段，与检测器级列表并存）
- **安装管理**：`plugin install <path|dir>` 复制到
  workspace/plugins/<name>/（目录整体复制、单 .py 生成占位清单、
  同名已存在拒绝）；`plugin uninstall <name>` 删除
- **零迁移兼容**：旧平铺 `plugins/*.py` 布局照常加载（ADR-031
  fail-fast 语义不变）；无清单子目录忽略，避免误加载任意嵌套；
  清单非法不中断 list（记入 errors）
- 安全边界不变：插件=本机可信代码，无沙箱（ADR-031/050 延续）

### 新增（Step 83，插件完整性校验与信任锚，ADR-083）

- **SHA-256 锁文件**（.datasentry/plugin_locks.json）：`plugin
  install` 安装即锁定全部文件哈希；`plugin uninstall` 移除锁条目；
  `plugin reaccept <name>` 按当前内容重锁（篡改后用户确认放行）
- **加载前校验**：import 之前比对锁——被篡改插件跳过加载并记入
  errors（仅限该插件，不影响内置与其他插件）；旧插件（本功能
  之前安装）首次加载自动建锁，零迁移
- **防误判**：完整性扫描排除 __pycache__/*.pyc/.DS_Store 等衍生
  文件（import 产生缓存不破坏锁）
- **plugin list** 增 integrity 状态（ok/tampered/no_lock）

### 新增（Step 84，插件测试夹具，ADR-083）

- **声明式夹具**：plugin.yaml 可选 `fixtures` 段——每条声明
  `data: <文件>` + `expect: detector/issues/dimension`；非法
  （缺失数据文件、负数期望）解析/安装即报错
- **plugin test <name>**：隔离注册表（内置 + 被测插件）按标准
  连接器管线执行扫描，按期望断言；三态结果（全过=0 / 任一失败=
  EXIT_GATE_FAILED=1 / 无夹具=跳过视为通过）
- **断言语义**：仅统计命中检测器的 Issue（内置检测器命中不计）；
  dimension 未声明则放行
- **不落库**：夹具执行不写 scan history，无副作用

## [0.15.0] - 2026-08-14

V13 云侧调度与协作：调度管理面三面补齐（CLI/HTTP API/MCP）。

### 新增（Step 86，CLI job 子命令，ADR-086）

- **job list [--status]**：调度任务列表（status 过滤）
- **job create NAME PATH --cron EXPR**：注册任务（dataset-id /
  table-name / retry-attempts / webhook-url / gate-quality-min /
  export-report 可选）；非法 cron 报错（EXIT_CONFIG=2）
- **job trigger ID**：立即同步执行一次（复用 Scheduler +
  LocalScanExecutor；正在运行拒绝）
- **job status ID**：任务视图 + 最近 5 条运行历史
- **job remove ID**：删除任务（不存在报错）
- 与 MCP job_create/jobs_list/job_trigger 同源同语义（同一
  SchedulerStore），无第二套逻辑

### 新增（Step 87，HTTP API jobs 运行历史与 webhook 验证，ADR-087）

- **GET /jobs/{job_id}/runs?limit=N**：运行历史独立端点（默认
  20 条；未知任务 404）
- **POST /jobs/{job_id}/test-webhook**：发送 job.test 样例负载
  验证协作链路，返回远端状态码与耗时；无 webhook 422、连接失败
  502、远端 ≥400 返回 notified=false 可判读
- 既有 /jobs CRUD/trigger/PATCH 保持兼容（Step 51 语义不变）

### 新增（Step 88，MCP 生命周期补全 + 历史保留，ADR-088）

- **MCP job_update**：部分更新（enabled/cron/retry_attempts/
  webhook_url/gate_quality_min），cron 变更重算下次运行时间，
  与 HTTP PATCH /jobs/{job_id} 同语义
- **MCP job_remove**：删除任务（未知任务返回 ok:false）
- **运行历史保留**：SchedulerStore.prune_runs(max_per_job=100)
  裁剪每个任务最旧的超限运行记录（窗口函数按时间倒序分区）
- 三面对齐：CLI / HTTP API / MCP 的 job 能力语义一致，共用
  同一 SchedulerStore，无分叉

## [0.13.0] - 2026-08-14

V11 三件套：证据级描述本地化 + 调度配置透传 + 增量画像列级复用。

### 新增（Step 78，证据级动态描述，ADR-078）

- **证据描述模板化**：29 处检测器证据的 `description=f"..."` 改为
  `ev()`（en 渲染文本 + 携带 `_text_key`/`_params` 翻译 meta 与数据
  base），en 逐字不变、JSON 契约面零改动
- **zh 证据镜像**：交互报告 detail 面板证据节按 `--lang` 渲染
  （`evidence_desc.*` i18n 键域 29 组 en/zh 模板，同源参数数值逐字
  一致）；JSON/Markdown/JUnit/SARIF 数据面保持 en 原文（机器契约）
- 历史证据（无 meta）回退原文；模板/参数缺失回退原文（诚实降级）；
  `make_evidence` 公共插件 API 向后兼容（EvText 为 str 子类）

### 新增（Step 79，调度任务 ScanConfig 透传，ADR-079）

- **JobCommand 增 `config` 字段**：计划任务可带 sampling/detectors/
  scan_tags（与 CLI/MCP 同源配置），store 落库自动持久化
- **API POST /jobs 请求体增 `config`**：FastAPI 嵌套解析（非法值 422）；
  无 config 任务行为不变（executor 传 None 与旧版等价）
- 跳过判定保持文件级指纹语义（config 不参与，ADR-079 记录边界）

### 新增（Step 80，增量画像列级 diff，ADR-080）

- **列级复用**：`Profiler.profile` 增 `reuse` 参数——仅对新增/变更列
  发起单条 SQL 聚合（保持下推特性），未变列从上次画像 sidecar 复制
  （行数始终最新）
- **schema 签名判定**：`_save_profile` 比较当前列集合（名+物理类型）
  与上次 completed 画像——一致 → 全量（数据变画像必变，不优化）；
  增/删/改 → 交集列复用；无 sidecar/损坏 → 全量
- 边界：数据行变更仍全量画像（无漏检）；列改名视为删+增；复用列
  dataset_id 漂移重建；默认行为与旧版完全一致

## [0.12.0] - 2026-08-14

V10 全球化与扫描管线：CLI 全局 --lang + 报告正文翻译 + MCP 透传 + 增量画像。

### 新增（Step 74，CLI 全局 --lang，ADR-074）

- **全局 `--lang {en,zh}`**：scan/issues/score 等 CLI text 输出本地化
  （`cli.*` i18n 键域：issues 计数 / 质量分 / llm cache·proposed·
  rejected）；`report export` 报告语言由全局参数统一接管（删除
  局部 --lang，默认 en 行为不变）
- 边界：JSON envelope 数据面不译（机器契约）；API `?lang=` 不受影响

### 新增（Step 75，报告正文翻译，ADR-075）

- **issue 标题/描述/修复建议翻译**：新增 `reporting/translate.py`
  渲染层映射（translate_title / translate_description /
  translate_suggestion），HTML / Markdown / UI 渲染前翻译；
  `--lang zh` 时标题（如「缺失值（id）」）、融合描述
  （`[detector_id vX.Y] 空值率过高：2`）、建议 label·rationale 全中文
- 键域：families.*（9）/ issue_types.*（39）/ suggestions.*（5）/
  issue 模板 2；en 逐字不变（短路原文），键缺失回退英文原文
- 边界：证据级动态描述、JUnit XML / JSON 机器面不译（候选 V11）

### 新增（Step 76，MCP scan 配置透传，ADR-076）

- **`scan_file` 工具透传**：sampling_size / sampling_ratio /
  sampling_method（random|reservoir|none，默认 reservoir）/
  sampling_seed / detectors（白名单数组）/ tags（对象）——与 CLI
  构造逻辑同源；无参数行为不变（全量扫描）
- 等价性：MCP 落库 detector run 的 SamplingInfo 与 CLI 扫描一致
  （method/sample_size/full_size 断言）

### 新增（Step 77，增量画像，ADR-077）

- **`client.scan_file(..., incremental=True)`**：本地文件源指纹
  （文件 SHA-256，与调度器 Step 53 同源）比对最近一次完成扫描；
  未变更 → 直接复用上次 scan_run / 检测器运行 / Issue（画像按
  scan_run_id 自然复用，不建新 run）；变更或无基准（首次扫描 /
  抽样 sampled 档指纹 / 远程源 / 指纹失败）→ 全量重扫，绝不误跳过
- 默认 `incremental=False` 行为与旧版完全一致（零影响）

## [0.11.0] - 2026-08-14

V9 大文件性能：抽样扫描 + 扫描管线瘦身 + 内存打磨。

### 发布说明（V9 收尾）

- V9 三个落点全部落地：Step 71 抽样配置接线（SampledDataHandle +
  capability 调度 + 报告/UI 标注）、Step 72 扫描管线瘦身（count 一次
  注入 + anomaly_ml SQL 侧抽样 + 画像复用计数）、Step 73 内存打磨
  （抽样物化表 + xlsx 行预算 + CSV 非 utf-8 提示 + 抽样指纹档 +
  fuzzy_duplicate 支持抽样 + bench 抽样档）；版本统一 v0.11.0，tag
  `v0.11.0` 触发 PyPI 发布 + Pages 更新
- 基准实测（1e6 行 CSV，reservoir 200000，seed 42）：抽样全量扫描
  **3.0s**（优化档 <15s）、质量分漂移 **3.7**（≤5）、抽样峰值内存
  525MB（仅跟踪，ADR-007/073 口径）；全量档保持 PASS（12.7s）
- 默认路径（不传抽样参数）行为与 v0.10.0 完全一致

### 新增（Step 71，抽样扫描，ADR-071）

- **`--sampling-size N` / `--sampling-ratio R`**：显式开启抽样扫描——
  抽样支撑检测器经 `SampledDataHandle` 查询 `USING SAMPLE reservoir(N
  ROWS) REPEATABLE(seed)`（可复现，seed 默认 42）；非抽样支撑检测器
  保持全量；不传参数时行为与 v0.10.0 完全一致
- **capability 调度**：`ScanRunner` 按 supports_sampling 分发，抽样
  检测器 `DetectorRun.sampling` 落 `SamplingInfo`（method/sample_size/
  full_size/generalizable）；融合与评分仍用全量行数
- **抽样即标注**：HTML/Markdown 报告 reproducibility 节与 UI 扫描
  详情页标注抽样参数；JSON 报告经 `scan.config.sampling` 携带
- **REST**：`POST /scans` 请求体支持 `sampling` 字段（同语义透传）

### 优化（Step 72，扫描管线瘦身，ADR-072）

- **count 一次注入**：扫描全程恰 1 次 `count_rows()`（原每检测器 +
  契约 + 融合 + 画像 ≈ O(检测器数+3) 次全扫）——`DetectorRun.rows_scanned`
  由 runner 直接计算（抽样检测器 = 抽样行数，全量 = 全量行数）
- **anomaly_ml SQL 侧抽样**：物化前 `reservoir(max_samples) REPEATABLE
  (seed)` 下推（行数 < max_samples 时等价全量，语义不变），消除唯一
  全列物化点的物化浪费
- **画像复用计数**：`Profiler.profile(row_count=...)` 复用扫描指纹
  行数，画像不再重复全扫

### 优化（Step 73，内存打磨 + 抽样物化，ADR-073）

- **抽样物化表**：抽样句柄首次数据访问把 reservoir 子集物化为
  TEMP TABLE（`sampled_data`），检测器查询直接读内存表——1e6 行
  抽样全量扫描 52.2s → **3.0s**（优化档 <15s）
- **xlsx 行预算**：整 sheet 行数 > 1e6 抛清晰 ConnectorError（拆
  sheet 或 --sampling），ADR-019 预算显式化
- **CSV 非 utf-8 提示**：整文件入内存路径（非 utf-8）文件 >512MB
  时预置 LoadWarning（提示 --sampling 或转码）
- **抽样指纹档**：抽样扫描默认 `mode="sampled"`（变更检测语义），
  免整文件 SHA-256
- **fuzzy_duplicate 支持抽样**：capability 增补，抽样下大组仍可检出
  （generalizable）
- **基准**：bench_scan.py 新增 `--sampling-size` 抽样档（耗时 +
  峰值内存 + 质量分漂移；内存沿用 ADR-007 仅跟踪口径）

## [0.10.0] - 2026-08-13

V8 MCP 数据面补全 + 报告与 UI 本地化 + 调度报告推送。

### 发布说明（V8 收尾）

- V8 三个落点全部落地：Step 68 MCP trends_list/profiles_get/
  comparison_build、Step 69 报告与 UI 本地化 --lang zh、Step 70 调度
  报告推送 export_report；版本统一 v0.10.0，tag `v0.10.0` 触发
  PyPI 发布 + Pages 更新
- 零引擎改动：全部消费既有 build_trends/load_profile/build_comparison
  与 26 章 JSON 契约；报告 JSON 结构化键保持英文；覆盖率 95.05%

### 新增（Step 68，MCP 数据面工具，ADR-068）

- **`trends_list(dataset_id=None)`**：趋势数据面——`{"trends": [...],
  "count": n}`，摘要字段与 CLI trend list / REST /trends 同源同构
- **`profiles_get(scan_run_id)`**：画像 sidecar 原样 JSON；缺失返回
  `{"ok": False, "error": "profile not found: <id>"}`
- **`comparison_build(dataset_id, current_run_id)`**：报告间对比结构；
  数据不足返回 `{"ok": True, "comparison": None}`（工具清单 10 → 13）

### 新增（Step 69，报告与 UI 本地化 --lang zh，ADR-069）

- **i18n 模块**：新 `reporting/i18n.py`（L10N en/zh + `t(lang, key)`，
  未知语言/未知键回退 en）；HTML/Markdown/交互表格/趋势 SVG/列画像
  渲染全部支持 `lang` 参数
- **CLI**：`datasentry report export --lang zh`——26 章 HTML/Markdown
  框架文案（章节标题/表格头/按钮/徽标/导航）中文化；正文与 issue
  标题不译；默认 en
- **API/UI**：`/ui/*` 与 `/scans/{run_id}/report.html` 支持 `?lang=`，
  未知值静默回退 en；UI 页导航/按钮/徽标文案本地化；26 章 JSON 报告
  结构化键保持英文

### 新增（Step 70，调度报告推送，ADR-070）

- **`POST /jobs` 增 `export_report`**：true 时扫描完成后自动导出 HTML
  报告到 `.datasentry/reports/<run_id>.html`（失败仅记日志，不影响
  run 状态）；schema v7 幂等迁移
- **webhook 载荷扩展**：结果含报告时追加 `report_path`（相对 project）
  与 `report_size`（字节）；错误/未开启路径不携带

## [0.9.0] - 2026-08-13

V7 趋势/画像数据面补全：CLI trend list + REST 趋势/画像端点 + UI 趋势页
可视化增强。

### 发布说明（V7 收尾）

- V7 三个落点全部落地：Step 65 CLI trend list、Step 66 REST /trends +
  /scans/{run_id}/profiles、Step 67 UI 趋势页 Sparkline + Δ 列；版本
  统一 v0.9.0，tag `v0.9.0` 触发 PyPI 发布 + Pages 更新
- 零引擎改动：全部消费既有 `build_trends`/`load_profile`；26 章 JSON
  契约/画像 sidecar 格式不变；覆盖率 95.03%

### 新增（Step 65，CLI trend list，ADR-065）

- **`datasentry trend list [--dataset-id DS]`**：跨扫描趋势数据面——
  `data.trends` 每项含 `DatasetTrend.to_report_dict()` 全字段 +
  delta/direction/latest_score/latest_issues 摘要；`count`；空数据
  退出 0（合法空列表）；`--dataset-id` 过滤单数据集

### 新增（Step 66，REST 趋势/画像端点，ADR-066）

- **`GET /trends[?dataset_id=]`**：趋势 JSON 数据面（与 CLI trend list
  同源同构）
- **`GET /scans/{run_id}/profiles`**：画像 sidecar 原样 JSON，缺失 404

### 新增（Step 67，UI 趋势页可视化增强，ADR-067）

- **Sparkline**：每数据集内联 SVG 折线（min-max 归一化 polyline +
  首尾端点 + aria-label），零依赖零 JS
- **run 行 Δ 列**：对前一 run 差值，正绿负红、0/首行灰 —

## [0.8.0] - 2026-08-13

V6 报告 HTML 交互增强：跨区块联动导航 + 列画像节 + 修复建议预览 +
深色模式 + 报告间对比。

### 发布说明（V6 收尾）

- V6 五个落点全部落地：Step 60 联动/导航/表格工具、Step 61 列画像节、
  Step 62 修复建议预览、Step 63 深色模式、Step 64 报告间对比；版本
  统一 v0.8.0，tag `v0.8.0` 触发 PyPI 发布 + Pages 更新
- 报告保持自包含单文件（零外链/零外部脚本）；26 章 JSON 契约/元数据库/
  画像 sidecar 零破坏性改动；覆盖率 95.03%

### 新增（Step 60，报告内部联动与导航，ADR-060）

- **评分条钻取**：Quality Score 维度条可点击/键盘触发（`data-dim-link`，
  role=button + Enter/空格等效）→ 自动应用维度筛选并滚动定位到 Issue
  Breakdown；扣分构成悬停提示保持不变
- **发现定位**：Critical Findings 条目点击 → 清空筛选、展开详情行、高亮
  4 秒并居中滚动到对应问题行（`data-issue-id` 行级锚点）
- **导航**：粘性章节导航（scrollspy 高亮当前章节）+ 回到顶部按钮（滚动
  超 600px 出现）；`h2` 设 scroll-margin-top 防导航遮挡
- **表格工具**：Issue Breakdown 新增 expand all / collapse all 一键
  展开/收起全部详情行
- 实现延续 Step 49（ADR-049）风格：原生 JS 内联零依赖、事件委托
  （脚本顺序无关）、无 JS 时降级为锚点跳转；新增 Python 纯函数
  `find_issue_by_id` 作为 JS 行定位的语义参照

### 新增（Step 61，Column Profiles 画像节，ADR-061）

- **扫描期画像**：`scan_file` 落库后用 `Profiler`（单条 SQL 聚合下推，
  全源可用）计算列画像 → `<workspace>/.datasentry/profiles/<run_id>.json`
  sidecar（app 私有，不进元数据库、不动 26 章 JSON 契约）
- **HTML 交互节**：`render_html(..., profiles=...)` 在 Dataset Overview
  后渲染 Column Profiles 节（可选，无 sidecar 时不出现）——可排序画像表
  （null/unique/distinct/mean/median/std + 列名，默认空值率降序最差列
  置顶）、每列迷你空值条、语义类型/PII 徽标、top 类别 chips（前 3）
- **导航**：节存在时粘性导航追加 `column_profiles` 锚点
- **PII 纪律**：sidecar 保留完整证据链（机器数据），显示层 top 类别经
  `mask_text_pii` 掩码（`[REDACTED]`）
- 实现延续 Step 49/60 风格：纯函数参照（`profile_rows`/`sort_profiles`）、
  原生 JS 内联、`json_script` 转义、`textContent` 写单元格

### 新增（Step 62，修复建议预览内联展开，ADR-062）

- **确定性建议**：`reporting/suggestions.py` 纯函数 `suggest_repairs` 按
  detector_ids 反查显示侧映射（镜像修复引擎知识），≤3 条去重建议
  （operation / label / rationale / risk / targetColumns）
- **内联展示**：Issue 详情行新增「Repair suggestions」块（无 server /
  LLM / 数据源句柄依赖），未知检测器显示「No built-in repair
  suggestion」诚实降级
- **零存储**：建议由报告数据纯函数推导，不落库、不进 26 章 JSON 契约
- **PII 纪律**：label / rationale 显示前经 `mask_text_pii` 掩码
- **漂移防护**：测试参数化覆盖修复引擎全部可修检测器必有建议
- 测试新增 18 例；覆盖率 94.97%

### 新增（Step 64，报告间对比，ADR-064）

- **Run Comparison 节**：同数据集历史 run 评分并列静态表——Run（当前
  run 徽标 + 整行高亮）/ Scanned at / Overall（Δ 对前一 run 按符号
  上色：升 var(--ok)、降 var(--critical)）/ 维度分列（跨 run 并集、
  首字母大写表头）/ 严重度列（仅出现过的严重度，critical→info 序）
- **构建器**：`trends.build_comparison(scans, dataset_id, current_run_id)`
  纯函数——过滤同数据集 + completed + 有质量分的 run、时间升序、Δ
  首行 None、不足 2 run 返回 None（节 + 导航锚点静默不渲染）
- **接线**：CLI `report export --as html` 与 API
  `/scans/{run_id}/report.html` 自动注入对比（dataset_id 取自 26 章
  报告头，杜绝跨数据集串行）；26 章 JSON 契约/元数据库/画像 sidecar
  零改动

### 新增（Step 63，深色模式，ADR-063）

- **CSS 变量化**：报告全量色板改为 15 个语义 CSS 自定义属性（:root 亮色
  默认），随 `prefers-color-scheme: dark` 自动切换为深色（GitHub-dark
  系配色）；`color-scheme` 同步（画布/表单原生适配）
- **打印保护**：`@media print` 强制亮色变量，避免深色页打印白字黑纸
- **趋势 SVG**：改走 `.trend-line`/`.trend-dot` 类（`var(--accent)`）
  随主题切换；评分条六色段为双主题可读中间饱和色，保持硬编码
- **强不变量**：测试断言 CSS 内任何 hex 色值只允许出现在变量定义行，
  新增硬编码颜色即失败
- 测试新增 8 例；覆盖率 94.99%

## [0.7.0] - 2026-08-12

V5 多数据源：MySQL 数据源连接器 + 云存储文件源连接器 + 分层增量指纹 +
凭据管理完善。

### 发布说明（V5 收尾）

- V5 四个落点全部落地：MySQL（Step 56）、云存储文件（Step 57）、
  远程源分层增量指纹（Step 58）、凭据管理（Step 59）；版本统一
  v0.7.0，tag `v0.7.0` 触发 PyPI 发布 + Pages 更新
- 多数据源矩阵：CSV/Parquet/JSONL/XLSX/DuckDB/SQLite（本地文件）+
  PostgreSQL + MySQL + s3/gs/az 云文件；调度器变更感知对全部远程
  源生效（统计层 + 内容层两层快速失效），凭据统一走
  env > secrets.env 解析链且零落库

### 新增（Step 59，凭据管理完善，ADR-059）

- **Step 59**：`datasentry secrets set|get|list|rm` 凭据管理——
  `~/.config/datasentry/secrets.env`（DATASENTRY_CONFIG_HOME /
  XDG_CONFIG_HOME 可覆盖），父目录 700/文件 600 强制；`set` getpass
  无回显 + 二次确认（不进 shell history）；`list` 仅显示键名（审计
  语义）
- **统一解析链**：CLI 参数 > 进程环境变量 > secrets.env 自动加载——
  connection_ref 语义扩展（env 找不到回落 secrets 文件，仍无 →
  DataSourceNotFoundError），PG/MySQL 连接器收敛到 `lookup_secret`，
  SDK/CLI/MCP/调度器共享
- **凭据红线不变**：DSN 仍只走内存态/配置面，不落库/日志/报告；
  错误净化（URL/KV 双形态）持续

### 新增（Step 58，远程源分层增量指纹，ADR-058）

- **Step 58**：调度变更感知两层快速失效——PG/MySQL/云文件任务
  每次 tick 先比统计层（schema_hash+row_count，DESCRIBE+count，
  零内容读取）：统计层变立即判定变更（零内容读取）；统计层不变才
  算内容层指纹（全表哈希/云元数据）判跳过
- **复合指纹落库**：`last_successful_hash` 存定序 JSON
  `{"stats","content"}`；扫描成功后用扫描指纹落库完整复合（避免
  统计层变更后的双重扫描）；Step 55/56/57 遗留单段 hash 自动保守
  迁移（内容层比对一次后落库复合）
- **本地文件源零回归**：沿用 Step 53 单层文件 SHA-256 语义，不参与
  两层指纹
- **基准**：PG 百万行统计层 0.20s vs 内容层 0.86s（4.2×，判据
  ≥3×）；benchmarks/bench_layered_fingerprint.py

### 新增（Step 57，云存储文件源连接器，ADR-057）

- **Step 57**：对象存储文件 —— `scan s3://bucket/orders.csv`（及
  gs://、az:// 前缀的 CSV/Parquet/JSONL）直达云文件：经 DuckDB
  httpfs 扩展只读直读（零新依赖），复用全部检测器/评分/报告/
  门禁/修复/漂移能力；MCP `scan_file` 同步支持
- **端点配置**：MinIO 等自定义 endpoint 经
  `options["s3_endpoint"]`（或 env `AWS_ENDPOINT_URL_S3`）传入，
  自动 path-style + 非 SSL（MinIO 必需）；无 endpoint 走 AWS 默认
  零配置；凭据只走进程环境变量（AWS_ACCESS_KEY_ID 等 httpfs 原生
  读取），不落库/日志/报告；az:// URI（可含 SAS token）错误净化
  为 `<remote-uri>`
- **变更感知（快速失效层）**：调度器源指纹支持 s3:// gs:// az://
  前缀 —— `content_fingerprint()` = size+last_modified 元数据组合
  哈希（HEAD 级开销免下载），同内容 skipped、覆盖写重扫、源不可达
  不误跳过；已知局限（同秒同 size 覆盖窗口）记录于 ADR-057
- **集成测试与 CI**：真实 MinIO 集成用例（integration marker，
  无服务自动跳过）；CI test job 加 minio service + mc 建桶步骤

### 新增（Step 56，MySQL 数据源连接器，ADR-056）

- **Step 56**：MySQL —— `scan mysql://user:pass@host:port/db --table <t>`
  直达远程库表：经 DuckDB mysql 扩展只读 ATTACH（不引入 pymysql），
  复用全部检测器/评分/报告/门禁/修复/漂移能力；表名必填；
  MCP `scan_file` 同步支持
- **凭据红线（继承 PG 全套）**：DSN 只走 CLI 参数/内存态/环境变量
  引用（`DATASENTRY_MYSQL_DSN` 等 connection_ref），不落库、不进
  日志/evidence/报告；DuckDB 错误净化双形态（URL DSN 整体与 KV
  `passwd=` 均打码）后转 ConnectorError——CLI 连接失败退出码 4，
  错误面无凭据
- **已知 DuckDB 1.5.x bug 绕行**：mysql-attach 之上的视图+聚合触发
  内部绑定错误 → 连接器统一 `SET mysql_aggregate_pushdown_enabled
  = false`（聚合改在 DuckDB 本地执行，语义不变）
- **变更感知演进（继承 PG 语义）**：调度器源指纹支持 mysql:// 前缀，
  同内容 skipped、表内容变更重扫
- **集成测试与 CI**：真实 MySQL 集成用例（integration marker，无
  MySQL 自动跳过）；CI test job 加 mysql:8 service

## [0.6.0] - 2026-08-12

V4 多数据源：PostgreSQL 数据源连接器。

### 新增（Step 55，PostgreSQL 数据源连接器，ADR-055）

- **Step 55**：PostgreSQL —— `scan postgresql://user:pass@host/db --table <t>`
  直达远程库表：经 DuckDB postgres 扩展只读 ATTACH（不引入 psycopg），
  复用全部检测器/评分/报告/门禁/修复/漂移能力；表名必填、schema 可选；
  MCP `scan_file` 同步支持
- **凭据红线**：DSN 只走 CLI 参数/内存态/环境变量引用
  （`DATASENTRY_PG_DSN` 等 connection_ref），不落库、不进日志/evidence/
  报告；DuckDB 错误净化（DSN/密码打码）后转 ConnectorError——CLI
  连接失败退出码 4，错误面无凭据
- **变更感知演进**：PG 无文件字节 → 内容指纹（单查询全表哈希，
  行序无关、NULL 不折叠）——同内容调度 skipped、表内容变更重扫，
  `last_successful_hash` 同字段落库零 schema 变更
- **类型归一化**：DECIMAL(n,p)→DECIMAL、TIMESTAMP WITH TIME ZONE→
  TIMESTAMPTZ 等物理类型规范化，检测器/画像精确匹配；evidence
  序列化兜底 Decimal→float
- **集成测试与 CI**：真实 PG 集成用例（integration marker，无 PG
  自动跳过）；CI test job 加 postgres:16-alpine service

## [0.5.1] - 2026-08-11

### 修复

- FastAPI 生命周期：`on_event`（startup/shutdown，已弃用）迁移至
  `lifespan` 异步上下文管理器（startup 恢复调度 + 启动 worker，shutdown
  经 `finally` 停止 worker），消除弃用警告
- CLI `scan --table` 帮助文本补齐 SQLite 支持说明（.db/.sqlite/.sqlite3，
  Step 54，V3 遗留文档不一致）

## [0.5.0] - 2026-08-11

V3 多数据源：SQLite 文件数据源接入。

### 新增（Step 54，SQLite 数据源连接器）

- **Step 54**：SQLite 数据源 —— `.db`/`.sqlite`/`.sqlite3` 文件经
  DuckDB sqlite 扩展只读扫描（sqlite_scan 注册 data 视图，复用
  schema/抽样/聚合/fingerprint/公式注入扫描共享实现）；表名必填
  （缺失 404 提示）；`POST /scans` 请求体新增 `table_name` 透传；
  调度器天然联动（JobCommand.table_name + 变更感知哈希对 SQLite
  表变更同样生效）；REST 异常映射补连接器错误族
  （DataSourceNotFoundError→404、UnsupportedFormat/UnsafeSql→400）
  （ADR-054）

## [0.4.0] - 2026-08-11

持续质量门禁：变更感知增量调度。

### 新增（Step 53，变更感知增量调度）

- **Step 53**：文件 SHA-256 缓存 —— 调度执行前比对目标文件哈希与
  最近一次成功扫描（未跳过）一致则本轮跳过：不建 scan_run、不重判
  门禁，仅记 `skipped:true` 的 completed run（scan_run_id 为空，
  summary/webhook 带 skipped + file_hash）；next_run 照常推进；
  文件缺失走正常失败路径；手动 trigger 同样生效（二次触发同内容
  文件 → 202 skipped）（ADR-053）

## [0.3.0] - 2026-08-11

修复闭环 + MCP 面与 REST 对等（V2-D 收尾）。

### 新增（Step 52，V2-D 收尾）

- **Step 52**：调度质量门禁 + MCP 调度工具 —— `scheduled_jobs` 增
  `gate_quality_min`（0-100，NULL=关；schema v5）；run 完成后按
  `quality_score.overall` 判定门禁（业务判定非执行失败：run 仍
  completed，仅 summary/webhook 带 `gate: {passed, min, score}`）；
  JobCreate/JobUpdate 越界 422，PATCH None = 不变；MCP 新增
  `jobs_list` / `job_create` / `job_trigger` 工具（与 REST `/jobs`
  同源复用 SchedulerStore，非法 cron 返回 ok:false 而非异常）
  （ADR-052）

## [0.2.0] - 2026-08-10

V2 四大方向（PII 加密还原 / HTML 报告交互 / 插件生态 / 云侧调度）。

### 新增（Step 51，V2-D 云侧调度）

- **Step 51**：本地调度器 —— cron 表达式（croniter，非法拒绝 422）+
  SQLite 持久化任务队列（schema v4 `scheduled_jobs` / `job_runs`）；
  `POST/GET/PATCH/DELETE /jobs` + `POST /jobs/{id}/trigger`（执行中
  409 互斥）；失败重试（60s 间隔，可配次数）与死信（dead）；
  服务重启恢复（running → idle + interrupted 标记）；worker 线程
  FastAPI startup/shutdown 生命周期内运行；webhook 结果通知
  （成功/失败，URL 为空即关）；`ScanExecutor` 协议抽象执行器
  （默认本地执行，未来可换云函数/SSH）（ADR-051）

### 新增（Step 50，V2-C 插件生态）

- **Step 50**：插件 entry point 自动发现 —— `datasentry.detectors`
  entry points 组自动加载（类/实例/工厂三形态），逐失败优雅降级
  （`PluginDiscoveryReport` 汇总 loaded/failed/errors）；`plugin list`
  支持 `--format json` 并展示 `source`（builtin/dir/entrypoint）与
  加载失败明细；`list_detectors()` 补 source 字段；提供可安装示例
  插件包 `examples/plugins/datasentry-sample-detector`
  （`uv pip install -e` → 自动发现 → 扫描自动启用，无配置；
  验证：隔离环境 39 内置 + 1 插件 = 40 个 detector runs）；
  目录插件与内置检测器保持原样（ADR-050）

### 新增（Step 49，V2-B HTML 报告交互）

- **Step 49**：交互式 HTML 报告 —— Issue Breakdown 升级为可交互表格
  （severity/维度筛选、列排序、详情折叠、分页，纯原生 JS 零依赖内联，
  离线可用）；Quality Trends 迷你 SVG 折线（复用 trends.py 数据）；
  server 模式联动修复工作台（`GET /scans/{id}/report.html`）；
  数据 JSON 经 `\u003c` 转义防 `</script>` 注入，JS 只以 textContent
  写入、PII 掩码双保险；筛选/排序/分页 Python 纯函数可测（ADR-049）

### 新增（Step 48，V2-A PII 加密还原）

- **Step 48**：PII 加密还原 —— 脱敏映射 AES-256-GCM 加密落库
  （SQLite `pii_mappings`，schema v3），LLM 回复占位符还原，
  密钥轮换 `llm rotate-key`、还原审计（`pii_restore` /
  `pii_key_rotate`）、报告/UI 默认打码（ADR-048）

## [0.1.0] - 2026-08-09

MVP 里程碑：九项硬性验收 M1–M9 全达成（36+ 检测器、融合评分、
质量门禁、修复闭环、报告引擎、Docker、CI 十阶段、M9 Demo）。
V1 收官：漂移引擎、跨表完整性、AI 修复候选、MCP 生态面、趋势 UI。

### 新增（Step 32–45，V1 收官）

- **Step 45**：跨扫描趋势 UI —— `/ui/trends` 质量分趋势页
  （纯函数数据层 `trends.build_trends`，ADR-045）
- **Step 44**：AI 修复候选 —— `repair propose --ai`，LLM 在
  检测器对应的操作白名单内生成操作/参数/理由（脱敏 + 审计 +
  缓存，ADR-044）
- **Step 43**：MCP stdio 服务器 —— 零依赖 JSON-RPC 2.0，
  `datasentry mcp` 7 工具供 LLM 代理调用（ADR-043）
- **Step 42**：模型异常检测 —— IsolationForest / LOF 单变量
  离群（`ScanConfig.detector_params`，scikit-learn 依赖，
  ADR-042）
- **Step 41**：模糊重复检测 —— 归一化分组（大小写/标点/全半角），
  uniqueness Level 3（ADR-041）
- **Step 40**：跨表外键完整性 —— `Contract.references` +
  `ForeignKeyViolationDetector`（DuckDB LEFT JOIN 孤儿行，
  ADR-040）
- **Step 39**：漂移引擎 —— `drift compare/latest`，两历史扫描
  schema/行数/分数/issue 分布四类信号（ADR-039）
- **Step 38**：DuckDB 文件连接器 —— `.duckdb` READ_ONLY ATTACH
  + `scan --table`（ADR-038）
- **Step 37**：契约导出 —— `contract export --as pandera|ge`
  （ADR-037）
- **Step 36**：CI 报告导出 —— JUnit / SARIF 两格式（ADR-036）
- **Step 35**：契约门禁修复证据释放 —— gate 报告携带修复建议
  （ADR-035）
- **Step 34**：电商订单域多文件场景演示（契约 + 门禁 + 修复闭环
  真实化，ADR-034）
- **Step 33**：产品 README 用户优先重构 + 路线图（ADR-033）
- **Step 32**：发布工程 —— wheel 构建、元数据、changelog
  （ADR-032）

### 新增（Step 25–31，V1 第一阶段收尾）

- **Step 31**：检测器插件 API v1 —— `load_plugin_detectors`
  目录动态加载（Detector 协议发现）、`<workspace>/plugins/`
  自动加载、CLI `datasentry detectors`（ADR-031）
- **Step 30**：危险规则批准安全阀门 —— `rules approve --file`
  重跑预运行复核，dangerous 规则需 `--force` 才激活（ADR-030）
- **Step 29**：Ollama 原生 `/api/generate` 接入 + 超时重试统一
  （`_post_with_retry` 共享辅助）（ADR-029）
- **Step 28**：自然语言 → 规则候选 —— propose 脱敏画像 → LLM
  严格 JSON → 预运行报告 → 候选落库待批 → approve 转正；
  `llm_cache` prompt 哈希复用（ADR-028）
- **Step 27**：LLM Provider 抽象（core 零网络接口）+
  确定性 PII 脱敏管线 + 审计闭环 `llm status/invocations`
  （ADR-027）
- **Step 26**：工程收尾 —— Apache-2.0 LICENSE、
  CONTRIBUTING.md、Makefile、Dockerfile + compose、
  CI 十阶段（ADR-026）
- **Step 25**：M9 Demo 单脚本（5000 行 5.4s，预算内可复现）
  （ADR-025）

### 新增（Step 1–24，MVP 主体）

- 36+ 检测器：缺失模式族（4 核心）、日期时间族（6 核心）、
  表示变体与编码（spelling/fullwidth/mojibake/invalid_numeric）、
  跨字段规则（受限安全表达式求值，ADR-015）等
- 证据驱动融合 + 六维质量评分（27 章归一化，ADR-013）
- 质量门禁 `scan --fail-on`（22 章场景 C，ADR-014）
- 修复闭环：propose → preview → apply → rollback（15 章，
  ADR-020）
- 报告引擎：JSON / Markdown / HTML 三形态（26 章）
- 多格式连接器：CSV / Parquet / JSONL / XLSX（7.1，ADR-019）
- 契约引擎（跨表/多文件约束，C-04）
- REST API（FastAPI 单工作区门面，ADR-023）+ Web UI
  （服务端渲染五核心页，ADR-024）
- 性能基准：1e6 行双档判定（20.4，ADR-021/022）

### 工程

- Python >= 3.12，DuckDB 执行引擎（ADR-005/007）
- 门禁：ruff + mypy --strict + pytest 覆盖率 >= 85%
  （当前 522 例，95%）

## [0.16.0] - 2026-08-14

V14 调度执行器分布式化：扫描任务可下发远端 worker 执行（单
worker 端点，共享 token 鉴权；多 worker 路由留给未来）。

### 新增（Step 90，远程执行器，ADR-090）

- **RemoteScanExecutor**：实现 ScanExecutor Protocol——把
  JobCommand 序列化 POST 到远端 worker 的 /rpc/execute（共享
  token 鉴权），同步等待 JobResult 回传；失败（网络/鉴权/远端
  错误/超时/契约不符）统一 ScanExecutionError，按既有
  retry/死信语义落库，Scheduler 零改动
- **契约宽松**：远端返回多余字段忽略；非 JobResult 形状判为
  契约错误
- 测试 9 例（uvicorn 真 HTTP 后台线程）：成功 / base_url 容错 /
  远端 422 / 契约不符 / 网络错误 / 超时 / 连接拒绝 / 跳过结果
  透传 / 多余字段忽略

### 新增（Step 91，worker 远端执行端点，ADR-091）

- **POST /rpc/execute**：接收 JobCommand → 本地执行扫描 → 回传
  JobResult；任何 DataSentry 实例均可充当远端执行节点
- **安全**：默认关闭（503）；`DATASENTRY_WORKER_TOKEN`（或
  create_app(worker_token=)）启用；X-Datasentry-Token 头常量
  时间比对（401）
- 错误映射：非法 body 422 / 执行异常 500（仅异常类型，不泄堆栈）
- 测试 7 例（禁用/缺 token/错 token/成功落库/422/500/环境变量
  后备）

### 新增（Step 92，端到端 + CLI worker，ADR-092）

- **datasentry worker**：一行启动远端执行节点（uvicorn +
  api 服务）；--host/--port/--token（或 DATASENTRY_WORKER_TOKEN
  环境变量）；未配 token 打印警告且 /rpc/execute 禁用
- **端到端**：调度端 Scheduler + RemoteScanExecutor（真 HTTP）
  → worker 远端执行 → run completed + scan history 落库；远端
  错误 → run failed（retry/死信语义不变）；同一实例可同时是
  API 服务与执行节点
- 测试 3 例端到端（成功/失败/服务并存）

## [0.17.0] - 2026-08-14

V15 多 worker 池与容错路由：执行面从单点升级为池化容错。

### 新增（Step 93，worker 池与失败转移，ADR-093）

- **WorkerPoolExecutor**：多 worker round-robin 派发 + 失败转移
  （节点失败/不可达 → 冷却 60s 并转移下一节点）+ 冷却防雪崩；
  全部失败统一错误（摘要含各节点错误）；单 worker 退化为直连
  语义
- **健康预检可选**：/health 探活过滤（默认关闭，直连转移兜底）
- ScanExecutor Protocol 与 Scheduler 零改动；测试 8 例（真 HTTP
  多 worker：轮询分发/远端失败转移/不可达转移/全失败摘要/冷却
  跳过/冷却恢复/健康过滤/空池拒绝）

### 新增（Step 94，调度端 worker 配置面，ADR-094）

- **DATASENTRY_WORKERS** 环境变量：`url:token;url:token` 分号
  分隔配置 worker 池（末位冒号分隔，兼容 `http://host:port`）；
  配置后调度器自动改用 WorkerPoolExecutor
- **零迁移**：未配置或全部非法 → 回退 LocalScanExecutor；
  非法条目跳过 + 告警不炸启动；datasentry-server 自动透传 env
- 测试 8 例（parse_workers 单元 / _build_scheduler 集成 / 端到端
  失败转移真执行落库）
