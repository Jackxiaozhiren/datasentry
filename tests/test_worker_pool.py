"""Step 93（ADR-093）：WorkerPoolExecutor 路由与容错测试。

假 worker app（真 HTTP，uvicorn 后台线程）——可辨识 scan_run_id
区分节点；失败/不可达/冷却/健康预检场景全覆盖。
"""

from __future__ import annotations

import threading
import time
from typing import Any

import pytest
from fastapi import FastAPI, HTTPException
from uvicorn import Config, Server

from datasentry.scheduler.models import JobCommand, JobResult
from datasentry.scheduler.pool import RemoteWorker, WorkerPoolExecutor
from datasentry.scheduler.remote import ScanExecutionError


class _Server:
    def __init__(self, app: FastAPI) -> None:
        config = Config(app, host="127.0.0.1", port=0, log_level="warning")
        self.server = Server(config)
        self.thread = threading.Thread(target=self.server.run, daemon=True)

    def start(self) -> str:
        self.thread.start()
        for _ in range(200):
            if self.server.started:
                break
            time.sleep(0.01)
        assert self.server.started, "uvicorn failed to start"
        port = self.server.servers[0].sockets[0].getsockname()[1]
        return f"http://127.0.0.1:{port}"

    def stop(self) -> None:
        self.server.should_exit = True
        self.thread.join(timeout=10)


def _ok_app(marker: str) -> FastAPI:
    app = FastAPI()

    @app.post("/rpc/execute")
    def execute() -> dict[str, Any]:
        return JobResult(scan_run_id=f"{marker}_run", total_issues=1).model_dump()

    return app


def _fail_app(status: int = 500) -> FastAPI:
    app = FastAPI()

    @app.post("/rpc/execute")
    def execute() -> dict[str, Any]:
        raise HTTPException(status_code=status, detail="boom")

    return app


def _unhealthy_health_app() -> FastAPI:
    app = FastAPI()

    @app.get("/health")
    def health() -> dict[str, Any]:
        raise HTTPException(status_code=500, detail="unhealthy")

    @app.post("/rpc/execute")
    def execute() -> dict[str, Any]:
        return JobResult(scan_run_id="H_run", total_issues=1).model_dump()

    return app


def _cmd() -> JobCommand:
    return JobCommand(project="p", path="orders.csv")


class TestWorkerPool:
    def test_round_robin_distributes(self) -> None:
        sa, sb = _Server(_ok_app("A")), _Server(_ok_app("B"))
        url_a, url_b = sa.start(), sb.start()
        try:
            pool = WorkerPoolExecutor(
                [RemoteWorker("a", url_a, "t"), RemoteWorker("b", url_b, "t")]
            )
            r1 = pool.execute(_cmd())
            r2 = pool.execute(_cmd())
            assert {r1.scan_run_id, r2.scan_run_id} == {"A_run", "B_run"}
        finally:
            sa.stop()
            sb.stop()

    def test_failover_on_remote_error(self) -> None:
        sa, sb = _Server(_fail_app()), _Server(_ok_app("B"))
        url_a, url_b = sa.start(), sb.start()
        try:
            pool = WorkerPoolExecutor(
                [RemoteWorker("a", url_a, "t"), RemoteWorker("b", url_b, "t")],
                cooldown=0,
            )
            result = pool.execute(_cmd())
            assert result.scan_run_id == "B_run"
        finally:
            sa.stop()
            sb.stop()

    def test_failover_on_unreachable(self) -> None:
        sb = _Server(_ok_app("B"))
        url_b = sb.start()
        dead = _Server(_ok_app("A"))
        dead_url = dead.start()
        dead.stop()
        try:
            pool = WorkerPoolExecutor(
                [RemoteWorker("a", dead_url, "t"), RemoteWorker("b", url_b, "t")],
                cooldown=0,
            )
            result = pool.execute(_cmd())
            assert result.scan_run_id == "B_run"
        finally:
            sb.stop()

    def test_all_workers_failed_raises_summary(self) -> None:
        sa, sb = _Server(_fail_app()), _Server(_fail_app(501))
        url_a, url_b = sa.start(), sb.start()
        try:
            pool = WorkerPoolExecutor(
                [RemoteWorker("a", url_a, "t"), RemoteWorker("b", url_b, "t")],
                cooldown=0,
            )
            with pytest.raises(ScanExecutionError, match="all 2 workers failed"):
                pool.execute(_cmd())
        finally:
            sa.stop()
            sb.stop()

    def test_cooldown_skips_failed_worker(self) -> None:
        sa, sb = _Server(_fail_app()), _Server(_ok_app("B"))
        url_a, url_b = sa.start(), sb.start()
        try:
            pool = WorkerPoolExecutor(
                [RemoteWorker("a", url_a, "t"), RemoteWorker("b", url_b, "t")],
                cooldown=3600,
            )
            pool.execute(_cmd())
            for _ in range(3):
                assert pool.execute(_cmd()).scan_run_id == "B_run"
        finally:
            sa.stop()
            sb.stop()

    def test_cooldown_expires_and_recovers(self) -> None:
        sa, sb = _Server(_fail_app()), _Server(_ok_app("B"))
        url_a, url_b = sa.start(), sb.start()
        try:
            pool = WorkerPoolExecutor(
                [RemoteWorker("a", url_a, "t"), RemoteWorker("b", url_b, "t")],
                cooldown=0.2,
            )
            assert pool.execute(_cmd()).scan_run_id == "B_run"
            time.sleep(0.3)
            pool.reset()
            assert pool.execute(_cmd()).scan_run_id == "B_run"
            sa.stop()
            url_a_restart = _Server(_ok_app("A"))
            new_url_a = url_a_restart.start()
            try:
                pool2 = WorkerPoolExecutor(
                    [RemoteWorker("a", new_url_a, "t"), RemoteWorker("b", url_b, "t")],
                    cooldown=0.2,
                )
                assert pool2.execute(_cmd()).scan_run_id in {"A_run", "B_run"}
            finally:
                url_a_restart.stop()
        finally:
            sa.stop()
            sb.stop()

    def test_health_check_skips_unhealthy(self) -> None:
        sa, sb = _Server(_unhealthy_health_app()), _Server(_ok_app("B"))
        url_a, url_b = sa.start(), sb.start()
        try:
            pool = WorkerPoolExecutor(
                [RemoteWorker("a", url_a, "t"), RemoteWorker("b", url_b, "t")],
                health_check=True,
                cooldown=0,
            )
            result = pool.execute(_cmd())
            assert result.scan_run_id == "B_run"
        finally:
            sa.stop()
            sb.stop()

    def test_empty_workers_rejected(self) -> None:
        with pytest.raises(ValueError, match="at least one worker"):
            WorkerPoolExecutor([])
