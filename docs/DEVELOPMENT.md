# DataSentry AI

> An evidence-driven, local-first AI copilot for detecting, explaining, validating, and safely
> repairing data quality problems.
>
> 一个以统计证据为基础、以 AI 为辅助、以人工审批为保障的数据质量检测与修复平台。

**状态**：开发中（Step 34 — 电商订单域多文件场景示例；MVP 九项硬性验收 M1–M9 全达成）。本仓库按《产品设计与开发 Prompt 完整版 v2.0》
推进，一次一个实施步骤，每步执行 8 步法（设计决策 → 实现 → 测试 → 修复 → 文档 → 变更摘要）。

## 为什么选择 DataSentry

| 能力 | 说明 |
|------|------|
| **证据驱动** | 36+ 确定性检测器（缺失/日期/编码/跨字段/离群），每个问题带统计证据链（样本、占比、置信度），非"AI 猜" |
| **AI 辅助规则** | 自然语言描述 → LLM 生成规则候选 → 预运行试算 → **人工批准才生效**（未批准规则永不出现在扫描集） |
| **安全修复** | 修复前 preview → apply → rollback 全流程，AI 只提方案，落库由你决定 |
| **质量门禁** | `scan --fail-on`：CI 里按严重度/得分卡发布（22 章场景 C），报告 JSON/Markdown/HTML |
| **本地优先** | DuckDB 执行引擎，1e6 行 10 秒级；LLM 可完全离线（未配置自动降级）或接本地 Ollama（数据不出机器） |
| **可扩展** | 检测器插件 API v1：`plugins/` 目录复制即用；规则引擎 12 算子期望语义 |

## 快速开始

```bash
pip install datasentry          # 或 uv sync（源码开发）
datasentry scan orders.csv      # 检测 → 评分 → 落库，一步完成
datasentry scan analytics.duckdb --table payments   # DuckDB 文件表（Step 38）
datasentry report export <run_id> --as html --output report.html
datasentry report export <run_id> --as junit --output junit.xml   # CI（每个 issue = failure testcase）
datasentry report export <run_id> --as sarif --output sarif.json  # GitHub Code Scanning / IDE
datasentry score latest         # 质量总分（0–100，六维）
datasentry scan orders.csv --fail-on high --threshold 80   # CI 质量门禁
```

LLM 辅助规则（可选，离线不配也能用）：

```bash
export DATASENTRY_LLM_PROVIDER=ollama DATASENTRY_LLM_MODEL=llama3   # 本地
datasentry rules propose "prices must be positive" --file orders.csv
datasentry rules approve <rule_id> --file orders.csv --force         # 危险规则显式确认
```

插件（可选）：把检测器 `.py` 放进工作区 `plugins/` 目录即自动加载，
`datasentry detectors` 查看注册表。

> 完整示例：`examples/demo/demo.py` 一键走通生成脏数据 → 扫描 → 报告 → 修复闭环（5.4s）。

## 命令速查

| 命令 | 作用 |
|------|------|
| `datasentry scan <file> [--table NAME]` | 检测 + 融合 + 评分 + 落库（CSV/Parquet/JSONL/XLSX/DuckDB） |
| `datasentry issues list` | 问题列表（按严重度/维度筛选） |
| `datasentry score latest` | 六维质量总分 |
| `datasentry report export <run> --as json\|markdown\|html\|junit\|sarif` | 报告导出（含 CI 格式） |
| `datasentry contract validate <file>` | 契约校验 |
| `datasentry contract export <file> --as pandera\|ge` | 契约导出（Pandera 代码 / GE ExpectationSuite） || `datasentry repair propose/preview/apply/rollback` | 修复闭环 |
| `datasentry rules propose/approve/list` | NL→规则候选 + 预运行审批（14.3/14.4） |
| `datasentry llm status/invocations` | LLM 配置状态与审计 |
| `datasentry drift compare <run_a> <run_b>` | 漂移：两历史扫描版本比较（schema/行数/分数/issue 分布） |
| `datasentry drift latest <dataset>` | 最近两次扫描漂移（不足两次 → 退出码 2） |
| `datasentry mcp` | MCP stdio 服务器（JSON-RPC 2.0，LLM 代理可调 7 工具） |
| `datasentry detectors` | 检测器注册表（内置 + 插件） |
| `datasentry-server` | FastAPI 服务 + Web UI（或 Docker compose 一键起） |

## 架构总览

```
┌──────────────────────────── datasentry（应用层）──────────────────────────┐
│  CLI │ REST API + Web UI │ SDK (DataSentry) │ rules_ai（NL→规则候选）    │
└───────────────┬──────────────────────────────┬───────────────────────────┘
                │                              │ LLM Provider（OpenAI/Ollama/Null）
┌───────────────▼──────────────────────────────▼───────────────────────────┐
│                 datasentry-core（领域层，零网络依赖）                       │
│   connectors（CSV/Parquet/JSONL/XLSX）→ detectors（36+ 注册表 + 插件）      │
│   → 融合 → 六维评分 → 质量门禁 → 报告引擎（JSON/MD/HTML）                   │
│   rules 引擎（期望语义取反 + 预运行）│ repair 引擎（propose/apply/rollback） │
│   privacy 脱敏（PII 掩码映射不落盘）│ storage（SQLite 元数据库，ADR-010）    │
└───────────────────────────────────────────────────────────────────────────┘
                执行引擎：DuckDB（只读 SQL 视图，11.10 受限求值）
```

## 与现有工具的位置

- **vs pandas-profiling / ydata-profiling**：只报告不修复；DataSentry 提供修复闭环、门禁与规则化
- **vs Great Expectations**：GE 强在数据契约测试套件；DataSentry 用检测器自动发现 + AI 辅助生成规则（预运行 + 审批），上手无需写 expectation；契约可一键导出为 GE suite / Pandera schema（Step 37，双向流动：DSL 是单一来源）
- **vs 商业 Data Observability**（Monte Carlo 等）：DataSentry 本地优先、数据不出机器、免费开源；云侧调度与协作归 V2

## 路线图

- **V1 已完成（0.1.0）**：MVP M1–M9 全达成 —— 36+ 检测器 / 融合评分 / 门禁 / 修复闭环 / 报告 / 契约 / API+UI / Docker+CI / M9 Demo / LLM 辅助规则（脱敏+审批） / 插件 API v1 / 发布工程
- **V2 方向**：云侧调度与协作、报告 HTML 交互增强（已落地：Step 49 交互表 / Step 60 联动导航 / Step 61 列画像节）、加密存储的 PII 还原、插件生态治理
- **V10 已完成（v0.12.0）**：全球化与扫描管线——CLI 全局 --lang
  （Step 74：`cli.*` 键域，report export 全局接管）、报告正文翻译
  （Step 75：issue 标题/描述/建议 zh 镜像，en 逐字不变）、MCP scan
  配置透传（Step 76：sampling/detectors/tags 与 CLI 同源）、增量
  画像（Step 77：本地文件 SHA-256 比对复用，无基准降级全扫）；
  CI 全绿 957 passed，mypy --strict 通过
- **V11 已完成（v0.13.0）**：三件套收官——证据级动态描述翻译
  （Step 78：29 处 f-string 模板化 + zh 镜像，en 逐字不变、JSON 契约
  面零改动）、调度任务 ScanConfig 透传（Step 79：JobCommand.config
  落库生效，与 CLI/MCP 同源）、增量画像列级 diff（Step 80：schema
  列签名变更时未变列复用上次 sidecar，仅新列聚合，无漏检）；
  CI 全绿，mypy --strict 通过；下一阶段候选：云侧调度与协作、
  PII 还原（加密存储）、插件生态治理、报告交互增强（Step 49/60/61
  已落地，持续迭代）
- **V12 已完成（v0.14.0）**：插件生态治理——插件清单（Step 82：
  plugin.yaml 元数据 + plugin install/uninstall 管理 + plugin list
  清单视图）、完整性校验（Step 83：SHA-256 锁 + 加载前校验 +
  plugin reaccept + integrity 状态）、  测试夹具（Step 84：fixtures
  声明 + plugin test 隔离执行 + 三态退出码）；插件 API v1 载荷
  零改动，旧平铺布局零迁移；CI 全绿（95.01% 覆盖），mypy --strict
  通过；下一阶段候选：云侧调度与协作、PII 还原（加密存储）、
  报告交互增强（Step 49/60/61 已落地，持续迭代）
- **V13 已完成（v0.15.0）**：云侧调度与协作——CLI job 子命令
  （Step 86：list/create/trigger/status/remove，非法 cron
  EXIT_CONFIG=2，与 MCP 同源同 store）、HTTP API 运行历史与
  webhook 验证（Step 87：GET /jobs/{id}/runs?limit=N +
  POST /jobs/{id}/test-webhook，远端状态可判读）、MCP 生命周期
  补全与历史保留（Step 88：job_update/job_remove 部分更新、
  prune_runs 按任务保留最近 100 条运行）；CLI / HTTP API / MCP
  三面 job 能力语义对齐，共用 SchedulerStore 无分叉；CI 全绿
  （95.01% 覆盖），mypy --strict 通过；下一阶段候选：PII 还原
  （加密存储）、报告交互增强、调度执行器分布式化
- **V14 已完成（v0.16.0）**：调度执行器分布式化——远程执行器
  （Step 90：RemoteScanExecutor 实现 ScanExecutor Protocol，
  JobCommand/JobResult JSON 契约 + X-Datasentry-Token 共享密钥，
  同步等待与 Local 语义一致）、worker 端点（Step 91：POST
  /rpc/execute 默认禁用 503、401 常量时间比对、422 契约、500
  仅类型名；任何 DataSentry 实例可作执行节点）、端到端与 CLI
  （Step 92：datasentry worker 一行启动执行节点；调度端真 HTTP
  委托 worker 执行，失败走既有 retry/死信）；ScanExecutor
  Protocol 与 Scheduler 零改动，多 worker 路由留给未来；CI 全绿
  （95.01% 覆盖），mypy --strict 通过；下一阶段候选：PII 还原
  （加密存储）、报告交互增强、多 worker 路由与任务队列
- **V15 已完成（v0.17.0）**：多 worker 池与容错路由——worker
  池（Step 93：WorkerPoolExecutor 实现 ScanExecutor Protocol，
  round-robin 整轮遍历 + 失败转移（节点失败/不可达 → 冷却 60s
  防雪崩并转移下一节点）+ 可选 /health 探活过滤 + reset；
  全部失败统一错误摘要；单 worker 退化直连语义）、配置面
  （Step 94：DATASENTRY_WORKERS="url:token;url:token" 环境变量，
  末位冒号 rsplit 兼容 http://host:port，非法条目跳过+告警；
  未配置/全非法 → 回退 LocalScanExecutor 零迁移）、端到端
  （真 api + 假 500 worker + 真 worker → trigger → 失败转移 →
  真扫描落库）；ScanExecutor Protocol / Scheduler /
  RemoteScanExecutor 零改动；下一阶段候选：异步任务队列与
  多 worker 并行执行、PII 还原（加密存储）、报告交互增强
- **V16 已完成（v0.18.0）**：异步任务队列与并行执行——线程池
  派发（Step 96：Scheduler max_workers>1 时 tick/trigger 异步
  派发 ThreadPoolExecutor 立即返回、多 job 并行；=1 同步语义
  零迁移；shutdown(wait=True) 优雅关闭接 worker.stop）、API
  配置（Step 97：DATASENTRY_MAX_WORKERS 环境变量、非法回退 1、
  app.state.scheduler 观测、服务退出等待 in-flight 无残留线程）；
  互斥/重试/死信/webhook 语义不变（SQL 层原子 claim）；下一
  阶段候选：PII 还原（加密存储）、报告交互增强、跨进程/多
  调度端队列
- **V17 已完成（v0.19.0）**：PII 加密 vault 管理面补全——REST
  面（Step 99：五端点 /pii/sessions 列表+摘要+还原+删除+
  rotate-key；缺 key 503 对齐 /rpc/execute disabled 语义、
  session 缺失 404、还原缺 text 422、轮换后旧 key 解密失败 503、
  DELETE 无需密钥；_ENDPOINTS 同步 +5）、MCP 面（Step 100：
  pii_sessions/pii_restore/pii_delete_session 三工具，15→18；
  描述注明显式授权语义，缺 key → ok:false 错误消息不崩；
  tools/list 严格断言 +3）、Web UI 面（Step 101：/ui/pii
  会话列表 + 还原表单，还原结果仅内存展示不落盘，缺 key 显示
  未配置提示；mask_text_pii 打码路径零改动）；CLI / REST /
  MCP / UI 四面缺 key 语义对齐（EXIT_CONFIG / 503 / 错误消息 /
  页面提示）；CI 全绿（95.01% 覆盖，1131 测试），mypy --strict
  通过；下一阶段候选：报告交互增强、跨进程/多调度端队列、
  vault 密钥的 UI 管理
- **V18 已完成（v0.20.0）**：PII vault 密钥与会话生命周期管理
  收尾——密钥轮换/设置完整透传（Step 102：REST rotate-key 可选
  body {"new_key"} 向后兼容、MCP pii_rotate_key 可选 newKey、
  UI 密钥管理卡片（状态+轮换按钮+设置表单）、四面缺 key 对齐、
  远程面不返回密钥材料）、会话按龄清理（Step 103：purge 四面
  ——CLI --purge --older-than / REST POST /pii/sessions/purge /
  MCP pii_purge_sessions / UI 清理表单；无需密钥、N<1 拒绝、
  不触碰存储 schema）；CHANGELOG 归一为 Keep-a-Changelog 倒序；
  CI 全绿（95.01% 覆盖，1155 测试），mypy --strict 通过；下一
  阶段候选：报告交互增强、跨进程/多调度端队列
- **V19 已完成（v0.21.0）**：跨进程/多调度端一致性——元数据存储
  跨进程加固（Step 105：显式 busy_timeout=5000；journal_mode=WAL
  移出 DDL 改 _enable_wal() 显式重试——该 pragma 独占锁不调用
  busy handler，两进程并发首开新库会立刻 BUSY（实测复现后根治）；
  pii 会话列表 rowid 二级排序同秒顺序稳定）、vault 密钥原子写 +
  会话冲突检测（Step 106：rotate-key tmp+os.replace；save_mapping
  解密比较明文——相等幂等跳过、key 失配降级重写、不等抛
  PIIMappingConflictError 拒覆盖；llm status 加 key_fingerprint/
  key_file 感知字段）、多调度端互斥跨进程证明（Step 107：
  SchedulerStore 原子抢占设计不变，subprocess 并发测试 4 例证明
  claim 互斥与调度端写会话可被主进程感知）；CI 全绿（94.94% 覆盖，
  1175 测试），mypy --strict 通过；下一阶段候选：报告交互增强、
  调度端远程执行器细化
- **V20 已完成（v0.22.0）**：调度端远程执行器细化——超时细分 +
  传输层重试退避 + 错误分类（Step 108：connect/read 超时独立配置、
  retries+指数退避+可注入抖动，仅网络错误与 408/429/5xx 重试、
  4xx/契约错误立即失败，ScanExecutionError 增 category/retryable；
  边界：执行器内重试只治传输瞬时故障，任务级重试/死信仍在
  Scheduler）、worker 健康探测 preflight（Step 109：公开信息面
  GET /rpc/health 恒 200 {service,version,worker} 与 token 数据面
  分离；health() + execute(preflight=True) 快速失败，默认关向后
  兼容）、远端报告回传（Step 110：GET /rpc/reports/{run_id} token
  鉴权 401/503/404；执行器 report_dir 自动拉回本地，尽力而为与
  ADR-070 一致，未配置零请求）；CI 全绿（94.94% 覆盖，1204 测试），
  mypy --strict 通过；下一阶段候选：报告交互增强、调度端
  cancel/异步协议、worker 远程执行器 CLI 配置化
- **V21 已完成（v0.23.0）**：worker 远程执行器 CLI 配置化——
  job trigger 远程选项（Step 111：--remote-url/--remote-token/
  --remote-retries/--remote-preflight，无 --remote-url 本地行为
  零变化、缺 token EXIT_CONFIG；注入 RemoteScanExecutor + 报告
  自动回传本工作区 .datasentry/reports；RemoteScanExecutor 增
  构造 preflight 参数向后兼容；失败语义不变——Scheduler 落库
  run failed）、ping 健康可见性（Step 112：顶层 ping URL
  [--token] [--timeout] 探公开 /rpc/health 信息面，输出
  {url,service,version,worker,ok}，与 worker 叶子命令解耦）、
  跨 workspace 物理隔离（Step 113：/rpc/execute worker 改用
  自身 project 执行——scan 历史落 worker 库，JobCommand.project
  保留为契约信息字段，同 workspace 部署零变化；跨 workspace
  e2e 证明 scan 落 worker 库 + 调度端库无该记录 + 报告回传 +
  run completed）；CI 全绿（94.94% 覆盖，1214 测试），mypy
  --strict 通过；下一阶段候选：cancel/异步协议、报告交互增强、
  webhook 事件去重
- **V22 已完成（v0.24.0）**：调度端 cancel 语义闭环 + 远程 cancel
  协议——调度端 cancel（Step 114：RunStatus.CANCELLED +
  SchedulerStore.cancel_run 事务内原子判定 running run → cancelled +
  job idle；Scheduler.cancel + CLI `job cancel`；**结果丢弃语义**——
  finish_run 事务内读 run 状态，已 cancelled 则跳过全部更新，
  竞态由 BEGIN IMMEDIATE 串行化；schema v7→v8 重建 job_runs 放宽
  status CHECK（数据原样迁移 + 并发迁移防竞态））、远程 cancel 协议
  （Step 115：JobCommand.run_token=调度端 run_id 每次触发注入；
  worker 端 in-flight registry（Lock+dict）+ POST /rpc/cancel
  （503/401/404/200 打标）；扫描完成已打标 → JobResult.cancelled:true
  作废回执；RemoteScanExecutor.cancel 尽力而为仅警告；_ENDPOINTS 29）、
  跨 workspace 全链路证明（Step 116：触发中 cancel → run cancelled +
  远端通知 + worker 跑完 + 作废回执 + 调度端结果丢弃；边界文档化——
  worker 扫描线程无法强杀，落 worker 库记录不可回收）；CI 全绿
  （94.94% 覆盖，1226 测试），mypy --strict 通过；下一阶段候选：
  异步触发协议（异步化 cancel/状态查询）、报告交互增强、webhook
  事件去重
- **V24 已完成（v0.26.0）**：交互式终端界面（ADR-118）——
  Textual TUI：`datasentry`（无子命令）/ `datasentry ui` 启动，
  四视图（工作台/扫描/问题/修复），扫描后台线程 + 不定进度条
  完成自动跳转，问题证据链详情，修复工作台 propose/preview/
  apply/rollback（沿用同一客户端语义：AI 建议、人工审批、可回
  滚）；Pilot 无头测试 +5（tests/test_tui.py）；踩坑记录——
  Textual 8 的 run_test 不支持 thread worker（改 asyncio.
  to_thread）、TabPane 内 widget 获焦会回写 active（切 tab 后
  把焦点移入目标 pane 的表格）、pilot.click 对超屏 widget 不
  命中（Input 1fr 挤爆按钮，显式 width 修复）；CI 全绿
  （94.94% 覆盖，1234 测试），mypy --strict 通过；v0.26.1 补丁——
真实 TTY 端到端（pty 脚本）暴露三处可用性缺陷并修复：① 数字键
切视图后焦点需进目标控件（否则 Input 吞掉后续按键）；② 输入框
回车需处理 Input.Submitted 才触发扫描；③ 完成信息写 flash 会被
立即切 tab 抢走下一帧渲染而永不上屏——改全局 notify()（跨视图
可见）。调试方法：文件日志（写 /tmp，避免被屏幕重绘覆盖）定位
到 worker 正常、消息正常，最终确认是渲染时序问题；v0.27——Web 批量结果视图与基准（V27）：

- Web 批量部分失败语义：_expand_scan_paths 过滤缺失文件并记入
  _last_missing_paths；ui_create_scan 将缺失路径并入 failed 列表
  （error="file not found"）——避免批量输入含缺失文件时静默降级
  成单文件跳转。全部失败 404 错误页（detail 列出各文件原因）；
  部分成功 303 /ui/scans + 汇总横幅。
- _last_batch 为模块级消费式横幅：POST /ui/scans 写入，GET
  /ui/scans 渲染后置 None（测试跨用例共享，注意先后依赖）。
- 扫描列表每行渲染六维迷你条（_dim_strip，色同趋势图，悬停
  title 显示维度分数），QualityScore.dimensions 缺失时不渲染。
- 性能基准 scripts/benchmark.py：标准库零依赖，生成脏数据
  (10k/100k/300k 行) 端到端计时，markdown 表输出；实测
  Apple Silicon 300k 行约 9.5s（31.5k 行/s）。
- 横幅文案走 i18n 表（ui.batch_done / ui.batch_done_partial，
  .format 占位符 {files}/{issues}/{score}/{failed}）。

v0.26.2——Web UI 扫描实时进度（V25）与批量扫描（V26）：

- V25 进度模式：api.py 模块级线程安全进度槽
  _SCAN_PROGRESS（path → 快照，含 _ts 时间戳），on_progress
  回调写入，GET /scans/progress?path= 与 /scans/progress/latest
  轮询读取；前端零框架内联 JS 轮询渲染进度条。快照含 path
  字段供批量场景显示当前文件。
- V26 批量模式：CLI/TUI/Web 三端统一路径解析
  （逗号/分号/换行分隔 + glob 展开 + 去重保序）：
  CLI 走 _expand_cli_paths（缺失路径保留原样交给 scan_file
  报错）；Web 走 _expand_scan_paths（缺失路径过滤并记入
  _last_missing_paths 供错误页）；TUI 走 _parse_paths。
  批量语义：部分失败不中断，汇总 errors 数组；退出码
  4 = 有文件失败，1 = 任一文件门禁不通过，0 = 全成功。
- CLI 批量输出：json 模式 data = {batch: [...], errors, ...}；
  text 模式逐文件一行汇总（dataset: run=.. issues=.. score=..）。
- 测试注意：进度槽是模块级全局，跨 TestClient 用例共享，
  需要隔离时直接 _SCAN_PROGRESS.clear()。
- ScanPoint 增加可选 dimensions 字段（默认 None 向后兼容），
  趋势页据此渲染六维 SVG 折线与数值表。

v0.27.1——发布教训：版本 bump 脚本曾用全局字符串替换
（'0.27.0'→'0.27.1'），误伤 uv.lock 中恰好同版本号的第三方
依赖（typer==0.27.0 → 0.27.1 不存在 → CI 依赖安装 404）。
此后 bump 必须只改 datasentry-ai 自己的 pyproject/__init__/段
（name="datasentry-ai" 块内的 version 行），不可全局替换；
bump 后立即 grep 确认无其他包版本被改。
v0.27.0——现代 CLI UX：v0.27.0——现代 CLI UX：
命令面板用 App.COMMANDS + Provider（search/discover 双实现）；App 级
j/k/? 绑定仅在不聚焦输入框时生效（Input 自然吞字符）；Ctrl+Tab 在
run_test 可驱动、pty 需专有转义序列（真实用户键盘没问题）；扫描
进度走 to_thread 内 call_from_thread(post_message)（线程安全）；
状态条必须给 TabbedContent 显式 height:1fr 否则被挤出屏幕（默认
auto 会超屏裁剪）；CSV 预览 debounce 0.6s + asyncio task 取消防
陈旧覆盖；Textual 消息分发查类属性——测试 monkeypatch 需改类级；v0.26.2——回滚防误触：
回滚按钮移出主操作行、独立"危险区"（Vertical 需显式 height，
Horizontal 里 Label 会挤掉按钮），二次确认弹窗（复用 ModalScreen
模式），且确认弹窗默认焦点=取消（AUTO_FOCUS，回车=取消，防误
触）——退出确认同步处理

- **V23 已完成（v0.25.0）**：CLI 可用性——score `latest` 解析为
  最近一次扫描（started_at 倒序首条，空库 EXIT_CONFIG）+ score
  信封补 `scan_run_id` 字段（与 scan 信封命名对齐）+ `issues list
  --limit N` 截断（ADR-117）；本轮由增长期 demo 实测摩擦驱动
  （录制 demo 时无法快速取最近评分）；CI 全绿（94.94% 覆盖，
  1229 测试），mypy --strict 通过

---

## 开发附录（实施过程笔记）

### 目录结构

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

### 开发者快速开始

```bash
uv sync
make lint && make type && make test   # 或 make check（含覆盖率门禁）
```

### 设计文档

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

- 布局（ADR-010 二元化）：项目数据 → `<workspace>/.datasentry/metadata.db`；全局配置/缓存 → 平台数据目录 `datasentry/`（macOS: `~/Library/Application Support/datasentry/`，`DATASENTRY_HOME` 可覆盖）；子目录：`reports/`（报告导出，ADR-010）、`repairs/`（修复副本，ADR-020）、`profiles/`（扫描期画像 sidecar，ADR-061）
- `storage/schema.py`：docs/04 草案 20 表 DDL 冻结（MVP 占位表建表无写路径）+ `SCHEMA_VERSION`（PRAGMA user_version）幂等迁移，Alembic 归 V1（ADR-012）
- `storage/store.py`：`MetadataStore`（WAL + foreign_keys ON + 单连接写锁）——`save_scan` 单事务落 scan_runs/detector_runs/issues/evidence；`list_scan_runs`/`get_scan_run`/`get_detector_runs`/`get_issues`；数据集自动注册（local 项目占位）；级联删除由 DB 外键保证
- `detectors/runner.py` 新增 `run_scan()`：完整扫描入口，组装 `ScanRun`（fingerprint/ReproducibilityInfo/issues_count/任一检测器失败→status=failed）
- ADR-012 偏差记录：scan_runs.fingerprint 冗余 JSON 列（草案经 dataset_versions 关联，MVP 无版本写路径）

## CLI / SDK 闭环（Step 10/18，22-23 章）

- `src/datasentry/`：SDK 客户端 + CLI（`datasentry` 命令已注册到 PATH，`uv sync` 安装）
- SDK（23.1）：`DataSentry(project=...)` 工作区门面 —— `scan_file`（导入→扫描→评分→落库）、`list_issues`（--severity 过滤）、`get_scan`/`get_detector_runs`、`export_report`；构造即确保 `.datasentry/` 目录与 `.gitignore` 条目（ADR-010）
- CLI（22.1）：`init` / `scan` / `issues list|show` / `report export` / `contract validate` / `contract export`；全局 `--project` / `--format text|json` / `--seed` / `--version`
- JSON envelope：`{"ok", "command", "data", "warnings", "llm_usage"}`；退出码 0 成功 / 2 配置错误 / 3 执行错误 / 4 数据源不可用（1 保留质量门禁）
- 检测器类型收窄：字符串类检测器（missing token/placeholder 等）经 `supports()` + `string_columns()` 限定，数值列不再触发 `trim()` Binder 错误

## 多格式文件连接器（Step 18，7.1 + ADR-019）

- `connectors/file_based.py`：`FileDataHandle` 共享基类（schema/read_sample/sql_aggregate/count_rows/fingerprint/warnings 公式注入扫描/close）；CSV 连接器保持独立（专属编码探测/分隔符嗅探，不重构）
- `connectors/duckdb.py`（Step 38，V1 数据库型落地）：`.duckdb` 文件 READ_ONLY ATTACH + 表视图，`scan --table` 必填（`--table [schema.]table` 经 options.schema）；流式批读走 executor `execute_stream`（to_arrow_reader 按 batch 断批）；标识符双引号转义防注入，缺表名 → 退出码 2
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

- `reporting/`：`build_report()` = 26.2 规范 JSON 报告（报告头 + scan + detector_runs + issues + quality），SDK `export_report()` 与 CLI `report export --as json` 无差异消费；Markdown 表格化摘要（26.1）、HTML 自包含单文件（内嵌 CSS，8 节：Executive Summary/Quality Score 含 27.3 维度条形图与扣分悬停/Issue Breakdown/Critical Findings 等，Drift/规则/修复历史归 V1）；Step 49/60（ADR-049/060）交互增强：Issue Breakdown 交互表（severity/维度筛选、排序、搜索、详情折叠、分页）+ expand/collapse all + 评分条钻取（`data-dim-link` 联动维度筛选）/ 发现定位（`.finding-link[data-issue-id]` 聚焦高亮）/ 粘性导航 scrollspy + 回到顶部 —— 原生 JS 内联零依赖、事件委托、`#issues._render` 联动契约、无 JS 降级锚点跳转；Step 61（ADR-061）Column Profiles 交互节：`render_html(..., profiles=...)` 消费扫描期画像 sidecar（`<workspace>/.datasentry/profiles/<run_id>.json`，`project_profiles_dir`）——可排序画像表（null/unique/distinct/mean/median/std）、迷你空值条、语义/PII 徽标、top 类别 chips（显示层 `mask_text_pii` 掩码；sidecar 保留完整证据链，不进 26 章 JSON 契约）；Step 62（ADR-062）修复建议预览：`reporting/suggestions.py` 纯函数 `suggest_repairs`（detector_ids → ≤3 条确定性建议，镜像修复引擎映射，rationale 掩码）随 issue 行 JSON 下发，详情行内联「Repair suggestions」块（无 server/LLM 依赖，未知检测器诚实降级）；Step 63（ADR-063）深色模式：`_CSS` 全量变量化（:root 亮色默认 + `@media (prefers-color-scheme: dark)` 暗色覆盖 + `@media print` 强制亮色），趋势 SVG 走 `.trend-line`/`.trend-dot` 类，评分条六色段硬编码（双主题可读）；测试强不变量：CSS 内 hex 只允许出现在变量定义行；Step 64（ADR-064）报告间对比：`trends.build_comparison(scans, dataset_id, current_run_id)` 纯函数（同数据集 + completed + 有质量分过滤、时间升序、Δ 对前一 run 首行 None、不足 2 run 返回 None）→ `render_html(..., comparison=...)` 静态 Run Comparison 表（当前 run `cmp-current` 高亮 + `cmp-badge`、Δ 按符号 `cmp-up`/`cmp-down` 类上色、维度列跨行并集、严重度列仅出现过的、全单元格 escape、节与导航锚点条件渲染）；CLI/api 接线 dataset_id 取自 26 章报告头；V7（ADR-065~067）趋势/画像数据面补全：`datasentry trend list [--dataset-id]` 与 `GET /trends[?dataset_id=]` 同源同构（`build_trends` 每项 + delta/direction/latest_score/latest_issues 摘要，空数据合法空列表非错误）、`GET /scans/{run_id}/profiles` 画像 sidecar 原样 JSON（缺失 404）；`/ui/trends` 页内联 SVG Sparkline（`_sparkline` min-max 归一化 polyline，零依赖零 JS）+ run 行 Δ 列（`_delta_cell`，正绿负红、0/首行灰）；V8（ADR-068~070）MCP 数据面 + 本地化 + 报告推送：MCP 增 `trends_list`/`profiles_get`/`comparison_build` 三工具（工具清单 10 → 13，错误风格与 job_create 一致）；本地化 `reporting/i18n.py`（L10N en/zh + `t(lang, key)` 双回退，`report export --lang zh` 与 `/ui/*?lang=` 覆盖 HTML/Markdown/交互表/趋势 SVG/列画像框架文案，26 章 JSON 结构化键不动）；调度报告推送 `export_report`（`POST /jobs` 透传，扫描后导出 HTML 到 `.datasentry/reports/<run_id>.html`，webhook 载荷追加相对 `report_path` + `report_size`，导出失败仅记日志不影响 run）
- 报告默认落点 `<workspace>/.datasentry/reports/<run_id>.<ext>`（ADR-010），`--output` 可覆盖；HTML 全字段转义（XSS 安全）
- `scoring/gate.py`：`QualityGateEvaluator`（22 章场景 C）——`fail_on` 精确严重度集合、`maximum_failed_rows_ratio`（受影响行比例上限，max over issues）、`maximum_issues` 按严重度上限；`require_repair_validation` MVP 不支持时显式失败
- CLI：`scan --fail-on SEV [--max-failure-ratio R]` 激活门禁，失败退出码 1；`report export RUN_ID --as json|markdown|html|junit|sarif [--output PATH]`（Step 36：JUnit 每 issue 一个 failure testcase、SARIF 2.1.0 rules+results，CI 集成格式）

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

## 修复引擎 CLI/SDK 接入（Step 21，12.5 章持久化闭环）

- schema v2：`repair_proposals` 表转正并新增 `issue_type` 列（v1→v2 幂等迁移，`_ensure_column` 按 `PRAGMA table_info` 索引判断）、`repair_runs` 新增 `proposal_id` 列（apply 关联提案）
- `store.py`：`save_repair_proposal`/`get_repair_proposal`/`save_repair_run`/`get_repair_run`/`list_repair_runs`/`get_issue_by_id` + 反序列化 helpers（RepairProposal/RepairRun 与 engine 模型字段对齐）
- SDK 门面 `client.py`：`repair_open`（按路径后缀推断 CSV/JSONL/Parquet/XLSX）→ `repair_propose`（落库提案）→ `repair_preview` → `repair_apply`（落库 run + 回滚产物）→ `repair_rollback` → `list_repair_runs`
- CLI：`datasentry repair` 子命令组 propose / preview / apply / rollback / list（`--file` 或 `--issue_id` 定位），执行期错误统一退出码 3
- 测试 313 → 324：新增 tests/test_repair_cli.py 11 例（SDK 提案落库核对、preview 规则降为 0、apply→rollback 副本存在、未映射 issue 拒绝提案、CLI 全链路）

## 覆盖率门禁（Step 22，37.5 阶段 4 → M8）

- `make test` 固化 `--cov=datasentry_core --cov-fail-under=85`（M8 ≥85% 进门禁，低于则退出码非 0）
- 补齐薄弱模块：`storage/paths.py` 73% → 100%（新增 tests/test_paths.py：三平台布局 macos/linux/win32、XDG fallback、DATASENTRY_HOME override、expanduser）
- `connectors/file_based.py` 80% → 97%（共享句柄边界：path None 抛错、`read_sample` none/time_based(带/不带 time_column)/n<1、sampled 指纹、警告列表缓存与 _WARNING_CAP 截断、close 后使用、抽象契约 NotImplementedError）
- 核心包覆盖率 95% → **96.42%**（3298 stmt），测试 324 → 336；README 状态行更新为 Step 22

## REST API（Step 23，22/23 章 HTTP 面 + ADR-023）

- `src/datasentry/api.py`：`create_app(project=...)` 单工作区门面——绑定一个 `DataSentry`，全部端点复用 CLI/SDK 同一条扫描→落库→修复闭环（无第二套领域逻辑）
- 13 个端点：`GET /health`、`POST /scans`（201 + ScanResponse）、`GET /scans`、`GET /scans/{run_id}`、`/issues`、`/report`（26 章规范 JSON）、`/score`（27 章总分）、`/issues`（severity 过滤）、修复组 `POST /scans/{run_id}/repairs/propose|preview|apply`、`POST /repairs/{run_id}/rollback`、`GET /repairs`
- 同步端点 MVP（扫描 ≤60s@1e6 无需任务队列），异步 Job 归 V1（ADR-023）；错误映射 FileNotFoundError/KeyError→404、ValueError→422、其余→500，body 统一 `{"ok": false, "detail"}`
- `DataSentry` 新增 `list_scan_runs()`（此前仅 store 层）；新依赖 fastapi/uvicorn（运行）、httpx（测试）；mypy 清理 src 层遗留（dict 泛型/cast/yaml 存根），全仓 68 文件 0 错误
- 测试 336 → 345：tests/test_api.py 9 例（健康探针/全闭环扫描/404/评分/报告/修复 propose→apply→rollback/未映射提案 None）

## Web UI 核心页（Step 24，docs/03 1.2 + ADR-024）

- `src/datasentry/ui.py`：服务端渲染（与 reporting/html.py 同一零依赖风格——内嵌 CSS、无前端构建链、无 JS 框架），所有输出经 `escape()` 转义（XSS 安全）
- 三个核心页：`/ui/`（首页：工作区概览 + ScanRun 表 + 新扫描表单）、`/ui/scans/{run_id}`（Dataset Overview + Issue Center，severity 过滤）、`/ui/scans/{run_id}/issues/{issue_id}`（修复工作台：propose → preview 面板 → apply → rollback 链接，15 章闭环）
- 表单提交走 POST + 303 重定向（PRG 模式）；Column Explorer / 跨扫描趋势归 V1（MVP 只做问题定位闭环，ADR-024）
- `DataSentry` 新增 `get_issue()`（修复工作台按 ID 取 Issue）；新依赖 python-multipart（Form）
- 测试 345 → 355：tests/test_ui.py 10 例（首页空/有数据、scan 详情、severity 过滤、404、工作台 propose/apply/rollback 全流程、未知 action、表单扫描、列名 XSS 转义）

## M9 Demo（Step 25，34 章 Demo < 3 分钟）

- `examples/demo/demo.py`：一键走通全流程（无 LLM 完全离线）——生成脏数据 CSV（前后空白/大小写混用/缺失占位/离群价/非法日期）→ `scan_file`（36 检测器 + 融合 + 评分 + 落库）→ 导出 JSON + HTML 报告 → 修复闭环（propose → preview → apply → rollback，15 章）→ 打印各阶段耗时与 PASS/FAIL 判定
- 默认 5000 行固定种子（可复现）；实测 **5.4s**（预算 180s，余量 33×）；`--rows`/`--out` 可配
- 测试 355 → 358：tests/test_demo.py 3 例（子进程全流程 + 预算断言、固定种子数据可复现、空数据边界）

## 工程与生态收尾（Step 26，42 章验收补全）

- **License**：Apache-2.0（LICENSE 全文 + pyproject 声明）
- **贡献指南**：CONTRIBUTING.md（门禁表、代码约定、提交规范、新检测器流程）
- **Makefile**：`type` 补跑 `src/datasentry`（此前只跑 core，门禁实际 69 文件）；新增 `demo`/`bench`/`check-all`（门禁 + M9 Demo + 性能基准一条命令）
- **Docker 一键启动**：Dockerfile（uv 官方镜像多阶段，无 dev 依赖）+ docker-compose（端口 8000，workspace 卷挂载）；`datasentry-server` 入口（api.main，支持 `DATASENTRY_PROJECT` 环境变量）；已实测 build + health + UI + API 全通
- **CI 十阶段**：.github/workflows/ci.yml —— ruff lint / format / mypy --strict / pytest+覆盖率门禁 / 覆盖率工件上传 / M9 demo / 1e6 行基准 / CLI smoke / API+UI smoke / 产物检查

## V1 第一阶段：LLM Provider 抽象 + 脱敏管线（Step 27，38 章安全子集）

- **脱敏管线**（`datasentry_core/privacy/redactor.py`，38 章「AI 不接收未经授权的完整数据」）：确定性启发式 PII 识别（email / 中国手机号 / 身份证 / IPv4 / URL），占位符 `{{REDACTED:<kind>:<n>}}` 替换 + 进程内映射表可还原（`restore`）；`mask_rows` 批量掩码、`mask_profile` 掩码画像 examples/top_categories；同输入恒同输出（LLM 缓存可复用）；映射表不落盘（加密存储归 V1 后续）
- **LLM Provider 抽象**：core 层零网络依赖接口（`datasentry_core/llm/provider.py`：`LLMProvider` Protocol + `LLMRequest`/`LLMResponse`/`LLMError` 异常层次）；应用层实现（`src/datasentry/llm_providers.py`）：`NullProvider`（未配置显式降级）、`OpenAICompatibleProvider`（Chat Completions 兼容 + Bearer）、`OllamaProvider`（本地 /api/generate）；超时重试（默认 2 次）、HTTP/JSON 形态校验（`LLMSchemaError`）
- **配置**：`DATASENTRY_LLM_PROVIDER/MODEL/BASE_URL/API_KEY` 环境变量优先，其次全局 `config.json`；未配置默认 null（现有离线行为不变）
- **审计闭环（13.11）**：`llm_invocations` 表读写（store.record_llm_invocation / list_llm_invocations，含 masked_sample_count / injection_flagged）；CLI 新增 `datasentry llm status`（配置状态 + 最近调用）+ `datasentry llm invocations`（审计明细，不含 prompt 原文）
- 测试 358 → 381：tests/test_llm_ai.py 23 例（PII 识别/确定性/往返/重叠掩码、provider 成功/HTTP 错误/Schema 失败/未配置、env 配置合并、审计落库顺序与 limit）
- 未配置时全流程行为不变：`llm status` 显示 configured=false，扫描仍纯离线可复现（ADR-014 不变）

## 规则引擎预运行与 NL→候选审批闭环（Step 28，14.3/14.4）

- **规则预运行引擎**（`datasentry_core/rules/engine.py`）：`Rule.when` 为「期望条件」，预运行时自动取反生成违规子句（equals→`col <> $1`、gt→`col <= $1`、between→`col NOT BETWEEN`、not_in→`col IN`、not_null→`col IS NULL` 等 12 算子）；`run_preflight` 在**批准前**试算样本：schema/列存在校验、`dangerous` 标记（违规行占比 > 0.5）、`sample_run` 违规明细与示例行；参数用 DuckDB 命名参数绑定（`$1`…`{"1": v}`）
- **NL→规则候选**（`src/datasentry/rules_ai.py` `RuleProposalService`）：`rules propose "prices must be positive"` → 采样画像（每列 3 值）→ ADR-027 脱敏（占位符内嵌掩码统计）→ LLM 严格 JSON（pydantic 白名单校验：RuleType 9 类 / Severity / 算子 / 列存在）→ 逐候选预运行 → **候选 `enabled=False` 落库**（14.4：未批准规则永不出现在扫描集）→ 审计 + `llm_cache`（prompt sha256 命中跳过调用）
- **CLI**：`rules propose "<描述>" --file/--budget`、`rules approve <rule_id>`（危险规则需 `--force`）、`rules list`（候选/生效分开展示）；未配置 LLM 显式报错 exit 3，离线行为不变
- 测试 381 → 394：tests/test_rules_ai.py 13 例（期望语义取反/命名参数绑定/危险标记/缓存命中/审计/全链路 mock 服务冒烟），覆盖率 96.39%

## Ollama 具体接入与超时重试统一（Step 29，W13 前置项）

- **Ollama 接入定案**：`OllamaProvider` 用原生 `/api/generate`（非 OpenAI 兼容层），默认 `http://localhost:11434` 零密钥；模型必填在构造期校验；响应缺 `response` 字段/非字符串抛 `LLMSchemaError`
- **重试统一**：`_post_with_retry` 共享辅助（超时按 `max_retries` 重试，默认 2 次；HTTP 状态/网络/JSON 错误不重试，错误消息统一 `timeout after N attempts`）——补齐 ADR-027 承诺在 Ollama 侧未落实的不一致
- **真实接入验收**：本地 HTTP 模拟 `/api/generate`（真实 TCP，非 MockTransport）全链路——propose 预运行 1 违规行 → 候选落库 → approve 激活 → 二次 propose 缓存命中不再请求 → 审计 `provider_id=ollama`；CLI 子进程冒烟同链路全通（`DATASENTRY_LLM_PROVIDER=ollama` + `DATASENTRY_LLM_BASE_URL`）
- 测试 394 → 398：Ollama 超时重试成功/重试耗尽/HTTP 不重试 3 例 + 真实接入 E2E 1 例；ADR 待定表 Ollama 项标记完成

## 危险规则批准安全阀门（Step 30，14.4 强制确认流）

- **approve 真实复核**：`rules approve <id> --file <data>` 对目标数据**重跑预运行**（非信任 propose 快照——数据在提案与批准之间可能变化）；复核为 dangerous（违规行占比 > 0.5）且未 `--force` → 拒绝激活（exit 3），规则保持 `enabled=0`
- **`--force` 显式知情确认**：危险规则须用户显式带 `--force` 才批准（14.4 安全阀门，非绕过）；错误消息含失败行统计（`3/5 rows, ratio 0.60 > 0.5`）
- **兼容**：不带 `--file` 保持原 approve 语义（跳过复核直接激活）；store 新增 `get_rule` 单条读取
- 测试 398 → 401：拦截/强批/安全复核通过/无文件跳过 4 例；修正 rules_ai.py docstring 与实现不一致（候选落库 enabled=0，非「只展示不落库」）

## 检测器插件 API v1（Step 31，4.8 扩展约束 / ADR-031）

- **加载机制**（`datasentry_core/plugins.py`）：`load_plugin_detectors(registry, dirs)` 目录扫描 `*.py`（跳过私有/缓存文件）→ 动态 import → 发现实现 `Detector` 协议（detector_id/detector_version/quality_dimension/supports/detect/metadata）的类 → 无参实例化注册；文件按名排序确定性加载
- **失败即报错**：import/实例化/ID 冲突 → `PluginLoadError`（含文件定位），不静默；非检测器模块属性忽略
- **自动加载**：`DataSentry` 打开工作区即加载 `<workspace>/plugins/`（不存在跳过）；CLI `datasentry detectors` 列出注册表（内置+插件：dimension/version/enabled）
- **示例**：`examples/plugins/example_detector.py`（负值检测插件，复制进 plugins/ 即生效）——端到端测试验证扫描命中 `plugin_negative_value` issue
- **安全边界**：插件与本机内置检测器同权（本地可信代码，非沙箱）；受限表达式求值（11.10/ADR-015）不适用于插件模块
- 测试 401 → 408；ADR 待定表清零（插件 API v1 稳定性承诺=V1 前置项落地）

## 发布工程（Step 32，0.1.0 里程碑 / ADR-032）

- **双包构建**：`datasentry-core`（引擎，零网络）+ `datasentry`（CLI/API/UI）hatchling 打包；`uv build` 四产物（sdist+wheel × 2）验证通过；dist/ 不入库
- **元数据面**：readme（PyPI 首页）/keywords/classifiers（Alpha、Python 3.12/3.13、Apache-2.0）/urls 补齐；本次构建发现 core 缺 README 即构建失败——发布前冒烟的价值实证
- **干净环境验收**：全新 Python 3.12 venv 安装本地 wheel → `datasentry --version` → `scan`（6 issues / score 94.8）→ `report export` HTML 全链路通过；importlib.metadata 校验 keywords/classifiers/readme 完整
- **CHANGELOG.md**：Keep a Changelog 格式，0.1.0 汇总 Step 1–31 全部变更
- 测试 408 例不变（发布面无逻辑变更），门禁 3 连绿

## MCP Server（Step 43，LLM 生态集成面）

- `datasentry mcp [--project DIR]`：零依赖自实现 JSON-RPC 2.0 over stdio（MCP 2024-11-05 核心子集：initialize / tools/list / tools/call / ping），供 Claude Code 等 LLM 代理以 stdio 方式挂载
- 7 工具：scan_file / list_issues / quality_score / drift_compare / drift_latest / detectors_list / contract_validate——全部复用 DataSentry SDK（与 CLI/REST 同源）
- 输出统一 JSON 序列化（datetime/Path 安全）；工具异常 → JSON-RPC error；未知工具 → -32602
- 测试 `tests/test_mcp_server.py` 11 例（握手/工具/真子进程 stdio 循环）

## 趋势 UI（Step 45，跨扫描质量趋势，V1 收官）

- `datasentry ui` → `/ui/trends`：每数据集的质量分跨扫描趋势（条形图 + 表格 + up/down/flat 徽章）
- 数据层 `trends.build_trends` 纯函数：只消费 ScanRun 列表（quality_score 随扫描落库，历史保留原权重），按数据集最近活动排序
- 与漂移引擎分工：趋势页是轻量概览面；完整信号（行数/分数/覆盖/异常）走 `drift compare/latest`

## AI 修复候选（Step 44，规则引擎兜底 + LLM 参数/理由）

- `datasentry repair propose <issue_id> --file <data> --ai`：规则引擎无提案时，LLM 生成修复候选（35 章闭环的 AI 增强）
- 安全边界：AI 只能从该 issue 检测器对应的操作集内选择（`_CONTEXT_OPS`，与规则引擎同款 5 操作）；仅 clip_value 接受数值边界，其余强制空参数——杜绝任意表达式注入
- 流程复用 rules_ai 骨架：画像 + 样例整体脱敏（38 章）→ llm_cache → 审计（task_type=repair_candidate）→ JSON 严格校验 → 候选落库（status=proposed）
- `repair propose --ai` 未配置 LLM 时抛 LLMNotConfiguredError，CLI 清晰提示不崩溃

## 模型异常检测（Step 42，IF/LOF，distribution_stability 维度首个检测器）

- 检测器 `model_outlier`：单列 Isolation Forest（默认）/ LocalOutlierFactor，与统计法（IQR/z-score）互补，捕获任意形状分布的偏离点
- 默认 contamination 2%（显式，auto 对单变量数据过度标记）、min_anomalies 3、异常比例上限 5%、>20k 行采样；配置走 `ScanConfig.detector_params`
- 每列一条 issue：affected_count=异常行数，evidence=STATISTICAL_MEASURE（模型/异常数/比例/样例值），severity LOW（模型提示性信号，供人工确认）
- 融合新家族 `distribution_anomaly` → DISTRIBUTION_STABILITY（此前无检测器的维度）
- 新依赖 scikit-learn≥1.5（core 包）；测试 `tests/test_anomaly_ml.py` 7 例（IF/LOF/采样/端到端）

## 模糊重复检测（Step 41，uniqueness Level 3）

- 检测器 `fuzzy_duplicate`：SQL 下推归一化分组（lower + 去空白/标点，保留字母数字与 CJK，'g' flag 全替换），组大小 ≥ 2 且组内原始值 ≥ 2 种，归一化键 ≥ 2 字符
- 每列一条 issue：affected_count = 组内行数 − 组数（可去重行数），evidence=DUPLICATE_MATCH（归一化键 + 原始样例）；融合归入 uniqueness 维度家族
- 覆盖：大小写变体（Alice/alice）、空白变体（"张三"/"张三 "）、标点变体（李四/李四!）；数值列与短键自动跳过
- 测试 `tests/test_fuzzy_duplicate.py` 6 例（含中文、端到端）

## 跨表外键完整性（Step 40，integrity 维度首个真实检测器）

- 契约 `references` 声明跨表外键：`name/path/table/schema/columns`（主表列→引用表列），DuckDB 引用文件需 `table`；`schema` 键兼容
- 检测器 `foreign_key_violation`（INTEGRITY 维度）：主表非 NULL 但引用表无匹配 → 孤儿行，列级 issue + `constraint_violation` 证据（孤儿数/比例/引用名），无 references 时自动跳过
- 主表/引用支持 CSV/Parquet/JSONL/DuckDB（XLSX 引用文件 MVP 不支持）；自建只读 executor，路径/标识符全部转义
- 接入：`client.scan_file(..., references=[...])` 或 `scan --contract`（契约 references 自动透传）
- 测试 `tests/test_cross_table.py` 9 例：孤儿/全匹配/未知列/多引用/duckdb 引用/SDK+CLI 端到端

## 漂移引擎（Step 39，18.2 历史版本比较，V1）

- `drift/engine.py` `compare_scans`：纯函数式比较两个历史扫描 → `DriftReport`——schema 变更（added/removed/dtype_changed/order_changed，renamed 需启发式不伪造）、行数变化率（默认 20% 阈值）、质量分变化（默认 5 分阈值）、issue_type 分布增减（新问题=HIGH / 已解决）；阈值可传参
- SDK：`client.drift_compare(run_a, run_b)` / `drift_latest(dataset_id)`（最近两次，不足两次 ValueError）；CLI：`drift compare` / `drift latest`（不足两次 → 退出码 2）
- 不落库、零耦合：比较只读 scan 历史与 issues（`tests/test_drift_engine.py` 10 例 + 集成 6 例）

## 电商订单域场景示例（Step 34，34 章场景 B/C 真实化）

- **契约即门禁（Step 35）**：`Contract` 可内嵌 `gate` 段（`fail_on` / `maximum_failed_rows_ratio` / `maximum_issues`），`scan --contract` 自动以契约门禁求值，`--gate` 可覆盖；契约缺失/无效 → 退出码 2；`require_repair_validation` 落地——工作区存在已应用修复即豁免放行（`tests/test_gate.py` 6 例闭环验证）

- **多文件场景**：`examples/ecommerce/run_showcase.py` 生成 orders.csv（负价/非法日期/状态变体/重复客户）+ customers.csv（坏邮箱/坏手机号）双脏数据文件，分别扫描落库——orders 12 issues / 96.7，customers 8 issues / 97.0（固定种子可复现，同 seed 输出逐行一致）
- **质量门禁真实闭环**：进程内 `QualityGateEvaluator`（22 章场景 C）`fail_on=high` 求值——脏数据被拦截（passed=False，high 影响 4.15% 行 > 0.01 阈值）；扫描 → 修复（副本叠加）→ 复扫 → 再修，直至无可修复（上限 3 轮），修复 3 个 issue 后门禁放行（passed=True）
- **修复权衡显式化**：`set_null` 把非法日期转 NULL——消除错误但引入缺失，分数 96.7 → 96.0 不升反降；脚本明示「门禁才是最终裁决」，不强凑分数上涨
- **修复副本链式推进**：修复引擎写副本不原地改文件（15 章产物），复扫对象 = 上一轮修复副本（`.datasentry/repairs/<run_id>.csv`）；同轮修复互不叠加，轮末最后一个副本作为下一轮输入
- **三份 HTML 报告**：orders / customers / orders-final 各导出规范报告；预算 180s 实测 ~10s
- **核心包 bug 修复**：DuckDB 将全合法日期列推断为 DATE 类型后，`uniqueness_violation` / `rare_category` 把原始 `date` 对象塞进 evidence.data → `save_scan` JSON 序列化崩溃；两处改为 `_json_safe`（date/datetime → ISO 字符串）；新增 tests/test_evidence_json.py 回归守卫（JSON 往返 + 无 date 泄漏断言）
- 测试 408 → 413：tests/test_ecommerce.py（预算硬断言 + 门禁拦截/放行契约 + 产物存在性 + seed 可复现 ×2）+ tests/test_evidence_json.py（2 例）；ADR 待定表保持清零

## PII 加密还原（Step 48，V2-A）

- **vault 架构**：`src/datasentry/pii_vault.py` `PIIVault` —— AES-256-GCM
  加密脱敏映射（redactor 的 mapping 从「进程内不落盘」升级为「加密落库」）；
  密钥来源优先级 env `DATASENTRY_ENCRYPTION_KEY` > `~/.config/datasentry/vault.key`
  （0600）> 内置 dev 密钥（CLI 告警）；密文 = base64(nonce 12B || ct)，密钥
  sha256 派生 32 字节
- **schema v3**：新表 `pii_mappings`（session_id 主键 / ciphertext /
  key_version / created_at）+ `llm_invocations.pii_session_id` 审计列；
  迁移幂等（`_ensure_column` + `CREATE TABLE IF NOT EXISTS`）
- **确定性 session_id**：`pii_` + sha256(json(mapping, sort_keys))[:16]——
  同一映射复用同一加密会话，与 llm_cache 语义对齐（cache 命中时 mapping 一致）
- **还原闭环**：`repair_ai` / `rules_ai` propose 时 `vault.save_mapping(mapping)`；
  LLM 输出经 `restore_text`（str）/ `restore_value`（递归 dict/list，规则
  when 值）还原后落库；每次还原写审计（task_type=pii_restore，
  prompt_hash=session_id）；轮换审计 pii_key_rotate
- **密钥轮换**：`llm rotate-key` 用新密钥解密→重加密全部映射 + 写 key 文件；
  旧密钥丢失/不匹配 → `VaultKeyMissingError`（拒绝还原并提示，不静默降级）
- **报告打码**：`reporting.mask_text_pii`（redact 后占位符 → `[REDACTED]`）
  应用于 HTML/Markdown 人类可读输出与 UI 标题；JSON 机器契约保留原文证据链
- CLI：`llm restore`（无参=列表 / 会话摘要 / `--text` 还原 / `--delete`）、
  `llm rotate-key [--new-key]`、`llm status` 增 `pii_vault` 段
- 依赖：cryptography≥42（仅主包；core 零依赖）；测试 522 → 553（+31）

## HTML 报告交互（Step 49，V2-B）

- **架构**：`reporting/interactive.py` = 可测纯函数层（`issue_rows` /
  `filter_issues` / `sort_issues` / `paginate` / `json_script` /
  `render_trend_svg` / `render_interactive_issue_table`）+ 内嵌原生 JS
  常量 `_INTERACTIVE_JS`；`render_html` 新增可选参数 `trends` /
  `page_size` / `server_base_url`（全可选，向后兼容）
- **Step 61（ADR-061）Column Profiles**：`reporting/column_profiles.py`
  = 纯函数层（`profile_rows` / `sort_profiles` / `render_column_profiles`）
  + 内嵌 `_PROFILES_JS`；`render_html` 再增可选参数 `profiles`
  （`DatasetProfile.model_dump(mode="json")`，sidecar 由
  `client.load_profile` 读取）；画像数据流：`scan_file` → `Profiler`
  → `.datasentry/profiles/<run_id>.json`（app 私有，不进 JSON 契约）
  → HTML 节（显示层 `mask_text_pii` 掩码 top 类别）
- **Step 62（ADR-062）修复建议预览**：`reporting/suggestions.py` 纯函数
  `suggest_repairs`（按 detector_ids 反查显示侧映射，镜像 repair/engine
  知识，≤3 条去重，label/rationale 掩码）；`issue_rows` 行内挂
  `suggestions` 键，JS 详情行内联渲染；零存储零依赖，测试参数化防引擎
  映射漂移
- **注入防护**：数据经 `json_script` 内嵌（dumps 后 replace `<`/`>`/`&`
  为 `\uXXXX`，`</script>` 无法提前闭合）；JS 全部用 `textContent` 建
  单元格、severity 样式类走白名单映射；服务端数据已过 `mask_text_pii`
- **测试策略**：Python 纯函数与 JS 行为一一对应（同语义参照），单测 +
  报告快照断言（容器存在 / 数据 JSON 正确 / 无 `<script src` / 注入样例
  被转义）；无浏览器测试依赖
- **趋势注入**：core 不导入应用层——CLI/API 调 `build_trends` 后经
  `DatasetTrend.to_report_dict()` 序列化传入；不足两点不渲染 SVG
- **server 联动**：`GET /scans/{run_id}/report.html`（api.py）以
  request.base_url 注入 `serverBaseUrl`，JS 为每行生成 workbench 链接；
  离线 `report export --as html` 默认 None 无链接

## 插件 entry point 发现（Step 50，V2-C，ADR-050）

- **发现机制**：`importlib.metadata.entry_points` 扫描 `datasentry.detectors`
  组（`datasentry_core.plugins.discover_entrypoint_detectors`），返回
  `PluginDiscoveryReport`（loaded / failed / errors 三清单）；
  与目录插件（Step 31，`plugins/` 目录扫描）和内置检测器并存，
  互不覆盖——同一 detector_id 冲突记入 errors 而非中断
- **entry 值三形态**：实例 / 无参类 / 无参工厂（lambda 或函数）；
  收敛在 `_coerce_entry_value`。**坑**：runtime-checkable Protocol 的
  `isinstance(cls, Detector)` 对「类对象」会因方法签名匹配而误判为
  True（返回类而非实例），必须先判 `isinstance(value, type)` 再实例化
- **来源标记**：注册表快照统一带 `source`（builtin / dir / entrypoint）；
  CLI `plugin list --format json` 输出 plugins + errors 明细，
  便于审计"哪些插件在跑、谁失败了、为什么"
- **示例插件包**：`examples/plugins/datasentry-sample-detector`
  （pyproject 声明 `[project.entry-points."datasentry.detectors"]`，
  依赖 datasentry-core，不进工作区包）；验证路径：
  `uv pip install -e examples/plugins/datasentry-sample-detector` →
  `plugin list` 显示 source=entrypoint → scan 自动启用
  （detector runs = 39 内置 + 1 插件）；隔离验证用 `mktemp -d` + `uv venv`，
  勿装进项目 venv（会改变现有测试对 run 数的断言）

## 本地调度器（Step 51，V2-D，ADR-051）

- **架构**：`scheduler/models.py`（JobCommand/ScheduledJob/JobRun/状态枚举，
  naive UTC 与 core 存储 `_iso` 惯例一致）→ `scheduler/store.py`
  （SQLite 持久化 + 原子抢占）→ `scheduler/core.py`（cron 校验/
  next_run、Scheduler.tick 状态机、LocalScanExecutor、WebhookNotifier、
  SchedulerWorker 线程）→ api.py `/jobs` 端点族 + lifespan worker
- **并发与持久化**：所有写操作 `BEGIN IMMEDIATE` 事务；`claim_due_jobs`/
  `claim_job` 条件更新抢占（同一任务同一时刻一个执行者），
  SQLite 单写者锁天然跨进程互斥；任务随 metadata.db 持久化，
  startup 时 `recover_interrupted`（running → idle + interrupted）
- **状态机**：idle → running → idle（成功，next=cron 下一次）/
  failed+idle（attempt ≤ retry_attempts，60s 后重试）/ dead（耗尽）
- **坑位备忘**：
  - `scan_run.quality_score` 是 QualityScore 对象，取 `.overall` 才是 float
  - run_id 生成必须带随机后缀（同秒内多次触发会撞 UNIQUE 约束）
  - `sqlite3.connect` 记得 `row_factory = sqlite3.Row`（字符串索引）
  - SchedulerStore 独立 db 需调 `core_schema.migrate(conn)` 建表（DDL 同源）
  - 测试用可推进假时钟 `_Clock`，不要依赖真实时间；互斥窗口用
    `_BlockingExecutor`（Event 阻塞）模拟执行中
  - croniter 无类型标注：import 加 `# type: ignore[import-untyped]`，
    `get_next` 返回 Any 需 `cast(datetime, ...)`

## 调度质量门禁 + MCP 调度工具（Step 52，ADR-052）

- **门禁即判定**：`scheduled_jobs.gate_quality_min`（0-100，NULL=关，
  schema v5）；run 完成后 `core.evaluate_gate(job, run)` →
  `` `gate: {passed, min, score}` `` 进 summary 与 webhook 载荷；
  判定失败≠执行失败（run 仍 completed，不重试不进 dead），
  `passed=false` 由下游处置
- **schema 迁移套路**：core schema DDL 里直接建新列 +
  `migrate()` 里 `version == 4` 执行 `_ensure_column(conn, "scheduled_jobs",
  "gate_quality_min", "REAL")` 再置 version=5；老库软升级不丢数据

## MCP 调度工具（Step 52，ADR-052）

- **MCP 新工具走 `@self._tool` 装饰器**（name/description/properties/
  required 四参），在 `_register_tools()` 内定义；工具内 import 在函数
  体内（避免循环依赖并保持头部 import 精简）
- **调度工具与 REST 同源**：`SchedulerStore(project_db_path(client.workspace))`
  复用同一 metadata.db；`job_create` 非法 cron 返回
  `{"ok": False, "error": ...}`（工具面不抛异常）；返回值统一 `_json_safe`
- **MCP 工具测试模式**：`tools/call` 结果在
  `result.content[0].text`（JSON 字符串），需 `json.loads` 解析；
  `tools/list` 断言工具名集合（新增工具要同步改
  `test_tools_list_shape` 的期望集合）

## 变更感知增量调度（Step 53，ADR-053）

- **跳过判定位置**：`Scheduler._run_job`（execute 之前）——执行器
  （ScanExecutor）不感知 skipped 语义，保持抽象纯净；手动 trigger
  与 tick 共用同一判定
- **基准语义**：`store.last_successful_hash` 只取「status=completed
  AND skipped=0 AND file_hash IS NOT NULL」的最新一条——跳过自身
  不作基准（防永久跳过）；failed 也不作基准
- **坑位备忘**：
  - JobResult.scan_run_id 已放宽为 `str | None`（skipped 时为 None），
    正常路径无影响；webhook 载荷字段随之可空
  - `_finish_success` 必须显式把 `result.file_hash` 传给
    `finish_run(file_hash=...)`，否则 last_successful_hash 永远 None、
    跳过永不触发
  - 测试假时钟与 cron 匹配：默认 cron 是 `*/5 * * * *`，测试用
    advance(minutes=1) 会不到期——变更感知测试统一用 `* * * * *`
  - 文件哈希用流式 1MiB 块（`file_sha256`），大文件不整读入内存

## SQLite 数据源连接器（Step 54，ADR-054）

- **架构**：connectors/sqlite.py 的 `SQLiteDataHandle(FileDataHandle)`，
  `_ensure_view` 里 `LOAD sqlite` + `sqlite_scan('path', 'table')` 注册
  只读 data 视图——共享实现全复用；`SqliteConnector` 注册进
  default_registry；client 扩展名映射 `.db/.sqlite/.sqlite3` →
  DataSourceType.SQLITE
- **坑位备忘**：
  - 表名必填校验在 `open()`（supports 只看 type）——REST 缺 table_name
    走 404 而非 400（DataSourceNotFoundError 映射）
  - 注册表断言会变：tests/test_detector_registry.py 与
    test_connectors.py 的默认注册表顺序/不支持类型断言需同步更新
    （unsupported 用例改用 POSTGRESQL）
  - 调度联动测试同样有同秒排序竞态：断言用 trigger 返回的 run_id
    查 store，不要依赖 list_runs 的 DESC 顺序
  - `LOAD sqlite` 幂等（DuckDB 扩展自动加载，CI ubuntu 同样可用）

## PostgreSQL 数据源连接器（Step 55，ADR-055，V4）

- **架构**：connectors/postgres.py 的 `PostgresDataHandle(FileDataHandle)`，
  `_ensure_view` 里 `LOAD postgres` + `ATTACH dsn AS pg (TYPE postgres,
  READ_ONLY)` 注册只读 libpq 视图——共享实现全复用；`requires_path=False`
  （无文件字节）；表名必填（缺表名 DataSourceNotFoundError→404/CLI 2），
  schema 名经 `options["schema"]`；`PostgresConnector` 注册进
  default_registry；client 对 `postgresql://`/`postgres://` 前缀识别为
  POSTGRESQL（DSN 进 spec.options，不落库）
- **凭据红线（硬性）**：DSN 只走内存态或 connection_ref 环境变量引用
  （如 `DATASENTRY_PG_DSN`）；`_RedactingExecutor` 包装 DuckDB 执行器，
  异常净化（DSN→`postgresql://***`、密码→`***`）后转 ConnectorError；
  CLI 连接失败退出码 4、错误面无凭据，evidence/报告零泄漏
  （test_postgres_integration.py 有泄漏断言）
- **变更感知（关键语义）**：PG 无文件，调度器 `_source_fingerprint`
  源感知——文件源走文件 SHA-256（Step 53 原语义），PG 源走
  `handle.content_fingerprint()`（单查询全表哈希：每行
  `md5(concat_ws(chr(31), coalesce(col::VARCHAR, chr(0))...))` +
  `string_agg(..., ORDER BY rh)` 行序无关；并入 schema_hash 与行数）；
  同内容跳过、变更重扫、源不可达不误跳过；`last_successful_hash` 是
  TEXT，两侧指纹同字段，零 schema 变更
- **类型归一化**：DuckDB postgres 扩展返回的物理类型不命中检测器
  精确集合——`FileDataHandle.schema()` 统一 `_normalize_physical_type`
  （DECIMAL(n,p)→DECIMAL、TIMESTAMP WITH TIME ZONE→TIMESTAMPTZ）；
  回归点：曾有检测器把 PG 的 DECIMAL 聚合写进 evidence 导致
  `json.dumps` 崩（`Object of type Decimal is not JSON serializable`）
  ——store.save_scan 已加 `default=_json_default`（Decimal→float）兜底
- **集成测试**：tests/test_postgres_integration.py 全部 integration
  marker，fixture 先 `_pg_available` 探测（LOAD postgres + 只读 ATTACH），
  失败即 skip（本地无 PG/离线保持全绿）；**写路径实测可用**——
  DuckDB postgres 扩展支持无 READ_ONLY 的 ATTACH 建表/DML，fixture 就
  用它在测试内灌数据（不引入 psycopg，CI 同路径）；CI test job 有
  postgres:16-alpine service + `TEST_POSTGRES_DSN` env
- **坑位备忘**：
  - 单元测试用 FakeExecutor 惰性替换 `handle._executor`（构造不连库，
    连库只发生在 `_ensure_view`）；`_RedactingExecutor` 只捕
    `duckdb.Error`，UnsafeSqlError 等连接器族异常原样透传（404/400 语义不变）
  - `DATASENTRY_PG_DSN` 等 connection_ref 环境变量缺失要报
    DataSourceNotFoundError（可操作提示），不要裸 KeyError
  - 指纹查询的 `chr(31)`（US 分隔符）防 md5 拼接歧义；`chr(0)` 标记
    NULL；行序无关靠 `string_agg(rh, '' ORDER BY rh)`
  - `_ensure_view` 里区分三类失败提示：扩展加载失败/连接失败/表不存在
    （前两者 ConnectorError 带 hint，后者 DataSourceNotFoundError）
  - `LOAD postgres` 首次需联网装扩展；`requires_path` 是 ClassVar，
    PG 子类置 False，配置文件源子类默认 True

## MySQL 数据源连接器（Step 56，ADR-056，V5）

- **架构**：connectors/mysql.py 的 `MySQLDataHandle(FileDataHandle)`，
  `_ensure_view` 里 `SET mysql_aggregate_pushdown_enabled = false` +
  `LOAD mysql` + `ATTACH dsn AS my (TYPE mysql, READ_ONLY)` 注册只读
  表，再建 `data` 视图——共享实现全复用；`requires_path=False`；表名
  必填（缺表名 DataSourceNotFoundError→404/CLI 2）；MySQL 无独立
  schema 层（database 在 DSN 内）；`MySQLConnector` 注册进
  default_registry；client 对 `mysql://` 前缀识别为 MYSQL（DSN 进
  spec.options，不落库）
- **凭据红线（硬性）**：与 PG 同套——DSN 只走内存态或 connection_ref
  环境变量（如 `DATASENTRY_MYSQL_DSN`）；`_RedactingExecutor` 异常
  净化（DSN→`mysql://***`、密码→`***`）后转 ConnectorError；CLI 连接
  失败退出码 4、错误面无凭据；**mysql 扩展回显两种形态都要打码**：
  URL 形式（`mysql://user:pass@host/db` 整体替换）与 KV 形式
  （`host=... passwd=***`，`_KV_PASSWORD_PATTERN` 正则）——单测覆盖
- **已知 DuckDB bug 绕行（关键）**：1.5.x mysql 扩展「attach 之上的
  视图 + 聚合」触发内部绑定错误（`Failed to bind column reference`；
  直连 attach 正常、PG 扩展无此问题、全关优化器可过）。`_ensure_view`
  先 `SET mysql_aggregate_pushdown_enabled = false`：聚合在 DuckDB
  本地执行，语义不变；视图 + count/groupby/指纹全部正常。回归点：
  删掉该 SET 会立刻复现（test_mysql_integration.py 全红）
- **变更感知（继承 PG 语义）**：调度器 `_source_fingerprint` 已支持
  `mysql://`（`_is_remote_db_path` + 源类型推导）；`content_fingerprint()`
  与 PG 同款（行序无关、NULL 不折叠、并入 schema_hash 与行数）；
  同内容跳过、变更重扫、源不可达不误跳过
- **类型归一化**：复用 `FileDataHandle.schema()` 的
  `_normalize_physical_type`；MySQL INT→INTEGER、DECIMAL(10,2)→DECIMAL、
  TIMESTAMP→TIMESTAMP 已由集成测试锁定
- **集成测试**：tests/test_mysql_integration.py 全部 integration marker，
  fixture 先 `_mysql_available` 探测（LOAD mysql + 只读 ATTACH），失败
  即 skip；写路径实测可用——DuckDB mysql 扩展支持无 READ_ONLY 的
  ATTACH 建表/DML，fixture 用它灌数（不引入 pymysql）；本地默认连
  docker 映射 3307 端口（datasentry-mysql-test，root/testpass），CI
  test job 有 mysql:8 service + `TEST_MYSQL_DSN` env
- **坑位备忘**：
  - 与 PG 同：FakeExecutor 惰性替换 `handle._executor`；`_RedactingExecutor`
    只捕 `duckdb.Error`，连接器族异常原样透传
  - `_ensure_view` 三类失败提示区分同 PG（扩展加载/连接失败/表不存在，
    注意 MySQL 版表不存在文案是 `mysql table not found: <table>`）
  - mysql 扩展加载后需联网（首次）；MySQL 8.4 caching_sha2_password
    1.5.5 实测可连（无需老式 native password 兼容配置）
  - 灌数用的 `_seed` 要先 `DROP TABLE IF EXISTS` 再建表（脏表残留会
    让 CREATE IF NOT EXISTS 静默沿用旧列结构）

## 云存储文件源连接器（Step 57，ADR-057，V5）

- **架构**：connectors/remote_file.py 的 `RemoteFileDataHandle(FileDataHandle)`，
  `_ensure_view` 里 `LOAD httpfs` +（有 endpoint 时）`SET s3_endpoint/
  s3_region/s3_url_style='path'/s3_use_ssl=false` + 探测
  `SELECT 1 FROM read_*(uri) LIMIT 0` + 建 `data` 视图（按格式分派
  read_csv_auto/read_parquet/read_json_auto）——schema/抽样/聚合/
  count/警告扫描全复用；`read_batches` 走 execute_stream（无本地
  路径，不能用 pq.ParquetFile）；`RemoteFileConnector` 按 URI 前缀
  分派 CSV/PARQUET/JSONL 并注册进 default_registry
- **URI 与凭据**：`spec.path` 放宽为 `Path | str | None`——s3:// gs://
  az:// 走字符串；本地连接器（csv/parquet/jsonl/duckdb/sqlite/xlsx）
  supports 与构造均已收窄排除字符串 path（注册序不抢占）。
  **凭据只走进程环境变量**（AWS_ACCESS_KEY_ID/SECRET_ACCESS_KEY 等
  httpfs 原生读取）；非机密会话配置经 `options["s3_endpoint"]`（或
  env `AWS_ENDPOINT_URL_S3`）传入，有自定义 endpoint 自动
  path-style + 非 SSL（MinIO 必需，vhost 404）
- **净化**：az:// URI 可含 SAS token——`_RemoteRedactingExecutor` +
  `redact_uri` 把错误文本中的完整 URI 替换为 `<remote-uri>` 再转
  ConnectorError；DataSourceNotFoundError 文本同样不含 URI
- **变更感知（快速失效层）**：httpfs 不暴露 ETag → `content_fingerprint()`
  = `sha256(uri|size|last_modified)` 经 read_blob 元数据（HEAD 级
  开销，免下载）；覆盖写必变、同秒同 size 覆盖是已知局限（ADR-057）；
  调度器 `_source_fingerprint` 已支持 s3:// gs:// az:// 前缀，源
  不可达返回 None 不误跳过；`fingerprint('full')` 的 file_sha256=None
- **集成测试**：tests/test_remote_file_integration.py 全部 integration
  marker，fixture 先 `_minio_available` 探测（glob 桶内对象），失败
  即 skip；fixture 数据经 httpfs COPY TO 灌入（同路径，无 boto3）；
  本地默认连 docker 9000 端口（datasentry-minio-test，
  minioadmin/minioadmin，桶 test-bucket 需 mc mb 预建）；CI test job
  有 minio service + mc 建桶步骤 + TEST_MINIO_ENDPOINT/凭据 env
- **坑位备忘**：
  - endpoint env 探测：httpfs 不认 `AWS_ENDPOINT_URL_S3`，必须经
    连接器 `SET s3_endpoint`（连接器会自己读 env 并下发 SET）
  - read_blob 的 last_modified 裸取报 pytz 缺失 → 连接器 str() 包裹
  - 桶必须预建（缺桶 PUT 400）；gzip 压缩对象不在本步范围（后缀
    推断失败抛可操作错误）

## 分层增量指纹（Step 58，ADR-058，V5）

- **问题**：Step 55 内容指纹（PG/MySQL 全表哈希、云文件 size+last_modified）
  每次 tick 都读全表/HEAD 元数据——百万行级表 tick 开销可观
- **设计**：远程源两层复合指纹，落库 `last_successful_hash` 为定序 JSON
  `{"stats": ..., "content": ...}`：
  - 统计层 `stats_fingerprint()`（协议成员，`FileDataHandle` 默认实现）：
    `sha256(schema_hash|row_count)`——DESCRIBE（目录查询）+ count，
    零内容读取
  - 调度器 `_source_fingerprint(command, previous)`：统计层与上次复合
    指纹不一致 → 立即判定变更（content=None，**零内容读取**）；一致
    才算内容层指纹（PG/MySQL 全表哈希 / 云文件元数据）判跳过
  - 扫描成功后 `LocalScanExecutor` 用扫描指纹（schema+row_count+
    content）落库完整复合——避免「统计层变更→重扫→落库缺内容→下轮
    再扫一次」的双重扫描
- **迁移**：Step 55/56/57 时代落库的单段 hash 视为「统计层未知」→
  保守走内容层比对（等价旧语义），成功后自动迁移为复合
- **不实现采样层**（计划选项 3）：内容哈希抽样有误跳过风险，违反
  「内容变必重扫」硬不变量（ADR-058 记录理由）
- **基准**（benchmarks/bench_layered_fingerprint.py，PG 百万行）：
  统计层 0.20s vs 内容层 0.86s（4.2×）；判据 ≥3× PASS——同内容
  tick 开销从全表哈希降到目录查询 + count
- **测试**：scheduler 单测（统计层变更零内容调用、统计同+内容同跳过、
  统计同+内容异重扫、遗留迁移、复合落库）；PG/MySQL 集成（原地
  UPDATE 统计层不敏感+内容层兜底、INSERT 统计层移动、真实调度
  四阶段 skip/UPDATE 重扫/INSERT 重扫）

## 凭据管理（Step 59，ADR-059，V5）

- **凭据文件**：`~/.config/datasentry/secrets.env`（
  `DATASENTRY_CONFIG_HOME` > `XDG_CONFIG_HOME` > `~/.config` 取值，
  测试经 DATASENTRY_CONFIG_HOME 隔离）；行格式 `KEY=VALUE`，键名
  必须匹配 `[A-Z][A-Z0-9_]*`（环境变量命名形态，可直接 source）；
  父目录 700/文件 600 强制——读取权限过松拒绝（给 chmod 提示），
  set/rm 整体重写自动修正
- **统一解析链**：CLI 参数（scan path DSN）> connection_ref：
  进程环境变量 > secrets.env（`lookup_secret`），均无 →
  DataSourceNotFoundError；postgres/mysql 的 `_resolve_dsn` 已收敛，
  新增数据源同样走 lookup_secret 即可获得链路
- **CLI**：`datasentry secrets set|get|list|rm`——set 用 getpass
  无回显 + 二次确认（不进 shell history），list 仅显示键名与路径
  （审计语义，值永不出现在输出）
- **坑位备忘**：
  - `secrets set` 读取侧绕过权限检查（本操作随即重写 600），否则
    过松权限文件永远无法自愈
  - 凭据 grep 审计命令（发布前跑）：
    `grep -rInE --exclude-dir={.git,__pycache__,.venv} "postgresql://[^:]+:[^@/ ]+@|mysql://[^:]+:[^@/ ]+@|passwd=[^ ]+" src packages tests examples docs`
    预期仅剩净化正则与占位符（user:pass / testpass / minioadmin）


## V35：Web 修复闭环与文档（v0.40）

- v0.31–v0.40 批量修复工作流：提案零写 → 应用写副本（源文件永不覆盖）
  → 回滚单条/批量；source_path 持久化（schema v9，scan_runs.source_path）。
- 对比页 NEW 组一键提案：hidden issue ids + 预填源路径。
- README 新增「Web repair workflow」小节；blog-3（en/zh）总结闭环。
