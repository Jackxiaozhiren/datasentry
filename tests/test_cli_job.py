"""Step 86（ADR-086）：CLI `job` 子命令面。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from datasentry.cli import main


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    ws = tmp_path / "ws"
    ws.mkdir()
    return ws


def _data(ws: Path, name: str = "data.csv") -> Path:
    p = ws / name
    p.write_text("name,age\n,30\nbob,25\n", encoding="utf-8")
    return p


class TestJobList:
    def test_list_empty(self, workspace: Path, capsys: pytest.CaptureFixture[str]) -> None:
        code = main(["--project", str(workspace), "job", "list"])
        out = json.loads(capsys.readouterr().out)
        assert code == 0
        assert out["count"] == 0
        assert out["jobs"] == []


class TestJobCreate:
    def test_create_then_list(self, workspace: Path, capsys: pytest.CaptureFixture[str]) -> None:
        data = _data(workspace)
        code = main(
            [
                "--project",
                str(workspace),
                "job",
                "create",
                "daily",
                str(data),
                "--cron",
                "0 9 * * *",
                "--gate-quality-min",
                "80",
                "--retry-attempts",
                "2",
            ]
        )
        out = json.loads(capsys.readouterr().out)
        assert code == 0
        job = out
        assert job["name"] == "daily"
        assert job["cron"] == "0 9 * * *"
        assert job["gate_quality_min"] == 80
        assert job["retry_attempts"] == 2
        assert job["next_run_at"]

        code = main(["--project", str(workspace), "job", "list"])
        listed = json.loads(capsys.readouterr().out)
        assert listed["count"] == 1
        assert listed["jobs"][0]["job_id"] == job["job_id"]

    def test_invalid_cron_rejected(
        self, workspace: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        data = _data(workspace)
        code = main(
            ["--project", str(workspace), "job", "create", "bad", str(data), "--cron", "not-a-cron"]
        )
        out = json.loads(capsys.readouterr().out)
        assert code == 2
        assert "error" in out

    def test_filter_by_status(self, workspace: Path, capsys: pytest.CaptureFixture[str]) -> None:
        data = _data(workspace)
        main(
            [
                "--project",
                str(workspace),
                "job",
                "create",
                "daily",
                str(data),
                "--cron",
                "0 9 * * *",
            ]
        )
        capsys.readouterr()
        main(["--project", str(workspace), "job", "list", "--status", "idle"])
        idle = json.loads(capsys.readouterr().out)
        assert idle["count"] == 1
        main(["--project", str(workspace), "job", "list", "--status", "running"])
        running = json.loads(capsys.readouterr().out)
        assert running["count"] == 0


class TestJobTrigger:
    def test_trigger_missing_job(self, workspace: Path, capsys: pytest.CaptureFixture[str]) -> None:
        code = main(["--project", str(workspace), "job", "trigger", "job_nope"])
        out = json.loads(capsys.readouterr().out)
        assert code == 2
        assert "not found" in out["error"]


class TestJobStatus:
    def test_status_unknown_job(self, workspace: Path, capsys: pytest.CaptureFixture[str]) -> None:
        code = main(["--project", str(workspace), "job", "status", "job_nope"])
        out = json.loads(capsys.readouterr().out)
        assert code == 2
        assert "not found" in out["error"]


class TestJobRemove:
    def test_remove_job(self, workspace: Path, capsys: pytest.CaptureFixture[str]) -> None:
        data = _data(workspace)
        main(
            [
                "--project",
                str(workspace),
                "job",
                "create",
                "daily",
                str(data),
                "--cron",
                "0 9 * * *",
            ]
        )
        job = json.loads(capsys.readouterr().out)
        code = main(["--project", str(workspace), "job", "remove", job["job_id"]])
        out = json.loads(capsys.readouterr().out)
        assert code == 0
        assert out["removed"] is True
        main(["--project", str(workspace), "job", "list"])
        assert json.loads(capsys.readouterr().out)["count"] == 0

    def test_remove_missing_job(self, workspace: Path, capsys: pytest.CaptureFixture[str]) -> None:
        code = main(["--project", str(workspace), "job", "remove", "job_nope"])
        out = json.loads(capsys.readouterr().out)
        assert code == 2
        assert "not found" in out["error"]
