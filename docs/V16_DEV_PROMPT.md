# V16 开发计划书（v0.18.0）——异步任务队列与并行执行

## 一、目标

V13-V15 打通管理面/分布式执行/容错路由，但调度循环仍是
**同步串行**：`tick` 逐个 `_run_job`，一个扫描（20s+）阻塞整轮，
其余到期 job 排队；`trigger` 同步等待完成才返回。V16 把
执行推进异步化：

```
SchedulerWorker.tick ──claim 到期 jobs──▶ Scheduler._run_job ──▶ ThreadPoolExecutor(max_workers=N)
                                                 │                    ├─ job A（本地/远程执行）
                                                  └── 立即返回 ──────┼─ job B
                                                                     └─ job C
```

- **tick 异步派发**：只负责抢占（原子 claim，防重入语义不变），
  执行交给线程池，tick 立即返回 → 一轮不阻塞，多 job 并行执行；
- **trigger 异步化（仅 max_workers>1 时）**：提交后立即返回
  run_id，run 状态在后台推进；
- **默认 max_workers=1 → 行为与现状完全一致（同步）**，零迁移；
- **优雅关闭**：`Scheduler.shutdown(wait=True)` 停止接收新任务并
  等待 in-flight 完成；SchedulerWorker.stop 接入。
- **线程安全**：SchedulerStore 每操作独立连接（busy_timeout=5s）
  已天然安全，无需改造；互斥沿用 SQL 层原子 claim（同 job 重入
  防护与现状一致）。

边界：
- **不做**优先级/公平队列/背压——tick 按到期时间逐个提交；
- **不做**跨进程队列（调度端单进程内线程池；跨进程/多调度端
  留给未来）；
- **不做**执行取消/超时强制 kill（JobResult 等待超时沿用
  RemoteScanExecutor 既有 timeout）。
版本：v0.18.0（core 包 pyproject 恒 0.7.0）。

## 二、Step 分解

### Step 96（ADR-096）Scheduler 线程池异步执行 —— ✅ 完成

- `Scheduler.__init__` 增 `max_workers: int = 1`：
  - `>1` → `ThreadPoolExecutor(max_workers=N)`；`tick`/`trigger`
    用 `self._submit(job_id, run_id)`（内部 try/except 包住
    `_run_job`，异常仅 log——`_run_job` 本身全路径兜底）
  - `=1` → 保持现有同步路径（零行为变化）
- `Scheduler.shutdown(wait: bool = True)`：`=1` 时 no-op；`>1`
  时 `pool.shutdown(wait=wait)`；SchedulerWorker.stop 末尾调用
- 测试（tests/test_scheduler_async.py）：
  - 默认 max_workers=1：tick 同步完成（跑后 run 已终态）——
    与既有语义一致
  - max_workers=2 并行：注入慢 executor（sleep 0.3s/次），两个
    到期 job tick 一次 → 总耗时 < 0.55s（串行需 0.6s+），两个
    run 均 completed
  - max_workers=2 异步 trigger：trigger 立即返回 run_id，初始
    run 非终态，轮询至 completed
  - 互斥保持：慢 executor 下同一 job 再 trigger → None
  - shutdown(wait=True) 等 in-flight 完成（执行中 shutdown 后
    run 仍 completed）
  - 异常消化：executor 抛错（注入抛错 executor）→ run failed，
    无异常泄漏（future 回调消费）

### Step 97（ADR-097）API 接入 + 优雅关闭 —— ✅ 完成

- `_build_scheduler` 读 `DATASENTRY_MAX_WORKERS`（默认 1，非法
  回退 1 + 告警）传入 Scheduler；create_app 生命周期：
  FastAPI shutdown event → scheduler.shutdown(wait=True)
- SchedulerWorker.stop 已接 shutdown（Step 96）
- 测试（tests/test_api_workers_cfg.py）：
  - env 未设 → Scheduler max_workers=1（同步语义回归）
  - env=3 → Scheduler.max_workers==3
  - 集成：env=3 + 慢 worker 真 HTTP（uvicorn）→ trigger 立即
    202 → 轮询 runs 至 completed（并行执行端到端）
  - app shutdown 事件触发 graceful（TestClient context 退出
    不残留线程）

### Step 98（ADR-098）文档 + 发布 v0.18.0

- CHANGELOG [0.18.0] + DEVELOPMENT V16 段 + ADR-096/097 落档 +
  计划书收官；README 补 DATASENTRY_MAX_WORKERS 说明
- 发布：0.18.0 + tag v0.18.0 → PyPI/Pages/CI/GitHub release +
  uv.lock 同步

## 三、验收

1. 门禁全链绿：ruff + mypy --strict（全命令）+ pytest 覆盖率
   >= 85%（当前 95.01%）
2. 默认（无 env / max_workers=1）行为与 V15 及更早完全一致——
   全量既有测试不动一个断言
3. 互斥/retry/死信/webhook 语义在并行路径下不变
4. 优雅关闭：shutdown 等待 in-flight，无残留线程
- Step 96 坑位：create_job 收 ScheduledJob 对象（非关键字参数）；
  线程池提交后并发峰值需轮询观察（调度延迟）；uv 网络抖动时
  用 `uv run --offline`（索引拉取失败不阻塞）
- Step 97 坑位：端到端测试须显式 monkeypatch.setenv 后才断言池
  存在（否则默认同步正确回落）；run_id 断言只读不用 → F841
  （ruff 拦下）；app.state.scheduler 需显式挂载
