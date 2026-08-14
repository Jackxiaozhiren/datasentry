# V14 开发计划书（v0.16.0）——调度执行器分布式化：远程执行

## 一、目标

V13 已把调度**管理面**三面补齐（CLI/HTTP API/MCP 同源），但
**执行面**仍锁死在本地：`Scheduler` 注入的只有
`LocalScanExecutor`（同一进程内同步扫描）。V2-D（Step 51）时
core 注释已预留："扩展点：`ScanExecutor` Protocol——未来可换
云函数/SSH 远端执行"。

V14 = **远程执行器**：调度端把 `JobCommand` 序列化下发到远端
worker（另一台 DataSentry 实例 / 独立执行节点），远端执行扫描
并回传 `JobResult`。拓扑：

```
调度端（Scheduler + jobs 库 + worker 线程）
  └─ RemoteScanExecutor ──HTTP──▶ worker 端（POST /rpc/execute）
                                   └─ LocalScanExecutor（远端本地执行）
```

边界（延续 V2-D 决策，不过度设计）：
- **不做**多 worker 路由/负载均衡/任务队列/Celery——单 worker
  端点，路由留给未来；
- **不做**异步回调——与 Local 同语义：同步等待执行结果
  （HTTP 长请求），失败按既有 retry/死信语义落库；
- **鉴权最小**：共享 token（`X-Datasentry-Token` 头，常量时间
  比对）；未配置 token 时 worker 端点默认禁用（503）。
版本：v0.16.0（core 包 pyproject 恒 0.7.0）。

## 二、Step 分解

### Step 90（ADR-090）RemoteScanExecutor（客户端）

- **现状**：`ScanExecutor` Protocol（core.py:201）已定义
  `execute(command: JobCommand) -> JobResult`；`Scheduler` 构造
  可注入（core.py:316）；`WebhookNotifier` 已有 httpx 用法可参照。
- **方案**：新文件 `src/datasentry/scheduler/remote.py`：
  - `ScanExecutionError(Exception)`：执行失败（网络/鉴权/远端
    错误/超时/契约不符）
  - `RemoteScanExecutor(base_url, token, timeout=120.0)`
    - `execute(command)`：`POST {base_url}/rpc/execute`，头
      `X-Datasentry-Token`，body=`command.model_dump(mode="json")`
    - 2xx → `JobResult.model_validate(data)`（契约不符 → raise）
    - 4xx/5xx/网络异常/超时 → `raise ScanExecutionError`
  - **可测性**：构造接受 `client_factory: Callable[[], httpx.Client]`
    ——生产默认 `httpx.Client(timeout=...)`；测试注入
    `ASGITransport`（FastAPI app 直连）或 `MockTransport`（不可达
    场景）
- **语义**：与 Local 完全一致——同步等待；异常由
  `Scheduler._run_job` 统一落 failed + 重试（零改动 scheduler
  core）。
- **测试**（`tests/test_remote_executor.py`）：成功回传 / 500 →
  error / 401 → error / 契约不符（非 JobResult 形状）→ error /
  网络异常（MockTransport raise）→ error。

### Step 91（ADR-091）worker 端点 POST /rpc/execute（服务端）

- **方案**（api.py）：
  - `create_app(project, worker_token=None)`：token 可配置
    （环境变量 `DATASENTRY_WORKER_TOKEN`；未配置 → 端点禁用）
  - `POST /rpc/execute`：
    - 未配置 token → 503 "worker endpoint disabled"
    - `X-Datasentry-Token` 缺失/不符（`secrets.compare_digest`）
      → 401
    - `JobCommand.model_validate(body)` 失败 → 422
    - `LocalScanExecutor().execute(cmd)` → 200 `JobResult` json；
      执行异常 → 500 `{"error": ...}`（不回传敏感堆栈）
- **安全**：默认关闭；token 必配才启用。
- **测试**（`tests/test_api_worker.py`）：503 disabled / 401 缺
  token / 401 错 token / 200 执行成功（校验 scan_run 落库）/
  422 非法 body / 500 执行异常（指向不存在文件）。

### Step 92（ADR-092）端到端 + CLI/文档 + 发布 v0.16.0

- **端到端**（`tests/test_remote_e2e.py`）：worker app（带
  token，ASGITransport 直连）+ 调度端
  `Scheduler(SchedulerStore(tmp), RemoteScanExecutor(...,
  client_factory=...))` → job trigger → run completed + 远端
  真实执行扫描（scan history 落库）→ webhook 通知照常。
- **CLI**：`datasentry worker` 子命令——`--port`/`--token` 参数，
  用 `uvicorn` 启动 worker 端点（fastapi-cli 已带 uvicorn）；
  文档写清拓扑与安全。
- **文档**：CHANGELOG [0.16.0] + DEVELOPMENT V14 段 + 计划书
  收官 + ADR-090/091/092 落档。
- **发布**：版本 0.16.0（根 pyproject + 双 __init__，core 包
  pyproject 恒 0.7.0）+ tag v0.16.0 → PyPI/Pages/CI/GitHub
  release。

## 三、验收

1. 门禁全链绿：ruff + mypy --strict（全命令）+ pytest 覆盖率
   >= 85%（当前 95.01%）
2. `ScanExecutor` Protocol 不变；`Scheduler` 零改动；
   `LocalScanExecutor` 零改动
3. 远端执行失败语义与本地一致（retry/死信/webhook）
4. worker 端点默认关闭，配 token 后可用