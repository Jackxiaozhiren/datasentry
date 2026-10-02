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
        """无 config 任务：scan_run.config 为默认配置（D1-02：默认 reservoir）。"""
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
            assert run.config.sampling.method == "reservoir"
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


def _resolver(mapping: dict[str, str]) -> object:
    def resolve(host: object, port: int, *_args: object, **_kw: object) -> list[object]:
        return [(0, 0, 0, "", (mapping.get(str(host), str(host)), port))]

    return resolve


class TestWebhookTargetPolicy:
    """D5-03 (row r4, P26 option B): the address classes a webhook fetch may not reach."""

    @pytest.mark.parametrize(
        "url",
        [
            "http://169.254.169.254/latest/meta-data/",
            "HTTP://169.254.169.254/",
            "http://[fe80::1]/x",
            "http://0.0.0.0/cb",
            "http://100.64.0.1/x",
            "http://224.0.0.1/x",
            "http://240.0.0.1/x",
            # IPv4-mapped IPv6 spelling: an IPv6Address compared against an IPv4Network is silently
            # False, so the v4 ranges were unenforced behind this notation (review C-1).
            "http://[::ffff:169.254.169.254]/latest/meta-data/",
            "http://[::ffff:100.64.0.1]/x",
            "http://[::ffff:0.0.0.0]/x",
            "http://[::ffff:224.0.0.1]/x",
            "http://good.example@169.254.169.254/",
        ],
    )
    def test_non_routable_classes_are_refused(self, url: str) -> None:
        from datasentry.scheduler.models import webhook_target_refusal

        assert webhook_target_refusal(url), url

    @pytest.mark.parametrize(
        ("url", "host"),
        [
            ("http://metadata-flipped.example/x", "169.254.169.254"),
            ("http://decimal-metadata.example/x", "169.254.169.254"),
        ],
    )
    def test_a_name_resolving_into_the_metadata_block_is_refused(self, url: str, host: str) -> None:
        """The point of resolving: a caller can hide the destination behind a name."""
        from datasentry.scheduler.models import webhook_target_refusal

        refused = webhook_target_refusal(url, _resolver({url.split("//")[1].split("/")[0]: host}))
        assert refused and host in refused, refused

    @pytest.mark.parametrize(
        "url",
        [
            "https://hooks.example.test/notify",
            "http://127.0.0.1:9999/cb",
            "http://127.1/cb",
            "http://2130706433/cb",
            "http://[::1]:9999/cb",
            "http://localhost:9999/cb",
            "http://10.0.8.12:8080/internal",
            "http://192.168.1.1/admin",
        ],
    )
    def test_the_documented_local_and_lan_cases_stay_allowed(self, url: str) -> None:
        """P26 B keeps these: local notification is the written use case, not an oversight."""
        from datasentry.scheduler.models import webhook_target_refusal

        assert webhook_target_refusal(url) is None, url

    def test_a_host_that_does_not_resolve_is_not_a_refusal(self) -> None:
        """Nothing is reachable, so the request fails on its own; refusing here would only
        couple the scheduler to live DNS and break `https://hook/x`-style test doubles."""
        from datasentry.scheduler.models import webhook_target_refusal

        assert webhook_target_refusal("http://does-not-exist.invalid/cb") is None

    def test_notifier_never_touches_a_refused_target(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        from datasentry.scheduler.core import WebhookNotifier

        calls: list[str] = []

        def factory() -> object:
            class _Client:
                def post(self, url: str, json: object) -> None:
                    calls.append(url)

                def close(self) -> None:
                    return None

            return _Client()

        notifier = WebhookNotifier(client_factory=factory)
        with caplog.at_level("WARNING", logger="datasentry.scheduler.core"):
            notifier.notify("http://169.254.169.254/latest/meta-data/", {"a": 1})
            notifier.notify("http://127.0.0.1:9/api", {"a": 1})
        assert calls == ["http://127.0.0.1:9/api"]
        assert "not delivered" in caplog.text

    def test_the_rest_delivery_face_refuses_too(self, tmp_path: Path) -> None:
        """`POST /jobs/{id}/test-webhook` is server-side fetch on a caller-supplied URL."""
        app = create_app(project=tmp_path)
        client = TestClient(app)
        created = client.post(
            "/jobs",
            json={
                "name": "j",
                "path": str(tmp_path / "orders.csv"),
                "cron": "5 * * * *",
                "webhook_url": "http://169.254.169.254/latest/meta-data/",
            },
        )
        assert created.status_code == 201, created.text
        resp = client.post(f"/jobs/{created.json()['job_id']}/test-webhook")
        assert resp.status_code == 422
        assert "non-routable" in resp.json()["detail"]
