"""Step 90（ADR-090）：RemoteScanExecutor 远程执行器测试。

用 uvicorn 后台线程起真 HTTP 服务（fastapi-cli 已带 uvicorn），
走真实 socket 路径——与生产拓扑一致（httpx.ASGITransport 是
async-only，同步 Client 不可用，见 V14 计划书坑位）。
"""

from __future__ import annotations

import random
import threading
import time
from collections.abc import Callable, Iterator
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


class TestRemoteRetryV20:
    """Step 108（ADR-108）：超时细分 + 传输层重试退避 + 错误分类。"""

    @staticmethod
    def _mock_factory(
        handler: Callable[[httpx.Request], httpx.Response | Any],
    ) -> Callable[[], Any]:
        def factory() -> Any:
            return httpx.Client(
                transport=httpx.MockTransport(handler),  # type: ignore[arg-type]
                base_url="http://worker",
            )

        return factory

    def test_timeout_split_configuration(self) -> None:
        executor = RemoteScanExecutor("http://w", token="t")
        assert executor._timeout.connect == 120.0
        assert executor._timeout.read == 120.0
        assert executor._timeout.write == 120.0
        assert executor._timeout.pool == 120.0
        split = RemoteScanExecutor("http://w", token="t", connect_timeout=3.0, read_timeout=7.0)
        assert split._timeout.connect == 3.0
        assert split._timeout.read == 7.0
        assert split._timeout.write == 120.0
        assert split._timeout.pool == 120.0

    def test_default_no_retry_on_network_error(self) -> None:
        calls: list[str] = []

        def handler(_: httpx.Request) -> Any:
            calls.append("x")
            raise httpx.ConnectError("connection refused")

        executor = RemoteScanExecutor(
            "http://worker", token="t", client_factory=self._mock_factory(handler)
        )
        with pytest.raises(ScanExecutionError) as excinfo:
            executor.execute(JobCommand(project="p", path="orders.csv"))
        assert excinfo.value.category == "network"
        assert excinfo.value.retryable is True
        assert len(calls) == 1

    def test_network_error_retried_then_success(self) -> None:
        calls: list[str] = []

        def handler(_: httpx.Request) -> Any:
            calls.append("x")
            if len(calls) == 1:
                raise httpx.ConnectError("refused")
            return httpx.Response(
                200, json=JobResult(scan_run_id="sr_r", total_issues=1).model_dump()
            )

        sleeps: list[float] = []
        executor = RemoteScanExecutor(
            "http://worker",
            token="t",
            client_factory=self._mock_factory(handler),
            retries=2,
            backoff_jitter=0.0,
            sleep_fn=sleeps.append,
        )
        result = executor.execute(JobCommand(project="p", path="orders.csv"))
        assert result.scan_run_id == "sr_r"
        assert len(calls) == 2
        assert sleeps == [0.5]

    def test_5xx_retried_then_success(self) -> None:
        calls: list[str] = []

        def handler(_: httpx.Request) -> Any:
            calls.append("x")
            if len(calls) < 3:
                return httpx.Response(500, text="boom")
            return httpx.Response(
                200, json=JobResult(scan_run_id="sr_r", total_issues=1).model_dump()
            )

        sleeps: list[float] = []
        executor = RemoteScanExecutor(
            "http://worker",
            token="t",
            client_factory=self._mock_factory(handler),
            retries=2,
            backoff_jitter=0.0,
            sleep_fn=sleeps.append,
        )
        result = executor.execute(JobCommand(project="p", path="orders.csv"))
        assert result.scan_run_id == "sr_r"
        assert len(calls) == 3
        assert sleeps == [0.5, 1.0]

    def test_retries_exhausted_fails_with_last_error(self) -> None:
        calls: list[str] = []

        def handler(_: httpx.Request) -> Any:
            calls.append("x")
            return httpx.Response(503, text="unavailable")

        executor = RemoteScanExecutor(
            "http://worker",
            token="t",
            client_factory=self._mock_factory(handler),
            retries=2,
            backoff_jitter=0.0,
            sleep_fn=lambda _d: None,
        )
        with pytest.raises(ScanExecutionError) as excinfo:
            executor.execute(JobCommand(project="p", path="orders.csv"))
        assert excinfo.value.category == "http"
        assert excinfo.value.retryable is True
        assert "HTTP 503" in str(excinfo.value)
        assert len(calls) == 3

    def test_4xx_not_retried(self) -> None:
        calls: list[str] = []

        def handler(_: httpx.Request) -> Any:
            calls.append("x")
            return httpx.Response(401, text="unauthorized")

        executor = RemoteScanExecutor(
            "http://worker",
            token="t",
            client_factory=self._mock_factory(handler),
            retries=2,
            sleep_fn=lambda _d: None,
        )
        with pytest.raises(ScanExecutionError) as excinfo:
            executor.execute(JobCommand(project="p", path="orders.csv"))
        assert excinfo.value.category == "http"
        assert excinfo.value.retryable is False
        assert len(calls) == 1

    def test_429_retryable(self) -> None:
        calls: list[str] = []

        def handler(_: httpx.Request) -> Any:
            calls.append("x")
            if len(calls) == 1:
                return httpx.Response(429, text="rate limited")
            return httpx.Response(200, json=JobResult(scan_run_id="sr_r").model_dump())

        executor = RemoteScanExecutor(
            "http://worker",
            token="t",
            client_factory=self._mock_factory(handler),
            retries=1,
            backoff_jitter=0.0,
            sleep_fn=lambda _d: None,
        )
        result = executor.execute(JobCommand(project="p", path="orders.csv"))
        assert result.scan_run_id == "sr_r"
        assert len(calls) == 2

    def test_contract_error_not_retried(self) -> None:
        calls: list[str] = []

        def handler(_: httpx.Request) -> Any:
            calls.append("x")
            return httpx.Response(200, text="not-json")

        executor = RemoteScanExecutor(
            "http://worker",
            token="t",
            client_factory=self._mock_factory(handler),
            retries=2,
            sleep_fn=lambda _d: None,
        )
        with pytest.raises(ScanExecutionError) as excinfo:
            executor.execute(JobCommand(project="p", path="orders.csv"))
        assert excinfo.value.category == "contract"
        assert excinfo.value.retryable is False
        assert len(calls) == 1

    def test_jitter_bounded_and_deterministic(self) -> None:
        calls: list[str] = []

        def handler(_: httpx.Request) -> Any:
            calls.append("x")
            return httpx.Response(500, text="boom")

        def run(seed: int) -> tuple[list[float], float]:
            sleeps: list[float] = []
            executor = RemoteScanExecutor(
                "http://worker",
                token="t",
                client_factory=self._mock_factory(handler),
                retries=1,
                backoff_jitter=0.1,
                rng=random.Random(seed),
                sleep_fn=sleeps.append,
            )
            with pytest.raises(ScanExecutionError):
                executor.execute(JobCommand(project="p", path="orders.csv"))
            return sleeps, sleeps[0]

        _sleeps_a, delay_a = run(7)
        _sleeps_b, delay_b = run(7)
        assert delay_a == delay_b
        assert 0.5 <= delay_a < 0.6
        assert len(_sleeps_a) == 1


class TestRemoteHealthV20:
    """Step 109（ADR-109）：health() 探测 + execute(preflight) 快速失败。"""

    @staticmethod
    def _mock_factory(
        handler: Callable[[httpx.Request], httpx.Response | Any],
    ) -> Callable[[], Any]:
        def factory() -> Any:
            return httpx.Client(
                transport=httpx.MockTransport(handler),  # type: ignore[arg-type]
                base_url="http://worker",
            )

        return factory

    def test_health_success(self) -> None:
        app = FastAPI()

        @app.get("/rpc/health")
        def health() -> dict[str, str]:
            return {"service": "datasentry-worker", "version": "0.22.0", "worker": "true"}

        with _serve(app) as base_url:
            executor = RemoteScanExecutor(base_url, token="t")
            info = executor.health()
        assert info["service"] == "datasentry-worker"
        assert info["worker"] == "true"

    def test_health_http_error_raises(self) -> None:
        def handler(_: httpx.Request) -> Any:
            return httpx.Response(404, text="no health")

        executor = RemoteScanExecutor(
            "http://worker", token="t", client_factory=self._mock_factory(handler)
        )
        with pytest.raises(ScanExecutionError) as excinfo:
            executor.health()
        assert excinfo.value.category == "http"
        assert excinfo.value.retryable is False

    def test_health_contract_error_raises(self) -> None:
        def handler(_: httpx.Request) -> Any:
            return httpx.Response(200, text="not-json")

        executor = RemoteScanExecutor(
            "http://worker", token="t", client_factory=self._mock_factory(handler)
        )
        with pytest.raises(ScanExecutionError) as excinfo:
            executor.health()
        assert excinfo.value.category == "contract"

    def test_health_non_object_raises(self) -> None:
        def handler(_: httpx.Request) -> Any:
            return httpx.Response(200, json=[1, 2, 3])

        executor = RemoteScanExecutor(
            "http://worker", token="t", client_factory=self._mock_factory(handler)
        )
        with pytest.raises(ScanExecutionError) as excinfo:
            executor.health()
        assert excinfo.value.category == "contract"

    def test_preflight_fast_fail_skips_execute(self) -> None:
        """preflight 失败快速失败：execute 端点不被调用。"""
        calls: list[str] = []

        def handler(request: httpx.Request) -> Any:
            calls.append(request.url.path)
            if request.url.path == "/rpc/health":
                return httpx.Response(503, text="starting")
            return httpx.Response(200, json=JobResult(scan_run_id="sr_r").model_dump())

        executor = RemoteScanExecutor(
            "http://worker", token="t", client_factory=self._mock_factory(handler)
        )
        with pytest.raises(ScanExecutionError) as excinfo:
            executor.execute(JobCommand(project="p", path="orders.csv"), preflight=True)
        assert excinfo.value.category == "http"
        assert calls == ["/rpc/health"]

    def test_preflight_disabled_by_default(self) -> None:
        """默认不探测：health 端点不存在时 execute 照常成功。"""
        calls: list[str] = []

        def handler(request: httpx.Request) -> Any:
            calls.append(request.url.path)
            if request.url.path == "/rpc/health":
                return httpx.Response(404)
            return httpx.Response(200, json=JobResult(scan_run_id="sr_r").model_dump())

        executor = RemoteScanExecutor(
            "http://worker", token="t", client_factory=self._mock_factory(handler)
        )
        result = executor.execute(JobCommand(project="p", path="orders.csv"))
        assert result.scan_run_id == "sr_r"
        assert calls == ["/rpc/execute"]

    def test_preflight_ok_then_execute(self) -> None:
        calls: list[str] = []

        def handler(request: httpx.Request) -> Any:
            calls.append(request.url.path)
            if request.url.path == "/rpc/health":
                return httpx.Response(200, json={"service": "datasentry-worker"})
            return httpx.Response(200, json=JobResult(scan_run_id="sr_r").model_dump())

        executor = RemoteScanExecutor(
            "http://worker", token="t", client_factory=self._mock_factory(handler)
        )
        result = executor.execute(JobCommand(project="p", path="orders.csv"), preflight=True)
        assert result.scan_run_id == "sr_r"
        assert calls == ["/rpc/health", "/rpc/execute"]
