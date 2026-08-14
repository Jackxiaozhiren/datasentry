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

### Step 90（ADR-090）RemoteScanExecutor（客户端） —— ✅ 完成

- 交付：`src/datasentry/scheduler/remote.py`（ScanExecutionError +
  RemoteScanExecutor，client_factory 注入 + base_url 尾斜杠容错）；
  tests/test_remote_executor.py 9 例全绿；门禁全绿
- 坑位：
  1. **httpx.ASGITransport 是 async-only**——同步 Client 调用报
     "async request with sync Client"；测试改用 uvicorn 后台线程
     起真 HTTP 服务（server.started 轮询 + port=0 随机端口，
     servers[0].sockets[0] 取端口），生产拓扑一致，Step 92 端到端
     直接复用
  2. **JobResult 全字段有默认值 + extra 忽略**——远端返回多余
     字段不炸（宽松契约，已固化测试）；"totally": "wrong" 这类
     额外字段场景不触发契约错误，须用非 JSON 形状（如字符串）
     测契约破坏

### Step 91（ADR-091）worker 端点 POST /rpc/execute（服务端） —— ✅ 完成

- 交付：`create_app(project, worker_token=None)` + 环境变量
  `DATASENTRY_WORKER_TOKEN` 后备；POST /rpc/execute（503 disabled
  / 401 compare_digest / 422 契约 / 200 JobResult / 500 仅类型名）；
  _ENDPOINTS 清单更新；tests/test_api_worker.py 7 例全绿；门禁
  全绿（95.01%）
- 坑位：JobCommand 校验用 `model_validate(body)`（dict）而非
  JSON 字符串（FastAPI 已解析 body）；错误 detail 不含异常文本
  防泄敏

### Step 92（ADR-092）端到端 + CLI/文档 + 发布 v0.16.0 —— ✅ 完成

- 交付：tests/test_remote_e2e.py 3 例（远端 trigger 完成+落库 /
  远端 500 → run failed / 同实例 API 服务与执行节点并存）；
  `datasentry worker`（--host/--port/--token，token 缺省警告 +
  端点禁用）；DEVELOPMENT V14 段；ADR-092；冒烟：真 HTTP 200
  （5 issues）与 401 均验证
- 坑位：orders.csv 夹具实际 5 issues（4 medium + 1 low），
  断言须写 5 而非直觉 4
- 发布 v0.16.0 见下（升版 + tag + PyPI/Pages/CI + GitHub
  release + uv.lock 同步）

## 三、验收

1. 门禁全链绿：ruff + mypy --strict（全命令）+ pytest 覆盖率
   >= 85%（当前 95.01%）
2. `ScanExecutor` Protocol 不变；`Scheduler` 零改动；
   `LocalScanExecutor` 零改动
3. 远端执行失败语义与本地一致（retry/死信/webhook）
4. worker 端点默认关闭，配 token 后可用