# V20（v0.22.0）开发任务书：调度端远程执行器细化

> 基准：v0.21.0 已发布（V19 完成，CI/PyPI/Pages/release 全绿，工作区干净）
> ADR 止于 ADR-107（102 条记录）；MCP tools 20；测试 1175 passed；覆盖率 94.94%

## 目标

补全 `RemoteScanExecutor`（remote.py 81 行）与 `LocalScanExecutor` 的能力
差：传输层超时细分 + 可重试性、执行前健康探测（快速失败）、远端报告
回传（现在远端执行后报告留在 worker，主进程拿不到——真实缺口）。
不扩 schema、不加协议（复用既有 HTTP + worker_token），不改 Scheduler
任务重试/死信语义（执行器内部重试仅限传输层瞬时故障）。

## 现状事实（探查结论，写进 ADR）

1. `RemoteScanExecutor`（remote.py）：单 timeout=120s、无重试；失败一律
   `ScanExecutionError`；错误无分类（网络/4xx/5xx/契约混在一起）
2. `/rpc/execute`（api.py:877）：worker_token 启用（未配置 503）、
   `X-Datasentry-Token` 常量时间比对（401）、JobCommand 校验（422）、
   LocalScanExecutor 执行（500）——数据面端点
3. 报告：`LocalScanExecutor._export_report` 写
   `<project>/reports/{scan_run_id}.html`（命令 export_report 时）；
   **Remote 执行成功但报告留在 worker 的 workspace，调度端拿不到**
4. `tests/test_remote_executor.py` 已有真 uvicorn socket 测试（Step 90）
5. CLI `schedule worker` 已是 worker 服务入口（cli.py:1412）

## 三个 Step（每步：实现 → 测试 → 四连门禁 → 文档 → 单独 commit → CI 全绿）

### Step 108（ADR-108）：超时细分 + 传输层重试退避 + 错误分类 —— ✅ 完成

- `RemoteScanExecutor` 参数化：`connect_timeout` / `read_timeout` /
  `total_timeout`（httpx.Timeout 分离）；`retries`（默认 0，向后兼容；
  仅 5xx/408/429 与网络错误可重试，4xx/契约错误不重试）；
  `backoff_base`（默认 0.5s）+ `backoff_jitter`（默认 0.1，可注入 rng）
- 错误分类 `_classify_exc`：网络（httpx.ConnectError 等）/ HTTP 状态 /
  契约（JSON/校验）——错误消息带分类前缀，便于排障
- **边界（ADR 写明）**：执行器内重试只治传输瞬时故障；任务级重试/死信
  仍归 Scheduler._run_job（既有语义零改动）
- 测试 ~8：网络异常不重试 retries=0 直接失败、5xx 重试后成功、
  5xx 耗尽 retries 失败、4xx 不重试、契约错误不重试、退避时序
  （注入 sleep_fn 断言 0.5/1.0 序列）、抖动注入 rng 确定性、错误
  消息含分类前缀
- CLI/REST 零变更

### Step 109（ADR-109）：健康探测 preflight —— ✅ 完成

- REST `GET /rpc/health`（**公开**：只返回 {service, version, worker:
  true} 静态信息，不涉数据——与数据面 /rpc/execute 分离；未配置
  worker_token 时仍返回 200 但 worker: false 或 503？——决策：恒 200，
  worker 标志由 token 配置决定，轻量无泄露）；`_ENDPOINTS` +1
- `RemoteScanExecutor.health() -> dict` 探测方法；`execute(...,
  preflight=False)` 参数（默认关，向后兼容）；preflight 开启时健康
  探测失败 → ScanExecutionError 立即快速失败（不等 total_timeout）
- 测试 ~5：health 200 结构断言、health 404/非 JSON → 探测失败、
  preflight=True 时失败快路径（不调 execute）、preflight=False 行为
  不变、健康通过后正常执行

### Step 110（ADR-110）：报告回传（远端报告下载） —— ✅ 完成

- REST `GET /rpc/reports/{scan_run_id}`：token 鉴权（同 execute，
  401/503）；报告文件不存在 → 404；存在 → 返回 HTML 文本
  （reports_dir/{scan_run_id}.html 约定）；`_ENDPOINTS` +1
- `RemoteScanExecutor`：JobResult 携带 report_path/report_size 时拉回
  报告写本地 reports_dir（尽力而为：失败仅日志，不影响调度——与
  本地 _export_report 的 ADR-070 语义一致）
- 测试 ~7：下载 200 写本地文件内容匹配、404 尽力而为不抛、401 拒绝、
  503 未启用、端到端（真 uvicorn worker：execute(export_report) →
  自动回传 → 本地文件存在）、非 export_report 任务不回传、
  report 路径解析（本地 reports 目录创建）

### 收尾（ADR-108/109/110 落档，对齐 V19 流程）

- CHANGELOG `[0.22.0]`（顶部）三小节；DEVELOPMENT.md V20 段；计划书
  3 Step ✅ + 坑位记录；index.html → 0.22.0 / 新测试数 / 105 ADR /
  20 tools
- bump 0.21.0→0.22.0 三处 + uv.lock（bump 单独 commit）；tag v0.22.0；
  PyPI/Pages/CI 全绿；`gh release create v0.22.0 --title "v0.22.0 —
  调度端远程执行器细化" --notes ...`

## 门禁（每步全绿才 commit）

1. `uv run --offline ruff check .`
2. `uv run --offline ruff format --check .`
3. `uv run --offline mypy --strict src/datasentry packages/core/src/datasentry_core`
4. `uv run --offline pytest -q --cov=datasentry_core --cov-fail-under=85`

## 验收

- 新增测试 ≥ 20 例；1175 → ≥1195 passed；覆盖率 ≥85%
- RemoteScanExecutor 可配置超时/重试；4xx 与契约错误永重试；
  传输重试与任务重试边界文档化
- `/rpc/health` + preflight 快速失败；`/rpc/reports/{scan_run_id}`
  鉴权下载；远端报告自动回传本地（尽力而为）
- REST `_ENDPOINTS` +2（health/reports）；MCP tools 20 不变；
  Scheduler 任务重试/死信语义零改动
- v0.22.0 完整发布（bump/tag/PyPI/Pages/CI/release/uv.lock/工作区干净）

## 坑位（V20 预埋 + 历史复读）

1. **重试边界**：执行器内重试只治传输瞬时故障；任务级重试/死信在
   Scheduler._run_job——ADR 必须写明，防未来合并逻辑
2. 重试测试不能真 sleep：注入 `sleep_fn`（默认 time.sleep）+ 固定
   jitter（rng 注入或 jitter=0）
3. `httpx.Timeout(connect=..., read=..., total=...)` 分离；MockTransport
   不触发 connect 超时——重试网络错误场景用 MockTransport raise
   httpx.ConnectError 模拟
4. preflight 默认 False：现有 test_remote_executor.py 用例（Step 90）
   行为零变化（向后兼容红线）
5. `/rpc/health` 公开 vs `/rpc/reports` 必须 token：数据面与信息面
   分离，health 不泄露任何数据/路径
6. 报告回传是尽力而为（ADR-070 语义）：下载失败只 warning，不抛
   ScanExecutionError、不影响 finish_run
7. 报告文件名约定 `reports/{scan_run_id}.html`（与 LocalScanExecutor
   一致）；回传前 mkdir reports 目录
8. 401 用 compare_digest 常量时间（沿用 execute）；503 = 未配置
   worker_token
9. CHANGELOG 新节在顶部；bump 单独 commit；显式 `git add <files>`
10. CLI 校验错误 EXIT_ERROR=3、配置缺失 EXIT_CONFIG=2；`--project`/
    `--format json` 全局参数在子命令前
11. `_ENDPOINTS` frozenset 同步 +2，相关严格断言（若存在）同步

## 最终报告格式（交付）

五段式中文汇报：完成概述 / 新增能力 / 测试与门禁数据 / 发布状态 /
遗留问题（V21 候选：报告交互增强、调度端 cancel/异步协议等）。
