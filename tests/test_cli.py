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
        assert len(runs) == 21
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
        assert set(report) == {
            "report_schema_version",
            "datasentry_version",
            "scan_run_id",
            "generated_at",
            "reproducible",
            "llm_used",
            "scan",
            "detector_runs",
            "issues",
            "quality",
        }
        assert report["report_schema_version"] == "1.0"
        assert report["scan_run_id"] == scan.id
        assert report["reproducible"] is True and report["llm_used"] is False
        assert len(report["detector_runs"]) == len(runs)
        assert len(report["issues"]) == len(issues)
        assert report["scan"]["id"] == scan.id
        assert report["quality"]["overall"] == scan.quality_score.overall
        with pytest.raises(KeyError):
            client.export_report("missing")
        client.close()

    def test_quality_score_after_scan(self, sample_csv: Path, workspace: Path) -> None:
        client = DataSentry(project=workspace)
        scan, _, _ = client.scan_file(sample_csv)
        quality = client.quality_score(scan.id)
        assert quality is not None
        assert 0.0 <= quality.overall <= 100.0
        assert set(quality.dimensions) == {
            "completeness",
            "validity",
            "uniqueness",
            "consistency",
            "integrity",
            "timeliness",
        }
        assert quality.dimensions["consistency"] is None  # MVP 无一致性检测器
        with pytest.raises(KeyError):
            client.quality_score("missing")
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
        assert payload["data"]["quality_score"] is not None
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
        assert payload["data"]["format"] == "json"
        assert payload["data"]["path"].endswith(".json")
        assert Path(payload["data"]["path"]).is_file()
        content = json.loads(Path(payload["data"]["path"]).read_text(encoding="utf-8"))
        assert content["report_schema_version"] == "1.0"
        assert content["scan"]["id"] == scan_id
        assert "issues" in content

    def test_report_export_html(self, sample_csv: Path, workspace: Path, capsys) -> None:
        main(["--project", str(workspace), "--format", "json", "scan", str(sample_csv)])
        scan_id = json.loads(capsys.readouterr().out)["data"]["scan_run_id"]
        out = workspace / "out.html"
        code = main(
            [
                "--project",
                str(workspace),
                "report",
                "export",
                scan_id,
                "--as",
                "html",
                "--output",
                str(out),
            ]
        )
        assert code == 0
        html = out.read_text(encoding="utf-8")
        assert html.startswith("<!DOCTYPE html>")
        assert "score-bar" in html and "executive_summary" in html
        assert "<link" not in html and "<script" not in html  # 自包含单文件
        assert "DataSentry Data Quality Report" in html

    def test_report_export_missing_exit_2(self, workspace: Path, capsys) -> None:
        code = main(["--project", str(workspace), "report", "export", "nope"])
        assert code == 2

    def test_score_json(self, sample_csv: Path, workspace: Path, capsys) -> None:
        main(["--project", str(workspace), "--format", "json", "scan", str(sample_csv)])
        scan_id = json.loads(capsys.readouterr().out)["data"]["scan_run_id"]
        code = main(["--project", str(workspace), "--format", "json", "score", scan_id])
        assert code == 0
        payload = json.loads(capsys.readouterr().out)
        assert payload["command"] == "score"
        assert payload["data"]["scored"] is True
        assert payload["data"]["score"]["score_version"] == "1"
        assert payload["data"]["score"]["dimensions"]["consistency"] is None

    def test_score_missing_exit_2(self, workspace: Path, capsys) -> None:
        code = main(["--project", str(workspace), "score", "nope"])
        assert code == 2

    def test_scan_gate_fail_exit_1(self, sample_csv: Path, workspace: Path, capsys) -> None:
        code = main(
            [
                "--project",
                str(workspace),
                "--format",
                "json",
                "scan",
                str(sample_csv),
                "--fail-on",
                "medium",
            ]
        )
        assert code == 1
        payload = json.loads(capsys.readouterr().out)
        assert payload["data"]["gate"]["passed"] is False
        assert payload["data"]["gate"]["failed_count"] > 0

    def test_scan_gate_pass_exit_0(self, sample_csv: Path, workspace: Path, capsys) -> None:
        code = main(
            [
                "--project",
                str(workspace),
                "--format",
                "json",
                "scan",
                str(sample_csv),
                "--fail-on",
                "critical",
            ]
        )
        assert code == 0
        payload = json.loads(capsys.readouterr().out)
        assert payload["data"]["gate"]["passed"] is True

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
