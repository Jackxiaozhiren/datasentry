"""Step 94（ADR-094）：调度端 worker 配置面测试。

- parse_workers 单元：合法/非法条目处理
- _build_scheduler 集成：env 配置 → WorkerPoolExecutor；
  未配置 → LocalScanExecutor
- 端到端：api 服务配 2 worker（其一始终 500）→ job trigger →
  run completed（失败转移成功，真扫描执行）
"""

from __future__ import annotations

import threading
import time
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from uvicorn import Config, Server

from datasentry.api import _build_scheduler, create_app, parse_workers
from datasentry.scheduler.core import LocalScanExecutor
from datasentry.scheduler.pool import WorkerPoolExecutor


class TestParseWorkers:
    def test_parses_multiple_entries(self) -> None:
        assert parse_workers("http://a:8000:t1;http://b:9000:t2") == [
            ("http://a:8000", "t1"),
            ("http://b:9000", "t2"),
        ]

    def test_empty_string_returns_empty(self) -> None:
        assert parse_workers("") == []

    def test_skips_malformed_entries(self) -> None:
        assert parse_workers("http://a:8000:t1;no-sep;:tok;url:") == [
            ("http://a:8000", "t1"),
        ]

    def test_strips_whitespace(self) -> None:
        assert parse_workers(" http://a:8000 : t1 ;  ") == [("http://a:8000", "t1")]


class TestBuildScheduler:
    def test_defaults_to_local_executor(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("DATASENTRY_WORKERS", raising=False)
        from datasentry import client as sdk

        scheduler = _build_scheduler(sdk.DataSentry(tmp_path))
        assert isinstance(scheduler._executor, LocalScanExecutor)

    def test_env_workers_uses_pool(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("DATASENTRY_WORKERS", "http://a:8000:t1;http://b:9000:t2")
        from datasentry import client as sdk

        scheduler = _build_scheduler(sdk.DataSentry(tmp_path))
        pool = scheduler._executor
        assert isinstance(pool, WorkerPoolExecutor)
        assert [w.url for w in pool._workers] == ["http://a:8000", "http://b:9000"]

    def test_env_invalid_entries_falls_back_to_local(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("DATASENTRY_WORKERS", "bad-entry")
        from datasentry import client as sdk

        scheduler = _build_scheduler(sdk.DataSentry(tmp_path))
        assert isinstance(scheduler._executor, LocalScanExecutor)


def _fail_app() -> FastAPI:
    app = FastAPI()

    @app.post("/rpc/execute")
    def execute() -> dict[str, Any]:
        raise HTTPException(status_code=500, detail="boom")

    return app


def _ok_worker_server(project: Path, token: str) -> tuple[Server, threading.Thread, str]:
    config = Config(
        create_app(project=project, worker_token=token),
        host="127.0.0.1",
        port=0,
        log_level="warning",
    )
    server = Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    for _ in range(200):
        if server.started:
            break
        time.sleep(0.01)
    assert server.started, "uvicorn failed to start"
    port = server.servers[0].sockets[0].getsockname()[1]
    return server, thread, f"http://127.0.0.1:{port}"


class TestWorkerPoolE2E:
    def test_job_failovers_to_healthy_worker(self, tmp_path: Path) -> None:
        worker_dir = tmp_path / "worker"
        worker_dir.mkdir()
        (worker_dir / "orders.csv").write_text("id,amount\n1,10\n1,1000\n2,-5\n,500\n")

        fail_server = Server(Config(_fail_app(), host="127.0.0.1", port=0, log_level="warning"))
        fail_thread = threading.Thread(target=fail_server.run, daemon=True)
        fail_thread.start()
        for _ in range(200):
            if fail_server.started:
                break
            time.sleep(0.01)
        assert fail_server.started
        fail_port = fail_server.servers[0].sockets[0].getsockname()[1]
        fail_url = f"http://127.0.0.1:{fail_port}"

        ok_server, ok_thread, ok_url = _ok_worker_server(worker_dir, "s3cret")

        env_value = f"{fail_url}:bad;{ok_url}:s3cret"
        scheduler_dir = tmp_path / "scheduler"
        scheduler_dir.mkdir()
        (scheduler_dir / "orders.csv").write_text("id,amount\n1,10\n")
        try:
            with pytest.MonkeyPatch.context() as mp:
                mp.setenv("DATASENTRY_WORKERS", env_value)
                with TestClient(create_app(project=scheduler_dir)) as client:
                    created = client.post(
                        "/jobs",
                        json={
                            "name": "e2e",
                            "cron": "0 0 1 1 *",
                            "path": "orders.csv",
                            "project": "p",
                        },
                    )
                    assert created.status_code == 201, created.text
                    job_id = created.json()["job_id"]
                    resp = client.post(f"/jobs/{job_id}/trigger")
                    assert resp.status_code == 202, resp.text
                    run_id = resp.json()["run_id"]
                    assert run_id
                    for _ in range(100):
                        runs = client.get(f"/jobs/{job_id}/runs").json()["runs"]
                        status = runs[0]["status"] if runs else "pending"
                        if status in {"completed", "failed"}:
                            break
                        time.sleep(0.1)
                    assert status == "completed"
                    run = client.get(f"/jobs/{job_id}/runs").json()["runs"][0]
                    assert '"total_issues":2' in run["summary"].replace(" ", "")
        finally:
            fail_server.should_exit = True
            fail_thread.join(timeout=10)
            ok_server.should_exit = True
            ok_thread.join(timeout=10)
