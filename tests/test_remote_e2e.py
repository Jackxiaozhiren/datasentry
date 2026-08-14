"""Step 92（ADR-092）：远程执行端到端——调度端 Scheduler 委托远端 worker。

拓扑：调度端 SchedulerStore（jobs/runs 库）+ RemoteScanExecutor
（真 HTTP → uvicorn 后台线程的 worker app）→ job trigger → run
completed，远端真实执行扫描（scan history 落库）+ webhook 照常。
"""

from __future__ import annotations

import threading
import time
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient
from uvicorn import Config, Server

from datasentry.api import create_app as create_worker_app
from datasentry.scheduler.core import Scheduler
from datasentry.scheduler.models import ScheduledJob, utcnow
from datasentry.scheduler.remote import RemoteScanExecutor
from datasentry.scheduler.store import SchedulerStore
from datasentry_core.storage.paths import project_db_path


def _serve(app: FastAPI) -> tuple[str, Server, threading.Thread]:
    config = Config(app, host="127.0.0.1", port=0, log_level="warning")
    server = Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    for _ in range(200):
        if server.started:
            break
        time.sleep(0.01)
    assert server.started, "uvicorn failed to start"
    port = server.servers[0].sockets[0].getsockname()[1]
    return f"http://127.0.0.1:{port}", server, thread


def test_remote_job_trigger_end_to_end(tmp_path: Path) -> None:
    csv = tmp_path / "orders.csv"
    csv.write_text("id,amount\n1,10\n1,1000\n2,-5\n,500\n")

    worker_app = create_worker_app(project=tmp_path, worker_token="s3cret")
    base_url, server, thread = _serve(worker_app)
    try:
        store = SchedulerStore(project_db_path(tmp_path))
        now = utcnow()
        store.create_job(
            ScheduledJob(
                job_id="job_remote",
                name="remote scan",
                project=str(tmp_path),
                command=__import__(
                    "datasentry.scheduler.models", fromlist=["JobCommand"]
                ).JobCommand(project=str(tmp_path), path=str(csv), dataset_id="orders"),
                cron="0 0 1 1 *",
                next_run_at=now,
                created_at=now,
                updated_at=now,
            )
        )
        scheduler = Scheduler(store=store, executor=RemoteScanExecutor(base_url, token="s3cret"))
        run_id = scheduler.trigger("job_remote")
        assert run_id is not None
        run = store.get_run(run_id)
        assert run is not None
        assert run.status.value == "completed"
        assert run.scan_run_id is not None
        summary = run.summary or ""
        assert "total_issues" in summary
        assert '"total_issues":5' in summary.replace(" ", "")
    finally:
        server.should_exit = True
        thread.join(timeout=10)


def test_remote_job_trigger_worker_error_marks_failed(tmp_path: Path) -> None:
    store = SchedulerStore(project_db_path(tmp_path))
    now = utcnow()
    store.create_job(
        ScheduledJob(
            job_id="job_bad",
            name="remote scan",
            project=str(tmp_path),
            command=__import__("datasentry.scheduler.models", fromlist=["JobCommand"]).JobCommand(
                project=str(tmp_path), path=str(tmp_path / "nope.csv")
            ),
            cron="0 0 1 1 *",
            next_run_at=now,
            created_at=now,
            updated_at=now,
        )
    )
    worker_app = create_worker_app(project=tmp_path, worker_token="s3cret")
    base_url, server, thread = _serve(worker_app)
    try:
        scheduler = Scheduler(store=store, executor=RemoteScanExecutor(base_url, token="s3cret"))
        run_id = scheduler.trigger("job_bad")
        assert run_id is not None
        run = store.get_run(run_id)
        assert run is not None
        assert run.status.value == "failed"
        assert "HTTP 500" in (run.error or "")
    finally:
        server.should_exit = True
        thread.join(timeout=10)


def test_remote_worker_matches_api_server_semantics(tmp_path: Path) -> None:
    """同一 worker app：HTTP API 服务（SchedulerWorker 自动调度）与
    /rpc/execute 远端执行并存不冲突（共享 client，无第二套逻辑）。"""
    csv = tmp_path / "orders.csv"
    csv.write_text("id,amount\n1,10\n1,1000\n2,-5\n,500\n")
    app = create_worker_app(project=tmp_path, worker_token="s3cret")
    with TestClient(app) as client:
        job_id = client.post(
            "/jobs", json={"name": "a", "path": str(csv), "cron": "0 0 1 1 *"}
        ).json()["job_id"]
        resp = client.post(f"/jobs/{job_id}/trigger")
        assert resp.status_code == 202
        run_id = resp.json()["run_id"]
        resp = client.post(
            "/rpc/execute",
            headers={"X-Datasentry-Token": "s3cret"},
            json={"project": str(tmp_path), "path": str(csv)},
        )
        assert resp.status_code == 200
        assert resp.json()["scan_run_id"] != run_id
