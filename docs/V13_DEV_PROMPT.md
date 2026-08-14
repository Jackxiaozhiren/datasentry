# V13 开发计划书（v0.15.0）——云侧调度与协作：命令面补齐

## 一、目标

V2-D（Step 51/52）已交付**本地调度器**（cron + SQLite 持久化 +
worker 线程 + webhook + 门禁），并明确"不做分布式/K8s/Celery，
留好执行器抽象"。但调度能力的管理面严重偏科：

- MCP 有 `jobs_list / job_create / job_trigger`，但 **CLI 无 job
  命令、HTTP API 无 /jobs 路由**（api.py 只有 scans/trends/issues/
  repairs/ui）——主入口 CLI 与集成面 FastAPI 都无法管理任务；
- 任务生命周期不完整：有 create/trigger 无 remove/update
  （store 的 delete_job/update_job 无命令面暴露）；
- 运行历史（runs 表 + list_runs）无命令面查看、无保留策略。

V13 = **调度管理面三面补齐（CLI/HTTP API/MCP 同源）+ 生命周期
完整 + 运行历史可查可管**，延续 V2-D 边界（不引分布式依赖）。
版本：v0.15.0（core 包 pyproject 恒 0.7.0）。

## 二、Step 分解

### Step 86（ADR-086）CLI `job` 子命令

- **现状**：CLI 无 job 面；SchedulerStore（create_job/get_job/
  list_jobs/update_job/delete_job/claim_*/finish_run/list_runs/
  get_run）与 models（ScheduledJob.view / JobRun）齐备。
- **方案**：`datasentry job` 子命令族：
  - `job list [--status]`：任务列表（复用 `view()` 字段，JSON
    友好）；`--format json` 与既有 CLI 面一致
  - `job create <name> <path> --cron <expr> [--project]
    [--dataset-id] [--table-name] [--retry-attempts N]
    [--webhook-url URL] [--export-report]`：校验 cron
    （validate_cron）+ 同名校验，写 SchedulerStore
  - `job trigger <id>`：立即触发（scheduler.trigger）
  - `job remove <id>`：删除（store.delete_job）
  - `job status <id>`：单任务视图 + 最近 runs（list_runs limit 5）
- **影响**：cli.py + tests/test_cli_job.py；MCP 不动。

### Step 87（ADR-087）HTTP API `/jobs` 路由族

- **现状**：api.py 无 jobs 路由；`_build_scheduler` 已可复用；
  JobCreate/JobCommand 模型已定义。
- **方案**（与 MCP 同源，复用 SchedulerStore）：
  - `POST /jobs`（JobCreate → create_job，cron 非法 422）
  - `GET /jobs`（list，`?status=` 过滤）
  - `GET /jobs/{job_id}`（view）
  - `POST /jobs/{job_id}/trigger`（立即触发）
  - `DELETE /jobs/{job_id}`（删除，404 若不存在）
  - `GET /jobs/{job_id}/runs?limit=20`（运行历史 JobRun）
  - `POST /jobs/{job_id}/test-webhook`（WebhookNotifier 发送
    样例负载，验证协作链路；返回 HTTP 状态/耗时）
- **影响**：api.py + tests/test_api_jobs.py；文档面 OpenAPI 自动。

### Step 88（ADR-088）MCP 补全 + 三面对齐 + 历史保留

- **现状**：MCP 只有 list/create/trigger；store 有 update_job/
  delete_job 但 MCP 无 job_update/job_remove。
- **方案**：
  - MCP 增 `job_update`（enabled/cron/retry_attempts/webhook_url
    部分更新，cron 变更重算 next_run_at）+ `job_remove`
  - 三面命令对齐表（CLI/API/MCP 同一能力同一语义，全部走
    SchedulerStore，无第三套逻辑）
  - runs 保留策略：store.list_runs 默认 limit=20 已有；补
    `prune_runs(max_per_job=100)`——finish_run 后裁剪最旧，
    防 SQLite 无限膨胀（可配置）
- **影响**：mcp_server.py + store.py + tests 三处。

### Step 89 收尾 v0.15.0

- CHANGELOG [0.15.0] 三节 + ADR-086~088 落档 + DEVELOPMENT 路线图
  V13 完成段 + 计划书落地状态；升版（根 pyproject + 双
  `__init__.__version__`，core pyproject 恒 0.7.0）；tag v0.15.0
  （触发 PyPI + Pages）+ GitHub release + CI 观察。

## 三、验收标准（V13）

1. CLI/API/MCP 三面均可完成任务全生命周期：create → list →
   trigger → runs 历史 → remove；同一 SchedulerStore，无分叉
2. `job create` 非法 cron 报错（复用 InvalidCronError）；同名
   job 拒绝；`job remove` 不存在 job 报错
3. `POST /jobs` 校验与 MCP job_create 语义一致；`test-webhook`
   能发出样例负载并返回远端 HTTP 状态
4. `prune_runs` 生效：超过保留上限的 job 运行历史被裁剪
5. 门禁全绿：ruff / mypy --strict / pytest 全量覆盖 ≥85（维持
   ~95）+ CLI/API 冒烟；既有测试零回归（插件面 V12 不受影响）

## 四、勘察备忘

- 调度核心：src/datasentry/scheduler/core.py（Scheduler 状态机 /
  LocalScanExecutor / WebhookNotifier / evaluate_gate /
  validate_cron / next_run / _open_remote_handle 云 uri 打开）
- 存储：src/datasentry/scheduler/store.py（jobs + runs 两表；
  claim_due_jobs / claim_job / finish_run / recover_interrupted /
  list_runs / get_run / save_webhook_at）
- 模型：models.py（ScheduledJob.view / JobCommand / JobResult /
  JobCreate / JobRun / JobStatus.IDLE.QUEUED.RUNNING.DEAD /
  RunStatus.RUNNING.COMPLETED.FAILED）
- 集成：api.py `_build_scheduler` + SchedulerWorker 线程（lifespan
  起停）；MCP mcp_server.py jobs_list/job_create/job_trigger
  （project_db_path → SchedulerStore）
- 既有裁决：V2-D（Step 51/52，本地调度器边界、执行器抽象）、
  Step 79（JobCommand.config 透传）、Step 80（增量画像复合哈希）
- 版本惯例：根 pyproject + 双 __init__ 升版；core pyproject 恒
  0.7.0；tag v0.14.0 先例（PyPI+Pages 自动发布）；CI 门禁链
  ruff → format → mypy --strict → pytest --cov 85

## 五、状态回填

### Step 86（ADR-086）CLI job 子命令 —— ✅ 完成

- 交付：`job list [--status] / create / trigger / status / remove`
  五子命令（create 支持 dataset-id/table-name/retry-attempts/
  webhook-url/gate-quality-min/export-report；cron 非法、job 不存在、
  正在运行 → EXIT_CONFIG=2）；tests/test_cli_job.py 8 例全绿；
  门禁全绿（ruff/mypy --strict/pytest 95.01%）；CLI 冒烟
  create→trigger（真实执行）→status（run completed）→remove
- 坑位：`_emit` text 格式只输出信封 data 层（测试断言勿加
  envelope 层）；`SchedulerStore` 类型注解需 TYPE_CHECKING 导入
  （函数内 import 会触发 ruff F821/UP037 冲突）

### Step 87（ADR-087）HTTP API /jobs 路由族 —— ⏳ 待开始
### Step 88（ADR-088）MCP 补全 + 三面对齐 + 历史保留 —— ⏳ 待开始
### Step 89 收尾 v0.15.0 —— ⏳ 待开始
