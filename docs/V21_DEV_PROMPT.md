# V21（v0.23.0）开发任务书：worker 远程执行器 CLI 配置化

> 基准：v0.22.0 已发布（V20 完成，CI/PyPI/Pages/release 全绿，工作区干净）
> ADR 止于 ADR-110（105 条记录）；MCP tools 20；测试 1204 passed；
> 覆盖率 94.94%；REST _ENDPOINTS 28

## 目标

V20 把 `RemoteScanExecutor` 能力做全（超时/重试/探测/报告回传），但
CLI 无配置入口——`job trigger` 硬编码 `LocalScanExecutor`，库级远程
能力无法实际使用。V21 补 CLI 闭环：远程触发选项、远端健康可见性、
跨 workspace 端到端证明。不扩 schema、不加协议、不改 Scheduler 语义。

## 现状事实（探查结论）

1. CLI 顶层命令：scan / repair / job / worker / llm 等；无 schedule
   命令——执行路径只有 `job trigger`（cli.py:740，同步触发）
2. `_cmd_job_trigger` 硬编码 `Scheduler(store=store,
   executor=LocalScanExecutor()).trigger(job_id)`——远程执行器无入口
3. `worker` 是叶子命令（--host/--port/--token），不能加子命令（会
   破坏兼容）——健康探测需独立顶层命令
4. `tests/test_remote_e2e.py`（Step 92）已有真 uvicorn worker +
   Scheduler + RemoteScanExecutor 端到端基建（_serve helper）
5. CLI `main()` 顶层捕获一切异常 → EXIT_ERROR(3)（cli.py:997+）；
   配置缺失返回 EXIT_CONFIG(2)
6. 报告落点 `<workspace>/.datasentry/reports`（project_reports_dir）

## 三个 Step（每步：实现 → 测试 → 四连门禁 → 文档 → 单独 commit → CI 全绿）

### Step 111（ADR-111）：`job trigger` 远程执行器选项 + 报告回传落点 —— ✅ 完成

- `job trigger job_id [--remote-url URL] [--remote-token TOKEN]
  [--remote-retries N] [--remote-preflight]`：
  - 无 `--remote-url` → 现状（LocalScanExecutor，行为零变化红线）
  - 有 `--remote-url` 无 `--remote-token` → EXIT_CONFIG 明确报错
    （worker 数据面必须 token，防误用）
  - 远程时 executor = `RemoteScanExecutor(url, token,
    retries=--remote-retries, report_dir=project_reports_dir(project),
    preflight=--remote-preflight)`——报告自动回传本工作区
    `.datasentry/reports`（与本地导出落点统一）
  - `RemoteScanExecutor` 增构造参数 `preflight: bool = False`：
    execute 内部 `preflight` 参数为 None 时用构造默认（向后兼容，
    现有 `execute(command, preflight=True)` 显式调用不变）
- 测试 ~7（新文件 `tests/test_cli_remote.py`，复制 _serve helper）：
  本地无 --remote-url 行为不变 / 远程缺 token EXIT_CONFIG /
  真 worker 远程触发成功 completed / 错 token EXIT_ERROR /
  worker 未启用 token 时 preflight 通过但 execute 503 快速失败 /
  job create --export-report 远程触发后报告回传落点文件存在 /
  --format json 信封结构 {ok,command,data:{job_id,run_id}}

### Step 112（ADR-112）：`ping` 顶层命令（远端 worker 健康可见性） —— ✅ 完成

- 新增顶层命令 `ping URL [--token TOKEN] [--timeout S]`：
  `RemoteScanExecutor(url, token or "").health()`——health 公开
  无需 token；输出 {ok, service, version, worker, url}；失败 →
  error 信封 + EXIT_ERROR
- 测试 ~4：真 worker ping ok（version 非空）/ 未启用 token 的
  worker ping ok 且 worker:false / 不可达 URL → EXIT_ERROR +
  error 字段 / --format json 结构

### Step 113（ADR-113）：跨 workspace 端到端证明 + 收尾 —— 代码 ✅（收尾文档/发布见下）

- 端到端测试（Step 92 模式扩展）：worker 与调度端**不同** workspace
  ——远端 worker 执行扫描（scan 落 worker 库）+ 调度端本地 store
  run completed + 报告回传调度端 `.datasentry/reports`；证明
  「调度端-远端 worker 物理隔离」成立
- 收尾：ADR-111/112/113 落档；CHANGELOG [0.23.0] 三小节；
  DEVELOPMENT.md V21 段；计划书 ✅ + 坑位记录；index.html →
  0.23.0 / 新测试数 / 108 ADR / 20 tools
- bump 0.22.0→0.23.0（3 文件 + uv.lock）单独 commit；tag v0.23.0；
  PyPI/Pages/CI 全绿；`gh release create v0.23.0 --title "v0.23.0 —
  worker 远程执行器 CLI 配置化" --notes ...`

## 门禁（每步全绿才 commit）

1. `uv run --offline ruff check .`
2. `uv run --offline ruff format --check .`
3. `uv run --offline mypy --strict src/datasentry packages/core/src/datasentry_core`
4. `uv run --offline pytest -q --cov=datasentry_core --cov-fail-under=85`

## 验收

- 新增测试 ≥ 16 例；1204 → ≥1220 passed；覆盖率 ≥85%
- `job trigger --remote-url/--remote-token/--remote-retries/
  --remote-preflight` 可用；缺 token EXIT_CONFIG；报告自动回传
- `ping URL` 输出远端健康信息；MCP tools 20 不变；_ENDPOINTS 不变
- 跨 workspace 端到端证明（隔离成立）；v0.23.0 完整发布
- Scheduler/存储 schema/既有 CLI 命令行为零变化

## 坑位（V21 预埋 + 历史复读）

1. `worker` 命令是叶子命令，绝不能加 subparsers 破坏 --host 兼容
2. preflight 默认 False 是向后兼容红线：execute 参数与构造默认
   None-or-False 合并（None → 构造默认）
3. CLI 测试走真 uvicorn（_serve 模式）：worker 未启用 token 时
   health 恒 200（信息面设计），测试用「health 通过 → execute 503」
   验证快速失败，避免 120s 超时等待
4. 远程触发测试的 job 必须用 `--export-report` 验证报告回传落点
5. 报告落点是 `.datasentry/reports`（project_reports_dir），不是
   `<ws>/reports`
6. CLI 信封 {ok, command, data/warnings/error}；`--format json` 全局
   参数在子命令前（`--project` 同理）
7. EXIT_OK=0 / EXIT_CONFIG=2 / EXIT_ERROR=3；顶层异常统一 3
8. tests/test_cli_remote.py 的 _serve 复制自 test_remote_e2e（测试
   文件间不 import）
9. 现有 test_cli.py job trigger 测试（本地）必须零改动通过
10. mypy 门禁带两个源目录；int**int → 2.0**（Any 推断坑）

## 最终报告格式（交付）

五段式中文汇报：完成概述 / 新增能力 / 测试与门禁数据 / 发布状态 /
遗留问题（V22 候选：取消协议、报告交互增强、webhook 事件去重等）。
