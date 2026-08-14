"""Step 90（ADR-090）：RemoteScanExecutor 远程执行器测试。

用 uvicorn 后台线程起真 HTTP 服务（fastapi-cli 已带 uvicorn），
走真实 socket 路径——与生产拓扑一致（httpx.ASGITransport 是
async-only，同步 Client 不可用，见 V14 计划书坑位）。
"""

from __future__ import annotations

import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

import httpx
import pytest
from fastapi import FastAPI, HTTPException
from uvicorn import Config, Server

from datasentry.scheduler.models import JobCommand, JobResult
from datasentry.scheduler.remote import RemoteScanExecutor, ScanExecutionError


@contextmanager
def _serve(app: FastAPI) -> Iterator[str]:
    config = Config(app, host="127.0.0.1", port=0, log_level="warning")
    server = Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    try:
        for _ in range(200):
            if server.started:
                break
            time.sleep(0.01)
        assert server.started, "uvicorn failed to start"
        port = server.servers[0].sockets[0].getsockname()[1]
        yield f"http://127.0.0.1:{port}"
    finally:
        server.should_exit = True
        thread.join(timeout=10)


def _ok_app() -> FastAPI:
    """返回 JobResult 的假 worker。"""

    app = FastAPI()

    @app.post("/rpc/execute")
    def execute(body: dict[str, Any]) -> dict[str, Any]:
        if body.get("path") != "orders.csv":
            raise HTTPException(status_code=422, detail="bad path")
        return JobResult(scan_run_id="sr_fake", total_issues=3, quality_score=88.5).model_dump()

    return app


class TestRemoteExecutor:
    def test_execute_success(self) -> None:
        with _serve(_ok_app()) as base_url:
            executor = RemoteScanExecutor(base_url, token="t")
            result = executor.execute(JobCommand(project="p", path="orders.csv", dataset_id="d"))
        assert isinstance(result, JobResult)
        assert result.scan_run_id == "sr_fake"
        assert result.total_issues == 3
        assert result.quality_score == 88.5

    def test_execute_base_url_trailing_slash(self) -> None:
        with _serve(_ok_app()) as base_url:
            executor = RemoteScanExecutor(f"{base_url}/", token="t")
            result = executor.execute(JobCommand(project="p", path="orders.csv"))
        assert result.scan_run_id == "sr_fake"

    def test_execute_remote_error_raises(self) -> None:
        with _serve(_ok_app()) as base_url:
            executor = RemoteScanExecutor(base_url, token="t")
            with pytest.raises(ScanExecutionError, match="HTTP 422"):
                executor.execute(JobCommand(project="p", path="missing.csv"))

    def test_execute_invalid_contract_raises(self) -> None:
        app = FastAPI()

        @app.post("/rpc/execute")
        def execute() -> str:
            return "not-a-job-result"

        with _serve(app) as base_url:
            executor = RemoteScanExecutor(base_url, token="t")
            with pytest.raises(ScanExecutionError, match="invalid result"):
                executor.execute(JobCommand(project="p", path="orders.csv"))

    def test_execute_extra_fields_ignored(self) -> None:
        """宽松契约：远端返回多余字段不炸（JobResult 全字段有默认值）。"""
        app = FastAPI()

        @app.post("/rpc/execute")
        def execute() -> dict[str, Any]:
            return {"scan_run_id": "sr_x", "worker_version": "0.16.0"}

        with _serve(app) as base_url:
            executor = RemoteScanExecutor(base_url, token="t")
            result = executor.execute(JobCommand(project="p", path="orders.csv"))
        assert result.scan_run_id == "sr_x"

    def test_execute_network_error_raises(self) -> None:
        def factory() -> Any:
            def handler(_: httpx.Request) -> httpx.Response:
                raise httpx.ConnectError("connection refused")

            return httpx.Client(transport=httpx.MockTransport(handler), base_url="http://x")

        executor = RemoteScanExecutor("http://worker", token="t", client_factory=factory)
        with pytest.raises(ScanExecutionError, match="connection refused"):
            executor.execute(JobCommand(project="p", path="orders.csv"))

    def test_execute_timeout_raises(self) -> None:
        def factory() -> Any:
            def handler(_: httpx.Request) -> httpx.Response:
                raise httpx.ReadTimeout("timed out")

            return httpx.Client(transport=httpx.MockTransport(handler), base_url="http://x")

        executor = RemoteScanExecutor("http://worker", token="t", client_factory=factory)
        with pytest.raises(ScanExecutionError, match="timed out"):
            executor.execute(JobCommand(project="p", path="orders.csv"))

    def test_execute_connection_refused_raises(self) -> None:
        with _serve(_ok_app()) as base_url:
            pass
        executor = RemoteScanExecutor(base_url, token="t")
        with pytest.raises(ScanExecutionError):
            executor.execute(JobCommand(project="p", path="orders.csv"))

    def test_skipped_result_passthrough(self) -> None:
        app = FastAPI()

        @app.post("/rpc/execute")
        def execute() -> dict[str, Any]:
            return JobResult(skipped=True, file_hash="h123").model_dump()

        with _serve(app) as base_url:
            executor = RemoteScanExecutor(base_url, token="t")
            result = executor.execute(JobCommand(project="p", path="orders.csv"))
        assert result.skipped is True
        assert result.file_hash == "h123"
