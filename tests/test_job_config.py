"""Step 79（ADR-079）调度任务 ScanConfig 透传测试。

覆盖验收标准：创建任务带 config（sampling/detectors/tags）落库回显；
无 config 任务行为不变（command.config 为 None）；trigger 执行后
scan_run.config 与 JobCommand.config 一致（SamplingInfo 生效）；
持久化重启后 config 保留；指纹跳过语义不含 config（文件未变 +
config 不同仍跳过，ADR-079 记录边界）。
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from datasentry import DataSentry
from datasentry.api import create_app


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


def _create_with_config(client: TestClient, tmp_path: Path, config: dict) -> str:
    csv = _sample_csv(tmp_path)
    body = {"name": "cfg", "path": str(csv), "cron": "0 9 * * *", "config": config}
    resp = client.post("/jobs", json=body)
    assert resp.status_code == 201
    return resp.json()["job_id"]


class TestJobConfig:
    def test_create_job_with_sampling_config(self, client: TestClient, tmp_path: Path) -> None:
        job_id = _create_with_config(
            client,
            tmp_path,
            {
                "sampling": {
                    "method": "reservoir",
                    "sample_size": 100,
                    "seed": 7,
                },
                "detectors": ["missing_value"],
                "scan_tags": {"env": "prod"},
            },
        )
        body = client.get(f"/jobs/{job_id}").json()
        command = body["job"]["command"]
        assert command["config"]["sampling"]["method"] == "reservoir"
        assert command["config"]["sampling"]["sample_size"] == 100
        assert command["config"]["sampling"]["seed"] == 7
        assert command["config"]["detectors"] == ["missing_value"]
        assert command["config"]["scan_tags"] == {"env": "prod"}

    def test_create_job_without_config_keeps_none(self, client: TestClient, tmp_path: Path) -> None:
        csv = _sample_csv(tmp_path)
        resp = client.post("/jobs", json={"name": "plain", "path": str(csv), "cron": "* * * * *"})
        assert resp.status_code == 201
        assert resp.json()["command"]["config"] is None

    def test_create_job_invalid_config_422(self, client: TestClient, tmp_path: Path) -> None:
        csv = _sample_csv(tmp_path)
        resp = client.post(
            "/jobs",
            json={
                "name": "bad",
                "path": str(csv),
                "cron": "* * * * *",
                "config": {"sampling": {"method": "not-a-method"}},
            },
        )
        assert resp.status_code == 422

    def test_config_survives_restart(self, tmp_path: Path) -> None:
        csv = _sample_csv(tmp_path)
        with TestClient(create_app(project=tmp_path)) as client:
            job_id = client.post(
                "/jobs",
                json={
                    "name": "persist",
                    "path": str(csv),
                    "cron": "* * * * *",
                    "config": {"sampling": {"method": "none"}},
                },
            ).json()["job_id"]
        with TestClient(create_app(project=tmp_path)) as client:
            command = client.get(f"/jobs/{job_id}").json()["job"]["command"]
            assert command["config"]["sampling"]["method"] == "none"

    def test_trigger_applies_config_to_scan_run(self, client: TestClient, tmp_path: Path) -> None:
        """trigger 执行：scan_run.config 与 JobCommand.config 一致（配置真正生效）。"""
        job_id = _create_with_config(
            client,
            tmp_path,
            {"sampling": {"method": "reservoir", "sample_size": 3, "seed": 9}},
        )
        resp = client.post(f"/jobs/{job_id}/trigger")
        assert resp.status_code == 202
        detail = client.get(f"/jobs/{job_id}").json()
        scan_run_id = detail["runs"][0]["scan_run_id"]
        assert scan_run_id is not None

        ds = DataSentry(project=str(tmp_path.resolve()))
        try:
            run = ds.get_scan(scan_run_id)
            assert run is not None
            assert run.config.sampling.method == "reservoir"
            assert run.config.sampling.sample_size == 3
            assert run.config.sampling.seed == 9
        finally:
            ds.close()

    def test_trigger_without_config_uses_defaults(self, client: TestClient, tmp_path: Path) -> None:
        """无 config 任务：scan_run.config 为默认配置（旧行为不变）。"""
        csv = _sample_csv(tmp_path)
        job_id = client.post(
            "/jobs", json={"name": "plain", "path": str(csv), "cron": "* * * * *"}
        ).json()["job_id"]
        assert client.post(f"/jobs/{job_id}/trigger").status_code == 202
        detail = client.get(f"/jobs/{job_id}").json()
        scan_run_id = detail["runs"][0]["scan_run_id"]

        ds = DataSentry(project=str(tmp_path.resolve()))
        try:
            run = ds.get_scan(scan_run_id)
            assert run is not None
            assert run.config.sampling.method == "random"
        finally:
            ds.close()

    def test_fingerprint_skip_not_config_aware(self, client: TestClient, tmp_path: Path) -> None:
        """ADR-079 边界：文件未变 + config 不同 → 仍跳过（config 不参与跳过判定）。"""
        from datasentry.scheduler.store import SchedulerStore
        from datasentry_core.storage.paths import project_db_path

        csv = _sample_csv(tmp_path)
        store = SchedulerStore(project_db_path(tmp_path))
        job_id = client.post(
            "/jobs", json={"name": "a", "path": str(csv), "cron": "* * * * *"}
        ).json()["job_id"]
        first = client.post(f"/jobs/{job_id}/trigger")
        assert first.status_code == 202
        first_run = store.get_run(first.json()["run_id"])
        assert first_run is not None and first_run.skipped is False

        client.patch(f"/jobs/{job_id}", json={"enabled": True})
        second = client.post(f"/jobs/{job_id}/trigger")
        assert second.status_code == 202
        second_run = store.get_run(second.json()["run_id"])
        assert second_run is not None
        assert second_run.skipped is True
