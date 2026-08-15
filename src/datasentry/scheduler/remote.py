"""Step 90/91（V14 调度执行器分布式化，ADR-090）+ Step 108（V20，ADR-108）
远程执行器。

- `RemoteScanExecutor`：实现 `ScanExecutor` Protocol——把
  `JobCommand` 序列化下发到远端 worker（`POST /rpc/execute`，
  `X-Datasentry-Token` 共享密钥），远端执行扫描并回传
  `JobResult`。
- 语义与 `LocalScanExecutor` 完全一致：同步等待执行结果；失败
  （网络/鉴权/远端错误/契约不符）统一抛 `ScanExecutionError`，
  由 `Scheduler._run_job` 按既有 retry/死信语义落库（core 零改动）。
- Step 108（ADR-108）细化：超时细分（connect/read 与总超时分离）、
  传输层重试退避（仅网络错误与 `_RETRYABLE_HTTP_STATUS`，4xx/契约
  错误立即失败）、错误分类（`category`/`retryable` 属性）。
  **边界**：执行器内重试只治传输瞬时故障；任务级重试/死信仍归
  `Scheduler._run_job`（`retries=0` 时行为与 V14 完全一致）。
- Step 109（ADR-109）细化：健康探测——`health()` 调公开信息面
  `GET /rpc/health`（无数据、无需 token）；`execute(preflight=True)`
  执行前探测，失败快速失败（不等总超时）；默认关闭，向后兼容。
- Step 110（ADR-110）细化：报告回传——执行成功且 JobResult 携带
  `report_path` 时，经 `GET /rpc/reports/{scan_run_id}` 拉回远端
  报告写入本地 `report_dir/{scan_run_id}.html`（**尽力而为**：失败
  仅日志，不影响调度——与 LocalScanExecutor._export_report 的
  ADR-070 语义一致）；未配置 `report_dir` 时不拉取。
- 可测性：`client_factory` 注入——生产默认 `httpx.Client`（真
  socket）；测试注入 `httpx.ASGITransport`（FastAPI app 直连）
  或 `httpx.MockTransport`（网络异常场景）。
"""

from __future__ import annotations

import json
import logging
import random
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

import httpx
from pydantic import ValidationError

from datasentry.scheduler.models import JobCommand, JobResult

logger = logging.getLogger(__name__)

#: 传输层可重试的 HTTP 状态：5xx 服务端抖动 + 408/429（限流）。
_RETRYABLE_HTTP_STATUS = frozenset({408, 429, 500, 502, 503, 504})


class ScanExecutionError(RuntimeError):
    """远程执行失败：网络、鉴权、远端错误、超时或契约不符。

    - `category`：故障分类（network / http / contract）；
    - `retryable`：仅传输层瞬时故障（网络错误、`_RETRYABLE_HTTP_STATUS`）
      为 True；4xx 与契约错误永 False——任务级重试/死信由
      `Scheduler._run_job` 负责（Step 108 边界，ADR-108）。
    """

    def __init__(
        self,
        message: str,
        *,
        category: str = "unknown",
        retryable: bool = False,
    ) -> None:
        super().__init__(message)
        self.category = category
        self.retryable = retryable


class RemoteScanExecutor:
    """把扫描任务委托给远端 worker 执行的执行器（HTTP 同步）。"""

    _ENDPOINT = "/rpc/execute"
    _HEALTH_ENDPOINT = "/rpc/health"
    _REPORTS_ENDPOINT = "/rpc/reports"
    _TOKEN_HEADER = "X-Datasentry-Token"

    def __init__(
        self,
        base_url: str,
        token: str,
        timeout: float = 120.0,
        client_factory: Callable[[], Any] | None = None,
        *,
        connect_timeout: float | None = None,
        read_timeout: float | None = None,
        retries: int = 0,
        backoff_base: float = 0.5,
        backoff_jitter: float = 0.1,
        sleep_fn: Callable[[float], None] = time.sleep,
        rng: random.Random | None = None,
        report_dir: Path | None = None,
        preflight: bool = False,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._endpoint = f"{self._base_url}{self._ENDPOINT}"
        self._token = token
        self._timeout = httpx.Timeout(
            timeout,
            connect=connect_timeout or timeout,
            read=read_timeout or timeout,
        )
        self._client_factory = client_factory
        self._retries = retries
        self._backoff_base = backoff_base
        self._backoff_jitter = backoff_jitter
        self._sleep_fn = sleep_fn
        self._rng = rng or random.Random()
        self._report_dir = report_dir
        self._preflight = preflight

    def _client(self) -> Any:
        if self._client_factory is not None:
            return self._client_factory()
        return httpx.Client(timeout=self._timeout)

    def _backoff_delay(self, attempt: int) -> float:
        """第 attempt 次重试前的退避：base * 2**(attempt-1) + 抖动。"""
        delay = self._backoff_base * (2.0 ** (attempt - 1))
        if self._backoff_jitter > 0:
            delay += float(self._rng.uniform(0.0, self._backoff_jitter))
        return delay

    def _get_text(self, path: str) -> str:
        """GET 原始文本（供报告等非 JSON 响应）；失败抛
        `ScanExecutionError`（分类语义与 execute 一致）。"""
        try:
            client = self._client()
            try:
                response = client.get(
                    f"{self._base_url}{path}",
                    headers={self._TOKEN_HEADER: self._token},
                )
            finally:
                client.close()
        except Exception as exc:
            raise ScanExecutionError(
                f"network: GET {path} failed: {exc}",
                category="network",
                retryable=True,
            ) from exc
        if response.status_code >= 400:
            raise ScanExecutionError(
                f"GET {path} failed: HTTP {response.status_code}: {response.text[:200]}",
                category="http",
                retryable=False,
            )
        return str(response.text)

    def _get(self, path: str) -> Any:
        """GET 同步请求（token 头恒带，服务端信息面可忽略）；失败抛
        `ScanExecutionError`（分类语义与 execute 一致，契约错误不可重试）。"""
        text = self._get_text(path)
        try:
            return json.loads(text)
        except ValueError as exc:
            raise ScanExecutionError(
                f"GET {path} returned invalid JSON: {exc}",
                category="contract",
                retryable=False,
            ) from exc

    def health(self) -> dict[str, Any]:
        """探测远端 worker（`GET /rpc/health`，公开信息面）；失败抛
        `ScanExecutionError`。返回 worker 健康信息 dict（service/version/
        worker 标志），供执行前 preflight 与状态诊断用。"""
        data = self._get(self._HEALTH_ENDPOINT)
        if not isinstance(data, dict):
            raise ScanExecutionError(
                f"GET {self._HEALTH_ENDPOINT} returned non-object: {data!r}",
                category="contract",
                retryable=False,
            )
        return data

    def _pull_report(self, result: JobResult) -> None:
        """远端报告回传（Step 110，ADR-110）：尽力而为。

        JobResult 携带 `report_path`（远端已导出）且配置了 `report_dir`
        时，拉取 `GET /rpc/reports/{scan_run_id}` 写入本地
        `report_dir/{scan_run_id}.html`；失败仅记录日志，不影响调度
        （与 LocalScanExecutor._export_report 的 ADR-070 语义一致）。
        """
        if self._report_dir is None or result.report_path is None or result.scan_run_id is None:
            return
        try:
            content = self._get_text(f"{self._REPORTS_ENDPOINT}/{result.scan_run_id}")
            out = self._report_dir / f"{result.scan_run_id}.html"
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_text(content, encoding="utf-8")
            logger.info("pulled report for %s -> %s", result.scan_run_id, out)
        except Exception as exc:
            logger.warning("report pull failed for %s: %s", result.scan_run_id, exc)

    def cancel(self, run_token: str) -> bool:
        """远端取消（V22，Step 115，ADR-115）：尽力而为。

        POST /rpc/cancel {run_token}；成功返回 True。401/503/404 与
        网络失败**仅警告**（调度端取消语义不依赖远端回执——run 已在
        调度端标 cancelled，远端只是结果作废标记）。
        """
        try:
            client = self._client()
            try:
                response = client.post(
                    f"{self._base_url}/rpc/cancel",
                    headers={self._TOKEN_HEADER: self._token},
                    json={"run_token": run_token},
                )
            finally:
                client.close()
        except Exception as exc:
            logger.warning("remote cancel for %s failed: %s", run_token, exc)
            return False
        if response.status_code != 200:
            logger.warning("remote cancel for %s failed: HTTP %s", run_token, response.status_code)
            return False
        return True

    def execute(self, command: JobCommand, *, preflight: bool | None = None) -> JobResult:
        """下发任务并同步等待远端结果；失败抛 `ScanExecutionError`。

        仅网络错误与可重试 HTTP 状态退避重试（`retries` 次）；4xx 与
        契约错误立即失败（retryable=False）。重试耗尽后抛最后一次错误。
        `preflight`（Step 109，ADR-109）时先 `health()` 探测——失败
        快速失败（不等总超时）；参数为 None 时用构造默认 `preflight`
        （默认 False，向后兼容）。执行成功后按 Step 110（ADR-110）
        尽力而为回传远端报告。
        """
        if self._preflight if preflight is None else preflight:
            self.health()
        last_error: ScanExecutionError | None = None
        for attempt in range(self._retries + 1):
            if attempt > 0 and last_error is not None:
                self._sleep_fn(self._backoff_delay(attempt))
            try:
                result = self._execute_once(command)
                break
            except ScanExecutionError as exc:
                last_error = exc
                if not exc.retryable:
                    raise
        else:
            assert last_error is not None
            raise last_error
        self._pull_report(result)
        return result

    def _execute_once(self, command: JobCommand) -> JobResult:
        try:
            client = self._client()
            try:
                response = client.post(
                    self._endpoint,
                    headers={self._TOKEN_HEADER: self._token},
                    json=command.model_dump(mode="json"),
                )
            finally:
                client.close()
        except Exception as exc:
            raise ScanExecutionError(
                f"network: remote execution to {self._endpoint} failed: {exc}",
                category="network",
                retryable=True,
            ) from exc
        if response.status_code >= 400:
            raise ScanExecutionError(
                f"remote execution to {self._endpoint} failed: "
                f"HTTP {response.status_code}: {response.text[:200]}",
                category="http",
                retryable=response.status_code in _RETRYABLE_HTTP_STATUS,
            )
        try:
            return JobResult.model_validate(response.json())
        except (ValueError, ValidationError) as exc:
            raise ScanExecutionError(
                f"remote execution to {self._endpoint} returned invalid result: {exc}",
                category="contract",
                retryable=False,
            ) from exc
