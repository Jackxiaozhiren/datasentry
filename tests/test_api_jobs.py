"""Step 51（V2-D 云侧调度）API 测试：/jobs 端点端到端（ADR-051）。

覆盖验收标准：注册任务（非法 cron 422）、列表/状态、手动触发（409 互斥）、
更新/删除、worker 生命周期（startup 起、shutdown 停、重启恢复）。
"""

from __future__ import annotations

from datetime import timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from datasentry.api import create_app
from datasentry.scheduler.models import RunStatus, utcnow


def _sample_csv(tmp_path: Path) -> Path:
    p = tmp_path / "orders.csv"
    p.write_text(
        "id,amount\n1,10\n1,1000\n2,-5\n,500\n",
        encoding="utf-8",
    )
    return p


@pytest.fixture
def client(tmp_path: Path) -> TestClient:
    return TestClient(create_app(project=tmp_path))


class TestJobsApi:
    def test_create_job(self, client: TestClient, tmp_path: Path) -> None:
        csv = _sample_csv(tmp_path)
        resp = client.post(
            "/jobs",
            json={
                "name": "nightly orders",
                "path": str(csv),
                "cron": "0 9 * * *",
            },
        )
        assert resp.status_code == 201
        body = resp.json()
        assert body["name"] == "nightly orders"
        assert body["cron"] == "0 9 * * *"
        assert body["status"] == "idle"
        assert body["project"] == str(tmp_path.resolve())
        assert body["next_run_at"]
        assert body["command"]["path"] == str(csv)

    def test_create_job_export_report_flag(self, client: TestClient, tmp_path: Path) -> None:
        csv = _sample_csv(tmp_path)
        resp = client.post(
            "/jobs",
            json={
                "name": "nightly reports",
                "path": str(csv),
                "cron": "0 9 * * *",
                "export_report": True,
            },
        )
        assert resp.status_code == 201
        body = resp.json()
        assert body["export_report"] is True
        assert body["command"]["export_report"] is True
        default = client.post(
            "/jobs",
            json={"name": "plain", "path": str(csv), "cron": "0 9 * * *"},
        ).json()
        assert default["export_report"] is False

    def test_create_job_relative_path_resolves(self, client: TestClient, tmp_path: Path) -> None:
        _sample_csv(tmp_path)
        resp = client.post(
            "/jobs",
            json={"name": "rel", "path": "orders.csv", "cron": "* * * * *"},
        )
        assert resp.status_code == 201
        assert resp.json()["command"]["path"] == str(tmp_path / "orders.csv")

    def test_create_job_invalid_cron_rejected(self, client: TestClient, tmp_path: Path) -> None:
        csv = _sample_csv(tmp_path)
        resp = client.post(
            "/jobs",
            json={"name": "bad", "path": str(csv), "cron": "61 * * * *"},
        )
        assert resp.status_code == 422
        assert "invalid cron" in resp.json()["detail"]

    def test_list_jobs(self, client: TestClient, tmp_path: Path) -> None:
        csv = _sample_csv(tmp_path)
        client.post("/jobs", json={"name": "a", "path": str(csv), "cron": "* * * * *"})
        client.post("/jobs", json={"name": "b", "path": str(csv), "cron": "*/10 * * * *"})
        resp = client.get("/jobs")
        assert resp.status_code == 200
        assert [j["name"] for j in resp.json()] == ["a", "b"]

    def test_get_job_with_runs(self, client: TestClient, tmp_path: Path) -> None:
        csv = _sample_csv(tmp_path)
        created = client.post(
            "/jobs", json={"name": "a", "path": str(csv), "cron": "* * * * *"}
        ).json()
        job_id = created["job_id"]
        triggered = client.post(f"/jobs/{job_id}/trigger")
        assert triggered.status_code == 202
        detail = client.get(f"/jobs/{job_id}")
        assert detail.status_code == 200
        body = detail.json()
        assert body["job"]["status"] == "idle"
        runs = body["runs"]
        assert len(runs) == 1
        assert runs[0]["status"] == "completed"
        assert runs[0]["scan_run_id"].startswith("scan_")
        assert body["job"]["last_run_at"]

    def test_get_job_not_found(self, client: TestClient) -> None:
        assert client.get("/jobs/nope").status_code == 404

    def test_trigger_executes_scan(self, client: TestClient, tmp_path: Path) -> None:
        """手动触发：真实执行扫描（含脏数据 → 有 issues 的结果摘要）。"""
        csv = _sample_csv(tmp_path)
        job_id = client.post(
            "/jobs", json={"name": "a", "path": str(csv), "cron": "* * * * *"}
        ).json()["job_id"]
        resp = client.post(f"/jobs/{job_id}/trigger")
        assert resp.status_code == 202
        assert resp.json()["run_id"].startswith("run_")
        detail = client.get(f"/jobs/{job_id}").json()
        summary = detail["runs"][0]["summary"]
        import json as _json

        assert _json.loads(summary)["total_issues"] > 0

    def test_trigger_unknown_job_404(self, client: TestClient) -> None:
        assert client.post("/jobs/nope/trigger").status_code == 404

    def test_update_job_cron_and_disable(self, client: TestClient, tmp_path: Path) -> None:
        csv = _sample_csv(tmp_path)
        job_id = client.post(
            "/jobs", json={"name": "a", "path": str(csv), "cron": "* * * * *"}
        ).json()["job_id"]
        resp = client.patch(
            f"/jobs/{job_id}",
            json={"cron": "0 12 * * *", "enabled": False},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["cron"] == "0 12 * * *"
        assert body["enabled"] is False
        assert "12:00" in body["next_run_at"]

    def test_update_job_invalid_cron_422(self, client: TestClient, tmp_path: Path) -> None:
        csv = _sample_csv(tmp_path)
        job_id = client.post(
            "/jobs", json={"name": "a", "path": str(csv), "cron": "* * * * *"}
        ).json()["job_id"]
        resp = client.patch(f"/jobs/{job_id}", json={"cron": "bad"})
        assert resp.status_code == 422

    def test_update_job_unknown_404(self, client: TestClient) -> None:
        assert client.patch("/jobs/nope", json={"enabled": True}).status_code == 404

    def test_delete_job(self, client: TestClient, tmp_path: Path) -> None:
        csv = _sample_csv(tmp_path)
        job_id = client.post(
            "/jobs", json={"name": "a", "path": str(csv), "cron": "* * * * *"}
        ).json()["job_id"]
        assert client.delete(f"/jobs/{job_id}").status_code == 204
        assert client.get(f"/jobs/{job_id}").status_code == 404
        assert client.delete(f"/jobs/{job_id}").status_code == 404


class TestJobLifecycle:
    def test_restart_recovers_running_job(self, tmp_path: Path) -> None:
        """重启恢复：running 任务在服务重启后置回 idle 并可再次调度（持久化）。"""
        csv = _sample_csv(tmp_path)
        app1 = create_app(project=tmp_path)
        with TestClient(app1) as client1:
            job_id = client1.post(
                "/jobs", json={"name": "a", "path": str(csv), "cron": "* * * * *"}
            ).json()["job_id"]
            client1.post(f"/jobs/{job_id}/trigger")
            assert client1.get(f"/jobs/{job_id}").json()["runs"][0]["status"] == "completed"

        # 模拟崩溃：进程抢占后未落结果即退出（job + run 停在 running）
        from datasentry.scheduler.store import SchedulerStore
        from datasentry_core.storage.paths import project_db_path

        store = SchedulerStore(project_db_path(tmp_path))
        crashed_run_id = store.claim_job(job_id, utcnow())
        assert crashed_run_id is not None
        run = store.get_run(crashed_run_id)
        assert run is not None and run.status == RunStatus.RUNNING

        app2 = create_app(project=tmp_path)
        with TestClient(app2) as client2:
            job = client2.get(f"/jobs/{job_id}").json()
            assert job["job"]["status"] == "idle"
            crashed = store.get_run(crashed_run_id)
            assert crashed is not None
            assert crashed.status == RunStatus.FAILED
            assert "interrupted" in (crashed.error or "")
            # 恢复后可再次手动触发（按 run_id 断言，避免同秒 DESC 排序竞态）
            resp = client2.post(f"/jobs/{job_id}/trigger")
            assert resp.status_code == 202
            new_run = store.get_run(resp.json()["run_id"])
            assert new_run is not None and new_run.status == RunStatus.COMPLETED


class TestJobsV13:
    """Step 87（ADR-087）：runs 历史端点 + test-webhook 协作链路验证。"""

    def test_runs_endpoint_with_limit(self, client: TestClient, tmp_path: Path) -> None:
        csv = _sample_csv(tmp_path)
        job_id = client.post(
            "/jobs", json={"name": "a", "path": str(csv), "cron": "* * * * *"}
        ).json()["job_id"]
        client.post(f"/jobs/{job_id}/trigger")
        client.post(f"/jobs/{job_id}/trigger")
        resp = client.get(f"/jobs/{job_id}/runs")
        assert resp.status_code == 200
        body = resp.json()
        assert body["job_id"] == job_id
        assert body["count"] == 2
        assert [r["status"] for r in body["runs"]] == ["completed", "completed"]
        limited = client.get(f"/jobs/{job_id}/runs", params={"limit": 1})
        assert limited.json()["count"] == 1

    def test_runs_endpoint_unknown_job_404(self, client: TestClient) -> None:
        assert client.get("/jobs/nope/runs").status_code == 404

    def test_webhook_test_success(self, client: TestClient, tmp_path: Path) -> None:
        import json as _json
        import threading
        from http.server import BaseHTTPRequestHandler, HTTPServer

        received: list[dict[str, object]] = []

        class _Handler(BaseHTTPRequestHandler):
            def do_POST(self) -> None:
                length = int(self.headers.get("Content-Length", "0"))
                received.append(_json.loads(self.rfile.read(length)))
                self.send_response(200)
                self.end_headers()

            def log_message(self, *args: object) -> None:
                pass

        server = HTTPServer(("127.0.0.1", 0), _Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            port = server.server_address[1]
            csv = _sample_csv(tmp_path)
            job_id = client.post(
                "/jobs",
                json={
                    "name": "hook",
                    "path": str(csv),
                    "cron": "* * * * *",
                    "webhook_url": f"http://127.0.0.1:{port}/hook",
                },
            ).json()["job_id"]
            resp = client.post(f"/jobs/{job_id}/test-webhook")
            assert resp.status_code == 200
            body = resp.json()
            assert body["notified"] is True
            assert body["status_code"] == 200
            assert body["elapsed_ms"] >= 0
            assert len(received) == 1
            assert received[0]["event"] == "job.test"
            assert received[0]["job_id"] == job_id
            assert "payload" in received[0]
        finally:
            server.shutdown()
            thread.join()

    def test_webhook_test_remote_error(self, client: TestClient, tmp_path: Path) -> None:
        import threading
        from http.server import BaseHTTPRequestHandler, HTTPServer

        class _Handler(BaseHTTPRequestHandler):
            def do_POST(self) -> None:
                self.send_response(500)
                self.end_headers()

            def log_message(self, *args: object) -> None:
                pass

        server = HTTPServer(("127.0.0.1", 0), _Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            port = server.server_address[1]
            csv = _sample_csv(tmp_path)
            job_id = client.post(
                "/jobs",
                json={
                    "name": "hook",
                    "path": str(csv),
                    "cron": "* * * * *",
                    "webhook_url": f"http://127.0.0.1:{port}/hook",
                },
            ).json()["job_id"]
            resp = client.post(f"/jobs/{job_id}/test-webhook")
            assert resp.status_code == 200
            assert resp.json()["notified"] is False
            assert resp.json()["status_code"] == 500
        finally:
            server.shutdown()
            thread.join()

    def test_webhook_test_connection_failed_502(self, client: TestClient, tmp_path: Path) -> None:
        csv = _sample_csv(tmp_path)
        job_id = client.post(
            "/jobs",
            json={
                "name": "hook",
                "path": str(csv),
                "cron": "* * * * *",
                "webhook_url": "http://127.0.0.1:1/hook",
            },
        ).json()["job_id"]
        resp = client.post(f"/jobs/{job_id}/test-webhook")
        assert resp.status_code == 502
        assert "webhook delivery failed" in resp.json()["detail"]

    def test_webhook_test_no_url_422(self, client: TestClient, tmp_path: Path) -> None:
        csv = _sample_csv(tmp_path)
        job_id = client.post(
            "/jobs", json={"name": "plain", "path": str(csv), "cron": "* * * * *"}
        ).json()["job_id"]
        resp = client.post(f"/jobs/{job_id}/test-webhook")
        assert resp.status_code == 422
        assert "no webhook_url" in resp.json()["detail"]

    def test_webhook_test_unknown_job_404(self, client: TestClient) -> None:
        assert client.post("/jobs/nope/test-webhook").status_code == 404

    def test_worker_tick_runs_due_job_after_startup(self, tmp_path: Path) -> None:
        """startup 起 worker：到期任务自动执行（无需手动触发）。"""
        csv = _sample_csv(tmp_path)
        from datasentry.scheduler.models import ScheduledJob, utcnow
        from datasentry.scheduler.store import SchedulerStore
        from datasentry_core.storage.paths import project_db_path

        store = SchedulerStore(project_db_path(tmp_path))
        now = utcnow()
        store.create_job(
            ScheduledJob(
                job_id="job_due",
                name="due now",
                project=str(tmp_path.resolve()),
                command=__import__(
                    "datasentry.scheduler.models", fromlist=["JobCommand"]
                ).JobCommand(project=str(tmp_path.resolve()), path=str(csv)),
                cron="* * * * *",
                next_run_at=now - timedelta(seconds=5),
                created_at=now,
                updated_at=now,
            )
        )
        with TestClient(create_app(project=tmp_path)) as client:
            # worker 每 1s tick；轮询等待自动执行完成（CI 上单次扫描可达 20s+）
            job: dict[str, object] = {"job": {"status": "idle"}, "runs": []}
            for _ in range(120):
                job = client.get("/jobs/job_due").json()
                runs = job["runs"]
                if runs and runs[0]["status"] == "completed":
                    break
                import time

                time.sleep(0.5)
            assert job["runs"][0]["status"] == "completed"


class TestJobsGate:
    def test_create_job_with_gate(self, client: TestClient, tmp_path: Path) -> None:
        csv = _sample_csv(tmp_path)
        resp = client.post(
            "/jobs",
            json={"name": "gated", "path": str(csv), "cron": "* * * * *", "gate_quality_min": 90.0},
        )
        assert resp.status_code == 201
        assert resp.json()["gate_quality_min"] == 90.0

    def test_create_job_gate_out_of_range_422(self, client: TestClient, tmp_path: Path) -> None:
        csv = _sample_csv(tmp_path)
        resp = client.post(
            "/jobs",
            json={"name": "bad", "path": str(csv), "cron": "* * * * *", "gate_quality_min": 150.0},
        )
        assert resp.status_code == 422

    def test_update_job_gate(self, client: TestClient, tmp_path: Path) -> None:
        csv = _sample_csv(tmp_path)
        job_id = client.post(
            "/jobs", json={"name": "a", "path": str(csv), "cron": "* * * * *"}
        ).json()["job_id"]
        resp = client.patch(f"/jobs/{job_id}", json={"gate_quality_min": 75.0})
        assert resp.status_code == 200
        assert resp.json()["gate_quality_min"] == 75.0

    def test_trigger_applies_gate_judgement(self, client: TestClient, tmp_path: Path) -> None:
        csv = _sample_csv(tmp_path)
        job_id = client.post(
            "/jobs",
            json={
                "name": "gated",
                "path": str(csv),
                "cron": "* * * * *",
                "gate_quality_min": 99.99,
            },
        ).json()["job_id"]
        assert client.post(f"/jobs/{job_id}/trigger").status_code == 202
        detail = client.get(f"/jobs/{job_id}").json()
        import json as _json

        summary = _json.loads(detail["runs"][0]["summary"])
        assert summary["gate"]["passed"] is False
        assert summary["gate"]["configured"] is True
        assert detail["job"]["status"] == "idle"


class TestChangeAwareApi:
    def test_trigger_twice_unchanged_file_second_skipped(
        self, client: TestClient, tmp_path: Path
    ) -> None:
        """Step 53：内容未变时二次 trigger 记 skipped run（不建 scan_run）。"""
        from datasentry.scheduler.store import SchedulerStore
        from datasentry_core.storage.paths import project_db_path

        csv = _sample_csv(tmp_path)
        job_id = client.post(
            "/jobs", json={"name": "inc", "path": str(csv), "cron": "* * * * *"}
        ).json()["job_id"]
        store = SchedulerStore(project_db_path(tmp_path))

        first = client.post(f"/jobs/{job_id}/trigger")
        assert first.status_code == 202
        first_run = store.get_run(first.json()["run_id"])
        assert first_run is not None
        assert first_run.skipped is False
        assert first_run.scan_run_id is not None

        second = client.post(f"/jobs/{job_id}/trigger")
        assert second.status_code == 202
        second_run = store.get_run(second.json()["run_id"])
        assert second_run is not None
        assert second_run.skipped is True
        assert second_run.scan_run_id is None
        assert second_run.file_hash is not None
        assert second_run.file_hash == first_run.file_hash

        job = client.get(f"/jobs/{job_id}").json()
        assert job["job"]["status"] == "idle"
        assert any(r["run_id"] == second_run.run_id and r["skipped"] for r in job["runs"])

    def test_trigger_after_content_change_runs_full(
        self, client: TestClient, tmp_path: Path
    ) -> None:
        from datasentry.scheduler.store import SchedulerStore
        from datasentry_core.storage.paths import project_db_path

        csv = _sample_csv(tmp_path)
        job_id = client.post(
            "/jobs", json={"name": "chg", "path": str(csv), "cron": "* * * * *"}
        ).json()["job_id"]
        store = SchedulerStore(project_db_path(tmp_path))
        first_run = store.get_run(client.post(f"/jobs/{job_id}/trigger").json()["run_id"])
        assert first_run is not None and first_run.file_hash is not None

        new = csv.with_name("orders2.csv")
        new.write_text("id,amount\n9,42\n", encoding="utf-8")
        job_id2 = client.post(
            "/jobs", json={"name": "chg2", "path": str(new), "cron": "* * * * *"}
        ).json()["job_id"]
        second_run = store.get_run(client.post(f"/jobs/{job_id2}/trigger").json()["run_id"])
        assert second_run is not None and second_run.file_hash is not None
        assert second_run.file_hash != first_run.file_hash


class TestSqliteScanApi:
    def test_post_scans_with_table_name(self, tmp_path: Path) -> None:
        import sqlite3

        from fastapi.testclient import TestClient

        from datasentry.api import create_app

        db = tmp_path / "orders.db"
        conn = sqlite3.connect(db)
        try:
            conn.execute("CREATE TABLE orders (id INTEGER, amount REAL)")
            conn.execute("INSERT INTO orders VALUES (1, 10.5), (1, NULL), (2, -3)")
            conn.commit()
        finally:
            conn.close()

        with TestClient(create_app(project=tmp_path)) as client:
            resp = client.post(
                "/scans",
                json={"path": str(db), "table_name": "orders", "dataset_id": "orders"},
            )
            assert resp.status_code == 201
            body = resp.json()
            assert body["run"]["dataset_id"] == "orders"
            assert len(body["issues"]) >= 1

    def test_post_scans_sqlite_without_table_name_422(self, tmp_path: Path) -> None:
        import sqlite3

        from fastapi.testclient import TestClient

        from datasentry.api import create_app

        db = tmp_path / "orders.db"
        conn = sqlite3.connect(db)
        try:
            conn.execute("CREATE TABLE orders (id INTEGER)")
            conn.commit()
        finally:
            conn.close()

        with TestClient(create_app(project=tmp_path)) as client:
            resp = client.post("/scans", json={"path": str(db)})
            assert resp.status_code == 404
            assert "table_name" in resp.json()["detail"]
