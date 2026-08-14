"""Step 93/94（V15 多 worker 池与容错路由，ADR-093）worker 池执行器。

- `RemoteWorker`：远端执行节点描述（id/url/token）。
- `WorkerPoolExecutor`：实现 `ScanExecutor` Protocol——round-robin
  顺序派发；失败（含不可达）→ 冷却该 worker 并转移下一节点；
  全部失败 → 统一 `ScanExecutionError`（摘要含各节点错误）；
  可选健康预检（GET /health，默认关闭）。
- 语义与单点 `RemoteScanExecutor` 一致：同步等待；失败由
  `Scheduler._run_job` 按既有 retry/死信语义落库（core 零改动）。
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from datasentry.scheduler.models import JobCommand, JobResult
from datasentry.scheduler.remote import RemoteScanExecutor, ScanExecutionError

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class RemoteWorker:
    """远端执行节点：id（标识/冷却键）、url（base_url）、token。"""

    id: str
    url: str
    token: str


class WorkerPoolExecutor:
    """多 worker 池：round-robin + 失败转移 + 冷却（容错路由）。"""

    def __init__(
        self,
        workers: list[RemoteWorker],
        *,
        timeout: float = 120.0,
        cooldown: float = 60.0,
        health_check: bool = False,
        executor_factory: Callable[[RemoteWorker], Any] | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if not workers:
            raise ValueError("WorkerPoolExecutor requires at least one worker")
        self._workers = workers
        self._cooldown = cooldown
        self._health_check = health_check
        self._clock = clock
        self._executor_factory = executor_factory
        self._executors = {
            w.id: (
                executor_factory(w)
                if executor_factory is not None
                else RemoteScanExecutor(w.url, token=w.token, timeout=timeout)
            )
            for w in workers
        }
        self._cursor = 0
        self._cooldown_until: dict[str, float] = {}

    def _healthy(self, worker: RemoteWorker, now: float) -> bool:
        until = self._cooldown_until.get(worker.id)
        if until is not None and now < until:
            return False
        if self._health_check:
            try:
                import httpx

                response = httpx.get(f"{worker.url.rstrip('/')}/health", timeout=1.0)
                return response.status_code < 500
            except Exception:
                return False
        return True

    def execute(self, command: JobCommand) -> JobResult:
        """自轮询起始点整轮遍历 worker；失败转移；全部失败抛最终错误（含摘要）。"""
        start = self._cursor % len(self._workers)
        errors: list[str] = []
        attempted = 0
        for i in range(len(self._workers)):
            worker = self._workers[(start + i) % len(self._workers)]
            if not self._healthy(worker, self._clock()):
                continue
            attempted += 1
            try:
                result = self._executors[worker.id].execute(command)
                self._cursor = (start + i + 1) % len(self._workers)
                return result
            except ScanExecutionError as exc:
                errors.append(f"{worker.id}: {exc}")
                self._cooldown_until[worker.id] = self._clock() + self._cooldown
                logger.warning("worker %s failed, failing over: %s", worker.id, exc)
            except Exception as exc:
                errors.append(f"{worker.id}: {type(exc).__name__}: {exc}")
                self._cooldown_until[worker.id] = self._clock() + self._cooldown
        self._cursor = (start + 1) % len(self._workers)
        raise ScanExecutionError(f"all {attempted} workers failed: " + "; ".join(errors))

    def reset(self) -> None:
        """清空冷却表（测试/运维恢复用）。"""
        self._cooldown_until.clear()
