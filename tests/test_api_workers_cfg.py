"""Step 97（ADR-097）：API 层并行配置 DATASENTRY_MAX_WORKERS 测试。

- _parse_max_workers 单元：默认 1 / 非法回退 1 / >1 透传
- _build_scheduler 集成：env 未设同步 / env=3 异步池
- 端到端：env=3 真 HTTP 慢执行 → trigger 202 立即返回 → 轮询
  至 completed；lifespan 退出不残留线程
"""

from __future__ import annotations

import threading
import time
from pathlib import Path

from fastapi.testclient import TestClient

from datasentry.api import _build_scheduler, _parse_max_workers, create_app
from datasentry.scheduler.core import LocalScanExecutor, Scheduler


class TestParseMaxWorkers:
    def test_default_one(self) -> None:
        assert _parse_max_workers(None) == 1
        assert _parse_max_workers("") == 1

    def test_invalid_falls_back(self) -> None:
        assert _parse_max_workers("abc") == 1
        assert _parse_max_workers("0") == 1
        assert _parse_max_workers("-2") == 1

    def test_positive_passthrough(self) -> None:
        assert _parse_max_workers("3") == 3


class TestBuildSchedulerWorkers:
    def test_unset_env_sync_default(self, tmp_path: Path, monkeypatch) -> None:
        monkeypatch.delenv("DATASENTRY_MAX_WORKERS", raising=False)
        monkeypatch.delenv("DATASENTRY_WORKERS", raising=False)
        from datasentry import client as sdk

        scheduler = _build_scheduler(sdk.DataSentry(tmp_path))
        assert isinstance(scheduler._executor, LocalScanExecutor)
        assert scheduler._pool is None

    def test_env_three_creates_pool(self, tmp_path: Path, monkeypatch) -> None:
        monkeypatch.setenv("DATASENTRY_MAX_WORKERS", "3")
        monkeypatch.delenv("DATASENTRY_WORKERS", raising=False)
        from datasentry import client as sdk

        scheduler = _build_scheduler(sdk.DataSentry(tmp_path))
        assert isinstance(scheduler, Scheduler)
        assert scheduler._pool is not None
        assert scheduler._pool._max_workers == 3
        scheduler.shutdown(wait=True)

    def test_invalid_env_sync_default(self, tmp_path: Path, monkeypatch) -> None:
        monkeypatch.setenv("DATASENTRY_MAX_WORKERS", "nope")
        monkeypatch.delenv("DATASENTRY_WORKERS", raising=False)
        from datasentry import client as sdk

        scheduler = _build_scheduler(sdk.DataSentry(tmp_path))
        assert scheduler._pool is None


def _slow_csv(tmp_path: Path) -> Path:
    p = tmp_path / "orders.csv"
    p.write_text("id,amount\n1,10\n1,1000\n2,-5\n,500\n", encoding="utf-8")
    return p


class TestParallelApiE2E:
    def test_trigger_async_with_workers(self, tmp_path: Path, monkeypatch) -> None:
        monkeypatch.setenv("DATASENTRY_MAX_WORKERS", "2")
        monkeypatch.delenv("DATASENTRY_WORKERS", raising=False)
        csv = _slow_csv(tmp_path)
        from datasentry.scheduler.models import utcnow

        with TestClient(create_app(project=tmp_path)) as client:
            created = client.post(
                "/jobs",
                json={"name": "e2e", "cron": "0 0 1 1 *", "path": str(csv)},
            )
            assert created.status_code == 201
            job_id = created.json()["job_id"]
            scheduler: Scheduler = client.app.state.scheduler
            assert scheduler._pool is not None
            resp = client.post(f"/jobs/{job_id}/trigger")
            assert resp.status_code == 202
            resp.json()["run_id"]
            deadline = time.monotonic() + 30
            status = "running"
            while time.monotonic() < deadline:
                runs = client.get(f"/jobs/{job_id}/runs").json()["runs"]
                if runs:
                    status = runs[0]["status"]
                    if status in {"completed", "failed"}:
                        break
                time.sleep(0.2)
            assert status == "completed"
            assert utcnow().isoformat()

    def test_unset_env_trigger_sync(self, tmp_path: Path, monkeypatch) -> None:
        monkeypatch.delenv("DATASENTRY_MAX_WORKERS", raising=False)
        monkeypatch.delenv("DATASENTRY_WORKERS", raising=False)
        csv = _slow_csv(tmp_path)
        with TestClient(create_app(project=tmp_path)) as client:
            created = client.post(
                "/jobs",
                json={"name": "e2e", "cron": "0 0 1 1 *", "path": str(csv)},
            )
            assert created.status_code == 201
            job_id = created.json()["job_id"]
            scheduler: Scheduler = client.app.state.scheduler
            assert scheduler._pool is None
            resp = client.post(f"/jobs/{job_id}/trigger")
            assert resp.status_code == 202
            run_id = resp.json()["run_id"]
            runs = client.get(f"/jobs/{job_id}/runs").json()["runs"]
            assert runs[0]["run_id"] == run_id
            assert runs[0]["status"] == "completed"


def test_lifespan_exit_no_thread_leak(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("DATASENTRY_MAX_WORKERS", "2")
    monkeypatch.delenv("DATASENTRY_WORKERS", raising=False)
    csv = _slow_csv(tmp_path)
    with TestClient(create_app(project=tmp_path)) as client:
        created = client.post(
            "/jobs",
            json={"name": "e2e", "cron": "0 0 1 1 *", "path": str(csv)},
        )
        job_id = created.json()["job_id"]
        client.post(f"/jobs/{job_id}/trigger")
    remaining = [t for t in threading.enumerate() if "scheduler" in t.name]
    assert remaining == []
