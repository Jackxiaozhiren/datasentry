"""Step 10 CLI/SDK 闭环测试（22 章子集 + 23.1 客户端）。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from datasentry import DataSentry, __version__
from datasentry.cli import main


@pytest.fixture
def sample_csv(tmp_path: Path) -> Path:
    p = tmp_path / "orders.csv"
    p.write_text(
        "id,amount,email\n1,10,a@x.co\n1,1000,b@x.co\n2,-5,not-an-email\n,500,c@x.co\n",
        encoding="utf-8",
    )
    return p


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    ws = tmp_path / "ws"
    ws.mkdir()
    return ws


class TestClient:
    def test_scan_file_full_loop(self, sample_csv: Path, workspace: Path) -> None:
        client = DataSentry(project=workspace)
        scan, runs, issues = client.scan_file(sample_csv)
        assert scan.status == "completed"
        assert scan.fingerprint.row_count == 4
        assert len(runs) == 15
        assert issues, "脏数据应产生 Issue"
        assert scan.id in scan_run_ids(client)
        assert all(i.priority_score > 0 for i in issues)
        client.close()

    def test_scan_file_missing_source(self, workspace: Path) -> None:
        client = DataSentry(project=workspace)
        with pytest.raises(FileNotFoundError):
            client.scan_file(workspace / "nope.csv")
        client.close()

    def test_scan_file_dataset_id_defaults_to_stem(self, sample_csv: Path, workspace: Path) -> None:
        client = DataSentry(project=workspace)
        scan, _, _ = client.scan_file(sample_csv)
        assert scan.dataset_id == "orders"
        client.close()

    def test_list_issues_severity_filter(self, sample_csv: Path, workspace: Path) -> None:
        client = DataSentry(project=workspace)
        client.scan_file(sample_csv)
        all_issues = client.list_issues()
        high_issues = client.list_issues(severity_at_least="high")
        assert len(high_issues) <= len(all_issues)
        assert all(i.severity.value in ("high", "critical") for i in high_issues)
        client.close()

    def test_export_report_shape(self, sample_csv: Path, workspace: Path) -> None:
        client = DataSentry(project=workspace)
        scan, runs, issues = client.scan_file(sample_csv)
        report = client.export_report(scan.id)
        assert set(report) == {"scan", "detector_runs", "issues"}
        assert len(report["detector_runs"]) == len(runs)
        assert len(report["issues"]) == len(issues)
        assert report["scan"]["id"] == scan.id
        with pytest.raises(KeyError):
            client.export_report("missing")
        client.close()

    def test_init_creates_gitignore_entry(self, workspace: Path) -> None:
        client = DataSentry(project=workspace)
        assert (workspace / ".gitignore").read_text(encoding="utf-8").endswith(".datasentry/\n")
        client.close()


def scan_run_ids(client: DataSentry) -> set[str]:
    return {s.id for s in client._store.list_scan_runs()}


class TestCli:
    def test_version(self, capsys) -> None:
        assert main(["--version"]) == 0
        out = capsys.readouterr().out
        assert __version__ in out

    def test_scan_text(self, sample_csv: Path, workspace: Path, capsys) -> None:
        code = main(["--project", str(workspace), "scan", str(sample_csv)])
        out = capsys.readouterr().out
        assert code == 0
        assert "scan_run_id" in out

    def test_scan_json_envelope(self, sample_csv: Path, workspace: Path, capsys) -> None:
        code = main(["--project", str(workspace), "--format", "json", "scan", str(sample_csv)])
        assert code == 0
        payload = json.loads(capsys.readouterr().out)
        assert payload["ok"] is True
        assert payload["command"] == "scan"
        assert payload["data"]["status"] == "completed"
        assert payload["data"]["total_issues"] > 0
        assert payload["llm_usage"] == {"calls": 0, "tokens": 0}

    def test_scan_missing_file_exit_4(self, workspace: Path, capsys) -> None:
        code = main(["--project", str(workspace), "scan", str(workspace / "nope.csv")])
        assert code == 4

    def test_issues_list_json(self, sample_csv: Path, workspace: Path, capsys) -> None:
        main(["--project", str(workspace), "--format", "json", "scan", str(sample_csv)])
        capsys.readouterr()  # 清空上次输出，避免拼接破坏 JSON
        code = main(
            ["--project", str(workspace), "--format", "json", "issues", "list", "--severity", "low"]
        )
        assert code == 0
        payload = json.loads(capsys.readouterr().out)
        assert payload["command"] == "issues list"
        assert payload["data"]["count"] > 0

    def test_report_export_json(self, sample_csv: Path, workspace: Path, capsys) -> None:
        main(["--project", str(workspace), "--format", "json", "scan", str(sample_csv)])
        scan_id = json.loads(capsys.readouterr().out)["data"]["scan_run_id"]
        code = main(["--project", str(workspace), "--format", "json", "report", "export", scan_id])
        assert code == 0
        payload = json.loads(capsys.readouterr().out)
        assert payload["data"]["scan"]["id"] == scan_id
        assert "issues" in payload["data"]

    def test_report_export_missing_exit_2(self, workspace: Path, capsys) -> None:
        code = main(["--project", str(workspace), "report", "export", "nope"])
        assert code == 2

    def test_contract_validate_ok(self, workspace: Path, capsys) -> None:
        contract = workspace / "c.yaml"
        contract.write_text(
            "version: '1.0'\n"
            "dataset:\n"
            "  name: orders\n"
            "  primary_key: [id]\n"
            "columns:\n"
            "  id:\n"
            "    type: integer\n",
            encoding="utf-8",
        )
        code = main(
            ["--project", str(workspace), "--format", "json", "contract", "validate", str(contract)]
        )
        assert code == 0
        payload = json.loads(capsys.readouterr().out)
        assert payload["data"]["valid"] is True

    def test_contract_validate_bad_yaml_exit_2(self, workspace: Path, capsys) -> None:
        contract = workspace / "bad.yaml"
        contract.write_text("dataset: [unclosed", encoding="utf-8")
        code = main(
            ["--project", str(workspace), "--format", "json", "contract", "validate", str(contract)]
        )
        assert code == 2
        payload = json.loads(capsys.readouterr().out)
        assert payload["data"]["valid"] is False
