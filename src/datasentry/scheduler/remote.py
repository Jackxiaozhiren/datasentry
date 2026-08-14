"""Step 90/91（V14 调度执行器分布式化，ADR-090）远程执行器。

- `RemoteScanExecutor`：实现 `ScanExecutor` Protocol——把
  `JobCommand` 序列化下发到远端 worker（`POST /rpc/execute`，
  `X-Datasentry-Token` 共享密钥），远端执行扫描并回传
  `JobResult`。
- 语义与 `LocalScanExecutor` 完全一致：同步等待执行结果；失败
  （网络/鉴权/远端错误/契约不符）统一抛 `ScanExecutionError`，
  由 `Scheduler._run_job` 按既有 retry/死信语义落库（core 零改动）。
- 可测性：`client_factory` 注入——生产默认 `httpx.Client`（真
  socket）；测试注入 `httpx.ASGITransport`（FastAPI app 直连）
  或 `httpx.MockTransport`（网络异常场景）。
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

from pydantic import ValidationError

from datasentry.scheduler.models import JobCommand, JobResult

logger = logging.getLogger(__name__)


class ScanExecutionError(RuntimeError):
    """远程执行失败：网络、鉴权、远端错误、超时或契约不符。"""


class RemoteScanExecutor:
    """把扫描任务委托给远端 worker 执行的执行器（HTTP 同步）。"""

    _ENDPOINT = "/rpc/execute"
    _TOKEN_HEADER = "X-Datasentry-Token"

    def __init__(
        self,
        base_url: str,
        token: str,
        timeout: float = 120.0,
        client_factory: Callable[[], Any] | None = None,
    ) -> None:
        self._endpoint = f"{base_url.rstrip('/')}{self._ENDPOINT}"
        self._token = token
        self._timeout = timeout
        self._client_factory = client_factory

    def _client(self) -> Any:
        if self._client_factory is not None:
            return self._client_factory()
        import httpx

        return httpx.Client(timeout=self._timeout)

    def execute(self, command: JobCommand) -> JobResult:
        """下发任务并同步等待远端结果；失败抛 `ScanExecutionError`。"""
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
            raise ScanExecutionError(f"remote execution to {self._endpoint} failed: {exc}") from exc
        if response.status_code >= 400:
            raise ScanExecutionError(
                f"remote execution to {self._endpoint} failed: "
                f"HTTP {response.status_code}: {response.text[:200]}"
            )
        try:
            return JobResult.model_validate(response.json())
        except (ValueError, ValidationError) as exc:
            raise ScanExecutionError(
                f"remote execution to {self._endpoint} returned invalid result: {exc}"
            ) from exc
