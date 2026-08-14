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

### Step 93（ADR-093）WorkerPoolExecutor（路由与容错） —— ✅ 完成

- 交付：`src/datasentry/scheduler/pool.py`（RemoteWorker +
  WorkerPoolExecutor：round-robin 整轮遍历 + 失败转移 + 冷却 +
  可选 /health 预检 + reset）；tests/test_worker_pool.py 8 例全绿；
  门禁全绿（95.01%）
- 坑位：**游标与 candidates 取模混用导致同节点重复命中**（a 被
  试两次 b 未试）——改为"自起始游标整轮遍历全列表，游标每轮
  推进 1"，错误摘要用实际尝试数（attempted）

### Step 94（ADR-094）调度端 worker 配置面 —— ✅ 完成

### Step 95（ADR-095）文档 + 发布 v0.17.0 —— ✅ 完成

## 三、验收

1. 门禁全链绿：ruff + mypy --strict（全命令）+ pytest 覆盖率
   >= 85%（当前 95.01%）
2. `ScanExecutor` Protocol 与 `Scheduler` 零改动；
   `RemoteScanExecutor`/`LocalScanExecutor` 零改动
3. 单 worker 配置行为与 V14 一致（退化为直连语义）
4. 未配置 workers 行为与 V13 及更早完全一致（零迁移）- Step 94 坑位：parse_workers 首冒号 partition 会切开 `://`（应
  rsplit 末位冒号）；/jobs 创建 201、trigger 202（非 200）；run
  的 summary 是 JSON 字符串字段（非 total_issues 平铺字段）
## 收官
- 门禁全绿（95.01%，108 源文件 mypy --strict）；全量连跑通过
- tag v0.17.0 → PyPI 0.17.0 ✓ / Pages ✓ / CI ✓ / GitHub release ✓；
  uv.lock 同步；DEVELOPMENT V15 段 + README 多 worker 部署示例
- ADR-093/094/095 落档
