# Changelog

本项目的所有显著变更按时间倒序列出。格式基于
[Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)，
版本号遵循 [Semantic Versioning](https://semver.org/lang/zh-CN/)。

## [Unreleased]

V6 报告交互增强进行中（目标版本待定，阶段收尾统一升版）。

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
- Docker 一键启动（`datasentry-server` 入口）
