"""V21（Step 111/112/113，ADR-111/112/113）：worker 远程执行器 CLI 测试。

拓扑：CLI `job trigger --remote-url`（或 `ping`）→ 真 HTTP → uvicorn
后台线程的 worker app（Step 92 模式）→ 远端执行扫描 / 健康探测。
"""

from __future__ import annotations

import threading
import time
from pathlib import Path

import pytest
from fastapi import FastAPI
from uvicorn import Config, Server

from datasentry.api import create_app as create_worker_app
from datasentry.cli import main
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


def _csv(tmp_path: Path) -> Path:
    p = tmp_path / "orders.csv"
    p.write_text("id,amount\n1,10\n1,1000\n2,-5\n,500\n")
    return p


def _create_job(tmp_path: Path, csv: Path, *, export_report: bool = False) -> str:
    args = [
        "--project",
        str(tmp_path),
        "job",
        "create",
        "remote job",
        str(csv),
        "--cron",
        "0 0 1 1 *",
    ]
    if export_report:
        args.append("--export-report")
    assert main(args) == 0
    store = SchedulerStore(project_db_path(tmp_path))
    jobs = store.list_jobs()
    assert jobs, "no jobs in store"
    return jobs[0].job_id


class TestJobTriggerRemoteV21:
    """Step 111（ADR-111）：job trigger 远程执行器选项。"""

    def test_trigger_local_without_remote_args(self, tmp_path: Path) -> None:
        """无 --remote-url → 本地执行，行为与 V20 及更早一致。"""
        csv = _csv(tmp_path)
        job_id = _create_job(tmp_path, csv)
        code = main(["--project", str(tmp_path), "job", "trigger", job_id])
        assert code == 0
        store = SchedulerStore(project_db_path(tmp_path))
        run = store.get_run(store.list_runs(job_id)[0].run_id)
        assert run is not None
        assert run.status.value == "completed"

    def test_trigger_remote_without_token_config_error(self, tmp_path: Path) -> None:
        csv = _csv(tmp_path)
        job_id = _create_job(tmp_path, csv)
        code = main(
            [
                "--project",
                str(tmp_path),
                "job",
                "trigger",
                job_id,
                "--remote-url",
                "http://127.0.0.1:1",
            ]
        )
        assert code == 2

    def test_trigger_remote_success(self, tmp_path: Path) -> None:
        csv = _csv(tmp_path)
        job_id = _create_job(tmp_path, csv)
        worker_app = create_worker_app(project=tmp_path, worker_token="s3cret")
        base_url, _server, _thread = _serve(worker_app)
        code = main(
            [
                "--project",
                str(tmp_path),
                "--format",
                "json",
                "job",
                "trigger",
                job_id,
                "--remote-url",
                base_url,
                "--remote-token",
                "s3cret",
            ]
        )
        assert code == 0
        store = SchedulerStore(project_db_path(tmp_path))
        run = store.get_run(store.list_runs(job_id)[0].run_id)
        assert run is not None
        assert run.status.value == "completed"
        assert run.scan_run_id is not None

    def test_trigger_remote_wrong_token_error(self, tmp_path: Path) -> None:
        """错误 token：执行失败 → run 落 failed（任务级错误在 Scheduler，CLI 不炸）。"""
        csv = _csv(tmp_path)
        job_id = _create_job(tmp_path, csv)
        worker_app = create_worker_app(project=tmp_path, worker_token="s3cret")
        base_url, _server, _thread = _serve(worker_app)
        code = main(
            [
                "--project",
                str(tmp_path),
                "job",
                "trigger",
                job_id,
                "--remote-url",
                base_url,
                "--remote-token",
                "wrong",
            ]
        )
        assert code == 0
        store = SchedulerStore(project_db_path(tmp_path))
        run = store.get_run(store.list_runs(job_id)[0].run_id)
        assert run is not None
        assert run.status.value == "failed"

    def test_trigger_remote_preflight_fast_fail(self, tmp_path: Path) -> None:
        """worker 未启用 token：health 恒 200（信息面）→ execute 503 → run failed
        （快速失败，无 120s 总超时等待）。"""
        csv = _csv(tmp_path)
        job_id = _create_job(tmp_path, csv)
        worker_app = create_worker_app(project=tmp_path)
        base_url, _server, _thread = _serve(worker_app)
        code = main(
            [
                "--project",
                str(tmp_path),
                "job",
                "trigger",
                job_id,
                "--remote-url",
                base_url,
                "--remote-token",
                "s3cret",
                "--remote-preflight",
            ]
        )
        assert code == 0
        store = SchedulerStore(project_db_path(tmp_path))
        run = store.get_run(store.list_runs(job_id)[0].run_id)
        assert run is not None
        assert run.status.value == "failed"

    def test_trigger_remote_report_pullback(self, tmp_path: Path) -> None:
        """--export-report 任务远程执行后报告回传本工作区 .datasentry/reports。"""
        csv = _csv(tmp_path)
        job_id = _create_job(tmp_path, csv, export_report=True)
        worker_app = create_worker_app(project=tmp_path, worker_token="s3cret")
        base_url, _server, _thread = _serve(worker_app)
        code = main(
            [
                "--project",
                str(tmp_path),
                "job",
                "trigger",
                job_id,
                "--remote-url",
                base_url,
                "--remote-token",
                "s3cret",
            ]
        )
        assert code == 0
        store = SchedulerStore(project_db_path(tmp_path))
        run = store.get_run(store.list_runs(job_id)[0].run_id)
        assert run is not None
        assert run.status.value == "completed"
        reports = list((tmp_path / ".datasentry" / "reports").glob("*.html"))
        assert reports, "report not pulled back to scheduler workspace"


class TestPingV21:
    """Step 112（ADR-112）：ping 远端 worker 健康探测。"""

    @staticmethod
    def _ping_json(
        capsys: pytest.CaptureFixture[str], tmp_path: Path, *, token: bool
    ) -> dict[str, object]:
        worker_app = create_worker_app(project=tmp_path, worker_token="s3cret" if token else None)
        base_url, _server, _thread = _serve(worker_app)
        code = main(["--format", "json", "ping", base_url])
        assert code == 0
        out = capsys.readouterr().out
        import json

        body = json.loads(out)
        assert body["ok"] is True
        return dict(body["data"])

    def test_ping_ok_worker_enabled_flag(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        body = self._ping_json(capsys, tmp_path, token=True)
        assert body["ok"] is True
        assert body["service"] == "datasentry-worker"
        assert body["version"]
        assert body["worker"] is True
        assert body["url"].startswith("http://127.0.0.1:")

    def test_ping_worker_disabled_flag(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        body = self._ping_json(capsys, tmp_path, token=False)
        assert body["ok"] is True
        assert body["worker"] is False

    def test_ping_unreachable_error(self, capsys: pytest.CaptureFixture[str]) -> None:
        code = main(["--format", "json", "ping", "http://127.0.0.1:1", "--timeout", "2"])
        assert code == 3
        out = capsys.readouterr().out
        import json

        body = json.loads(out)
        assert body["ok"] is True  # envelope 恒 ok；错误在 data.error
        assert "error" in body["data"]
