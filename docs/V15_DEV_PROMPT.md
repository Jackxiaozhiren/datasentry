# V15 开发计划书（v0.17.0）——多 worker 池与容错路由

## 一、目标

V14 打通"调度端 → 远端 worker"单点链路（RemoteScanExecutor）。
单点即单故障点：worker 宕机/网络抖动时任务直接失败。V15 把
执行面升级为 **worker 池**：

```
调度端（Scheduler + jobs 库 + worker 线程）
  └─ WorkerPoolExecutor ──round-robin + 失败转移──▶ worker A (/rpc/execute)
                                                    worker B (/rpc/execute)
                                                    worker C (/rpc/execute)
```

能力：
- **多端点轮询**：round-robin 顺序派发（无加权，保持简单）；
- **失败转移**：worker 执行失败/不可达 → 换下一 worker；全部
  失败 → 统一 ScanExecutionError（走既有 retry/死信语义）；
- **冷却**：执行失败的 worker 进入冷却期（默认 60s），期间不派
  发（防雪崩）；冷却结束自动恢复；
- **健康预检可选**：派发前 /health 探活（默认关闭——直连失败
  转移已兜底，避免双倍 RTT；配置打开可提前过滤）。
- **零迁移**：未配置 workers → 回退 LocalScanExecutor（现状
  不变）。

边界（延续 V2-D/V14）：
- **不做**异步任务队列（tick 仍同步执行，扫描期间阻塞）；队列
  与多 worker 并行执行明确留给 V16；
- **不做** worker 管理落库/注册中心/心跳上报——调度端配置即
  事实（环境变量/API 参数），worker 无状态；
- **不做**加权/亲和/按 job 路由——全局池 round-robin。
版本：v0.17.0（core 包 pyproject 恒 0.7.0）。

## 二、Step 分解

### Step 93（ADR-093）WorkerPoolExecutor（路由与容错）

- **方案**：新文件 `src/datasentry/scheduler/pool.py`：
  - `RemoteWorker`（dataclass）：`id` / `url` / `token`
  - `WorkerPoolExecutor(workers, timeout=120.0, cooldown=60.0,
    health_check=False)`
    - 内部维护 round-robin 游标 + 冷却表（worker id → 冷却截止）
    - `execute(command)`：按游标找可用（未冷却）worker 依次尝试；
      失败（ScanExecutionError）→ 记冷却 + 换下一；全部失败 →
      抛最终 ScanExecutionError（含各 worker 错误摘要）
    - `health_check=True` 时：派发前 GET `{url}/health`（1s 超
      时），不通 → 视同失败转移
    - `reset()`：清冷却（测试/运维用）
  - 实现 `ScanExecutor` Protocol，Scheduler 零改动。
- **测试**（`tests/test_worker_pool.py`）：真 HTTP 多 worker
  （复用 V14 `_serve` 模式）：
  - 双 worker 轮询分发（两次 execute 落不同 worker）
  - A 返回 500 → 转移 B 成功
  - A 端口关闭（连接拒绝）→ 转移 B
  - 双 worker 全失败 → ScanExecutionError（摘要含两错误）
  - 冷却：A 失败后 cooldown 内不再命中 A（连续执行均走 B）
  - 冷却恢复：cooldown=0 重置后 A 恢复参与
  - health_check=True：A /health 不通 → 跳过 A 走 B

### Step 94（ADR-094）调度端 worker 配置面

- **方案**：
  - 配置来源：环境变量 `DATASENTRY_WORKERS="url:token;url:token"`
    （分号分隔；url 与 token 冒号分隔）或
    `create_app(project, workers: list[tuple[str, str]] | None)`
  - `_build_scheduler`：解析配置 → 配置了 workers →
    `WorkerPoolExecutor`；否则 `LocalScanExecutor`（零迁移）
  - 解析函数 `parse_workers(raw: str) -> list[RemoteWorker]` 独立
    可测（非法条目跳过+告警，不炸启动）
  - `datasentry-server`（api:main）透传 env（自动生效）；
    `datasentry worker` 不变（worker 侧无状态）
- **测试**：
  - `parse_workers` 单元：正常多条目 / 空 / 缺 token / 空 url /
    畸形分隔 → 跳过非法
  - `_build_scheduler` 集成：env 配置 → Scheduler 执行器为
    WorkerPoolExecutor；未配置 → LocalScanExecutor
  - 端到端 1 例：api 服务（env 配 2 worker，其一为假 app 始终
    500）→ job trigger → run completed（转移成功）

### Step 95（ADR-095）文档 + 发布 v0.17.0

- CHANGELOG [0.17.0] + DEVELOPMENT V15 段 + ADR-093/094 落档 +
  计划书收官；README/拓扑文档补多 worker 部署示例
- 发布：版本 0.17.0（根 pyproject + 双 __init__，core 包
  pyproject 恒 0.7.0）+ tag v0.17.0 → PyPI/Pages/CI/GitHub
  release + uv.lock 同步

## 三、验收

1. 门禁全链绿：ruff + mypy --strict（全命令）+ pytest 覆盖率
   >= 85%（当前 95.01%）
2. `ScanExecutor` Protocol 与 `Scheduler` 零改动；
   `RemoteScanExecutor`/`LocalScanExecutor` 零改动
3. 单 worker 配置行为与 V14 一致（退化为直连语义）
4. 未配置 workers 行为与 V13 及更早完全一致（零迁移）