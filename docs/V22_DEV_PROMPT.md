# V22（v0.24.0）开发任务书：调度端 cancel 语义闭环 + 远程 cancel 协议

## 目标

补上「远程执行链路」的最后一个控制面缺口：**取消**。V21 报告遗留
首位即 cancel/异步协议——本版做**调度端 cancel 语义闭环 + 远程
cancel 协议（尽力而为）**，不引入完整异步协议（改 Scheduler 触发
语义是红线，留作 V23 候选）。

用户手动误触发长扫描后无法停止的问题被解决：cancel 后即使执行器
（本地线程/远端 worker）最终跑完，结果也被**丢弃**（run 保持
cancelled）。

## 方向选择（多候选，选 cancel）

- 候选 A：cancel/异步协议——V21 报告遗留首位；本版只做 cancel
  语义（异步化触发留 V23）
- 候选 B：报告交互增强——范围模糊，报告已可导出/回传，价值低
- 候选 C：webhook 事件去重——范围小、价值小，留作填充候选

## 现状与边界（红线）

- `job_runs.status` TEXT 无 CHECK 约束 → `RunStatus.CANCELLED` **无需
  schema 迁移**；`recover_interrupted` 不受影响
- `Scheduler.trigger` 语义**零变化**（互斥 claim、同步/线程池行为
  不变）；`job cancel` 只作用于 running 的 run
- `finish_run` 单事务（BEGIN IMMEDIATE）→ cancel 竞态靠「事务内读
  run 状态，已 cancelled 则跳过全部更新」解决，无锁设计
- 执行器无法强杀扫描线程（本地/远端都是同步阻塞）——**尽力而为
  cancel**：状态作废、结果丢弃、线程跑完；边界文档化
- `RemoteScanExecutor` 既有方法零变化（execute/health 契约不变）；
  新增 cancel 方法 + /rpc/cancel 端点
- MCP tools 20 不变；REST `_ENDPOINTS` 28 → 29（+POST /rpc/cancel）
- Scheduler/存储 schema/既有 CLI 命令行为零变化

## 实施步骤（每步全绿才 commit，ADR 编号接续 108）

### Step 114（ADR-114）：调度端 cancel 语义闭环（本地） —— ✅ 完成（含 schema v7→v8 重建，任务书「无 CHECK 约束」核实有误——坑位 1 修正为：job_runs.status 有 CHECK，需重建表迁移）

- `RunStatus.CANCELLED = "cancelled"`（scheduler/models.py）
- `SchedulerStore.cancel_run(job_id, *, error="cancelled by user")`：
  BEGIN IMMEDIATE 事务内查 running run → 更新 run 为 cancelled +
  finished_at + error → scheduled_jobs status=idle → COMMIT；返回
  run_id 或 None（未在运行）
- `Scheduler.cancel(job_id)`：代理 store.cancel_run
- `finish_run` 竞态防护：事务内 `SELECT status FROM job_runs WHERE
  run_id=?`——status 已是 cancelled → 返回（丢弃结果，job 状态也不
  动，未提交自动回滚）；running/其他 → 原逻辑
- CLI：`job cancel job_id [--remote-url URL] [--remote-token TOKEN]`；
  未在运行 → {job_id, error: "job is not running"} EXIT_CONFIG；
  成功 → {job_id, run_id, status: "cancelled"} EXIT_OK；--remote-url
  时顺带 RemoteScanExecutor.cancel(run_id)（尽力而为，失败仅警告）
- 测试 ~6（tests/test_scheduler_cancel.py 新文件）：cancel 本地
  running run → cancelled + job idle / cancel 非运行任务 → None +
  EXIT_CONFIG / 执行完成前 cancel → run 保持 cancelled（结果丢弃，
  用慢执行器 + 线程证明）/ 完成后 cancel 无操作 / CLI json 信封 /
  recover_interrupted 不受影响
- 慢执行器：测试内定义 SleepExecutor（sleep_fn 注入或直接
  time.sleep + threading.Thread 触发）——同步 trigger 阻塞，线程
  cancel

### Step 115（ADR-115）：远程 cancel 协议（尽力而为） —— ✅ 完成（JobCommand.run_token + /rpc/cancel + JobResult.cancelled 回执；回执链路由 Step 116 e2e 覆盖）

- worker app 级 in-flight registry（线程安全 dict + Lock：
  run_token → cancelled 标志）
- `POST /rpc/cancel` {run_token}（token 鉴权 401/503；未知 token
  404 无操作；已知 → 标记 cancelled + 200 {cancelled: true}）；
  `_ENDPOINTS` +1
- `rpc_execute` 开始登记 run_token（= 调度端 run_id），扫描完成后
  若已标记 → JobResult 增 optional `cancelled: bool = False`（契约
  向后兼容，本地执行不受影响）置 true 回传
- `RemoteScanExecutor.cancel(run_token)`：POST /rpc/cancel，401/503/
  404/网络失败全部仅警告（尽力而为，无重试）
- CLI job cancel 在 --remote-url 时通知远端（见 Step 114）
- 测试 ~5（test_cli_remote.py 扩展）：/rpc/cancel 401（无/错 token）
  / 503（worker 未启用）/ 404（未知 run_token）/ 200 标记 +
  rpc_execute 回传 cancelled:true / RemoteScanExecutor.cancel 网络
  失败不抛

### Step 116（ADR-116）：跨 workspace e2e 证明 + 收尾

- 端到端：调度端 trigger 远程任务 → 慢 worker（注入慢 DataSentry/
  SleepExecutor 于 worker 端）→ 调度端 job cancel --remote-url →
  调度端 run cancelled + worker 端 cancelled 标记回执 → 远端结果
  最终到达但调度端 run 保持 cancelled（结果丢弃）；跨 workspace
  物理隔离不回归（scan 落 worker 库）
- 收尾：ADR-114/115/116 落档；CHANGELOG [0.24.0] 三小节；
  DEVELOPMENT.md V22 段；计划书 ✅ + 坑位；index.html → 0.24.0 /
  新测试数 / 111 ADR / 20 tools
- bump 0.23.0→0.24.0（3 文件 + uv.lock）单独 commit；tag v0.24.0；
  PyPI/Pages/CI 全绿；`gh release create v0.24.0 --title "v0.24.0 —
  调度端 cancel 语义闭环 + 远程 cancel 协议" --notes ...`

## 门禁（每步全绿才 commit）

1. `uv run --offline ruff check .`
2. `uv run --offline ruff format --check .`
3. `uv run --offline mypy --strict src/datasentry packages/core/src/datasentry_core`
4. `uv run --offline pytest -q --cov=datasentry_core --cov-fail-under=85`

## 验收

- `job cancel` 本地与远程全链路可用；run cancelled + 结果丢弃
- 远程 cancel 尽力而为（失败仅警告，调度端语义不受网络影响）
- 测试 1214 → ≥1226（+6+5+1 及以上）；mypy/ruff 全绿
- v0.24.0 完整发布（PyPI/Pages/CI/release）
- Scheduler trigger/存储 schema/既有 CLI 行为零变化；MCP tools 20
  不变；_ENDPOINTS 29

## 坑位（V22 预埋 + 历史复读）

1. `worker` 命令叶子、preflight 默认 False、CLI 信封 data 层级、
   失败语义（run failed 退出 0）等 V21 坑 1-15 全部有效
2. cancel 竞态只靠 finish_run 事务内状态检查——绝不在事务外读
   （否则丢结果或复活 cancelled run）
3. 慢执行器测试用线程触发（trigger 同步阻塞），cancel 从主线程进
4. /rpc/cancel 与 /rpc/execute 同一 token 面（401/503 语义一致）
5. JobResult.cancelled 是可选字段——本地路径（LocalScanExecutor）
   与旧契约零变化，不能动既有字段语义
6. worker 端 registry 是 app 级单例（create_app 闭包内），多线程
   访问必须 Lock；run_token 用调度端 run_id（worker 无 job 概念）
7. finish_run 的 cancelled 分支 return 前不 COMMIT（自动回滚），
   绝不影响 run/job 既有数据
8. test_cli_remote.py 的 _serve 继续复制模式；新文件
   tests/test_scheduler_cancel.py 另写本地 slow executor
9. 远程 cancel 网络失败仅警告——测试断言不能期望 CLI 退出码变化
10. 跨 workspace e2e 的慢 worker：worker 端注入 slow scan（不能改
    DataSentry 本体，测试内 monkeypatch LocalScanExecutor 或
    用大文件+采样？——优先 monkeypatch/sleep executor 注入）

## 最终报告格式（交付）

V22 报告五段式中文：目标与完成情况 / 决策记录（ADR 108→111）/
测试与质量 / 关键技术点 / 版本与发布 + 遗留与 V23 候选（异步触发
协议、报告交互增强、webhook 去重）。
