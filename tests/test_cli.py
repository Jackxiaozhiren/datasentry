"""Step 10 CLI/SDK 闭环测试（22 章子集 + 23.1 客户端）。"""

from __future__ import annotations

import json
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

from datasentry import DataSentry, __version__
from datasentry.cli import main
from datasentry_core.connectors.errors import ConnectorError, DataSourceNotFoundError


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
        assert len(runs) == 39
        assert issues, "脏数据应产生 Issue"
        assert scan.id in scan_run_ids(client)
        assert all(i.priority_score > 0 for i in issues)
        client.close()

    def test_scan_file_missing_source(self, workspace: Path) -> None:
        client = DataSentry(project=workspace)
        with pytest.raises(FileNotFoundError):
            client.scan_file(workspace / "nope.csv")
        client.close()

    def test_scan_file_progress_callback(self, sample_csv: Path, workspace: Path) -> None:
        """on_progress 回调逐检测器上报（V24，TUI/CLI 实时进度）。"""
        client = DataSentry(project=workspace)
        steps: list[tuple[int, int, str]] = []

        def cb(done: int, total: int, name: str) -> None:
            steps.append((done, total, name))

        client.scan_file(sample_csv, on_progress=cb)
        assert len(steps) >= 3, "至少上报 3 个检测器"
        assert steps[0][1] == steps[-1][1], "total 一致"
        assert steps[0][1] > 0
        assert all(n for _, _, n in steps)
        assert steps[0][0] == 0 and steps[-1][0] == steps[-1][1] - 1
        client.close()

    def test_scan_writes_profile_sidecar(self, sample_csv: Path, workspace: Path) -> None:
        """Step 61：扫描期画像落 <workspace>/.datasentry/profiles/<run_id>.json。"""
        client = DataSentry(project=workspace)
        scan, _, _ = client.scan_file(sample_csv)
        path = client.profiles_dir / f"{scan.id}.json"
        assert path.is_file()
        data = json.loads(path.read_text(encoding="utf-8"))
        assert data["row_count"] == 4
        assert set(data["column_profiles"]) == {"id", "amount", "email"}
        assert data["column_profiles"]["amount"]["mean"] is not None
        top = data["column_profiles"]["email"]["top_categories"]
        assert top is not None
        assert ("a@x.co", 1) in [tuple(t) for t in top]
        assert ("not-an-email", 1) in [tuple(t) for t in top]
        assert client.load_profile(scan.id) == data
        client.close()

    def test_load_profile_missing_returns_none(self, workspace: Path) -> None:
        client = DataSentry(project=workspace)
        assert client.load_profile("scan_nope") is None
        client.close()

    def test_scan_pg_dsn_without_table_raises(self, workspace: Path) -> None:
        """Step 55：postgresql:// DSN 缺 --table → 可操作错误（连接器 open 阶段，无网络）。"""
        client = DataSentry(project=workspace)
        with pytest.raises(DataSourceNotFoundError):
            client.scan_file("postgresql://user:pass@localhost:5432/app")
        client.close()

    def test_scan_pg_dsn_connection_failure_redacted(self, workspace: Path) -> None:
        """Step 55：连接失败 → ConnectorError 且凭据已净化（DSN/密码不出现在错误面）。"""
        client = DataSentry(project=workspace)
        with pytest.raises(ConnectorError) as exc:
            client.scan_file(
                "postgresql://user:secret@localhost:1/app",
                table_name="payments",
                dataset_id="pg_payments",
            )
        assert "secret" not in str(exc.value)
        assert "postgresql://user:secret@localhost:1/app" not in str(exc.value)
        client.close()

    def test_scan_mysql_dsn_without_table_raises(self, workspace: Path) -> None:
        """Step 56：mysql:// DSN 缺 --table → 可操作错误（连接器 open 阶段，无网络）。"""
        client = DataSentry(project=workspace)
        with pytest.raises(DataSourceNotFoundError):
            client.scan_file("mysql://user:pass@localhost:3306/app")
        client.close()

    def test_scan_mysql_dsn_connection_failure_redacted(self, workspace: Path) -> None:
        """Step 56：MySQL 连接失败 → ConnectorError 且凭据已净化（DSN/密码不出现在错误面）。"""
        client = DataSentry(project=workspace)
        with pytest.raises(ConnectorError) as exc:
            client.scan_file(
                "mysql://user:secret@localhost:1/app",
                table_name="payments",
                dataset_id="mysql_payments",
            )
        assert "secret" not in str(exc.value)
        assert "mysql://user:secret@localhost:1/app" not in str(exc.value)
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

    def test_export_report_junit_and_sarif(self, sample_csv: Path, workspace: Path) -> None:
        """Step 36：--as junit / --as sarif CI 导出走通文件落盘。"""
        client = DataSentry(project=workspace)
        scan, _, issues = client.scan_file(sample_csv)
        client.close()
        junit_out = workspace / "junit.xml"
        code = main(
            [
                "--project",
                str(workspace),
                "report",
                "export",
                scan.id,
                "--as",
                "junit",
                "--output",
                str(junit_out),
            ]
        )
        assert code == 0
        assert junit_out.exists()
        root = ET.fromstring(junit_out.read_text(encoding="utf-8"))
        assert root.tag == "testsuite"
        assert int(root.get("tests")) == len(issues)
        sarif_out = workspace / "sarif.json"
        code = main(
            [
                "--project",
                str(workspace),
                "report",
                "export",
                scan.id,
                "--as",
                "sarif",
                "--output",
                str(sarif_out),
            ]
        )
        assert code == 0
        sarif = json.loads(sarif_out.read_text(encoding="utf-8"))
        assert sarif["version"] == "2.1.0"
        assert len(sarif["runs"][0]["results"]) == len(issues)

    def test_contract_export_pandera_and_ge(self, workspace: Path, capsys) -> None:
        """Step 37：contract export 生成 Pandera 代码与 GE suite 文件。"""
        contract = workspace / "orders.yaml"
        contract.write_text(
            "version: '1.0'\n"
            "dataset:\n"
            "  name: orders\n"
            "columns:\n"
            "  amount:\n"
            "    type: float\n"
            "    min: 0.0\n"
            "    max: 100000.0\n",
            encoding="utf-8",
        )
        py_out = workspace / "schema.py"
        code = main(
            [
                "--project",
                str(workspace),
                "contract",
                "export",
                str(contract),
                "--as",
                "pandera",
                "--output",
                str(py_out),
            ]
        )
        assert code == 0
        assert "DataFrameSchema" in py_out.read_text(encoding="utf-8")
        ge_out = workspace / "suite.ge.json"
        code = main(
            [
                "--project",
                str(workspace),
                "contract",
                "export",
                str(contract),
                "--as",
                "ge",
                "--output",
                str(ge_out),
            ]
        )
        assert code == 0
        suite = json.loads(ge_out.read_text(encoding="utf-8"))
        assert suite["expectation_suite_name"] == "orders.datasentry"
        assert any(
            e["expectation_type"] == "expect_column_values_to_be_between"
            for e in suite["expectations"]
        )
        code = main(
            [
                "--project",
                str(workspace),
                "contract",
                "export",
                str(workspace / "missing.yaml"),
                "--as",
                "ge",
            ]
        )
        assert code == 4  # EXIT_SOURCE_UNAVAILABLE

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

    def test_scan_batch_two_files(self, sample_csv: Path, workspace: Path, capsys) -> None:
        """V26：逗号分隔多文件 → batch 汇总。"""
        second = workspace / "second.csv"
        second.write_text(sample_csv.read_text(encoding="utf-8"), encoding="utf-8")
        code = main(
            [
                "--project",
                str(workspace),
                "--format",
                "json",
                "scan",
                f"{sample_csv}, {second}",
            ]
        )
        assert code == 0
        data = json.loads(capsys.readouterr().out)["data"]
        assert data["files_scanned"] == 2
        assert data["files_failed"] == 0
        assert len(data["batch"]) == 2
        assert data["total_issues"] == 2 * data["batch"][0]["total_issues"]

    def test_scan_batch_partial_failure_exit_4(
        self, sample_csv: Path, workspace: Path, capsys
    ) -> None:
        """V26：部分文件失败 → 成功部分照常输出，退出码 4。"""
        code = main(
            [
                "--project",
                str(workspace),
                "--format",
                "json",
                "scan",
                f"{sample_csv}, {workspace / 'nope.csv'}",
            ]
        )
        assert code == 4
        data = json.loads(capsys.readouterr().out)["data"]
        assert data["files_scanned"] == 1
        assert data["files_failed"] == 1
        assert data["errors"][0]["path"].endswith("nope.csv")

    def test_scan_glob_expands(self, sample_csv: Path, workspace: Path, capsys) -> None:
        """V26：* 通配由 CLI 展开（引号内 shell 不展开）。"""
        (workspace / "g1.csv").write_text(sample_csv.read_text(encoding="utf-8"), encoding="utf-8")
        (workspace / "g2.csv").write_text(sample_csv.read_text(encoding="utf-8"), encoding="utf-8")
        code = main(["--project", str(workspace), "--format", "json", "scan", f"{workspace}/*.csv"])
        assert code == 0
        data = json.loads(capsys.readouterr().out)["data"]
        assert data["files_scanned"] == 2

    def test_scan_missing_file_exit_4(self, workspace: Path, capsys) -> None:
        code = main(["--project", str(workspace), "scan", str(workspace / "nope.csv")])
        assert code == 4

    def test_scan_pg_dsn_without_table_exit_2(self, workspace: Path, capsys) -> None:
        """Step 55：postgresql:// DSN 缺 --table → EXIT_CONFIG（可操作错误）。"""
        code = main(
            ["--project", str(workspace), "scan", "postgresql://user:pass@localhost:5432/app"]
        )
        assert code == 2

    def test_scan_pg_dsn_conn_failure_exit_4_redacted(self, workspace: Path, capsys) -> None:
        """Step 55：PG 连接失败 → EXIT_SOURCE_UNAVAILABLE，错误面无凭据。"""
        code = main(
            [
                "--project",
                str(workspace),
                "scan",
                "postgresql://user:secret@localhost:1/app",
                "--table",
                "payments",
            ]
        )
        assert code == 4
        out = capsys.readouterr().out
        assert "secret" not in out
        assert "postgresql://user:secret@localhost:1/app" not in out

    def test_scan_mysql_dsn_without_table_exit_2(self, workspace: Path, capsys) -> None:
        """Step 56：mysql:// DSN 缺 --table → EXIT_CONFIG（可操作错误）。"""
        code = main(["--project", str(workspace), "scan", "mysql://user:pass@localhost:3306/app"])
        assert code == 2

    def test_scan_mysql_dsn_conn_failure_exit_4_redacted(self, workspace: Path, capsys) -> None:
        """Step 56：MySQL 连接失败 → EXIT_SOURCE_UNAVAILABLE，错误面无凭据。"""
        code = main(
            [
                "--project",
                str(workspace),
                "scan",
                "mysql://user:secret@localhost:1/app",
                "--table",
                "payments",
            ]
        )
        assert code == 4
        out = capsys.readouterr().out
        assert "secret" not in out
        assert "mysql://user:secret@localhost:1/app" not in out

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
        assert "<link" not in html and "<script src=" not in html  # 自包含单文件
        assert 'id="issue-table"' in html  # Step 49：交互式 Issue Breakdown
        assert 'id="column_profiles"' in html  # Step 61：Column Profiles 节
        assert 'id="profiles-data"' in html
        assert 'href="#column_profiles"' in html
        assert "DataSentry Data Quality Report" in html

    def test_report_export_html_comparison(self, sample_csv: Path, workspace: Path, capsys) -> None:
        main(["--project", str(workspace), "--format", "json", "scan", str(sample_csv)])
        capsys.readouterr()
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
        assert 'id="comparison"' in html  # Step 64：同数据集 2 run → 对比节
        assert 'href="#comparison"' in html
        assert 'class="cmp-current"' in html
        assert "Run Comparison" in html

    def test_report_export_missing_exit_2(self, workspace: Path, capsys) -> None:
        code = main(["--project", str(workspace), "report", "export", "nope"])
        assert code == 2

    def test_report_export_markdown_lang_zh(
        self, sample_csv: Path, workspace: Path, capsys
    ) -> None:
        main(["--project", str(workspace), "--format", "json", "scan", str(sample_csv)])
        scan_id = json.loads(capsys.readouterr().out)["data"]["scan_run_id"]
        out = workspace / "zh.md"
        code = main(
            [
                "--project",
                str(workspace),
                "--lang",
                "zh",
                "report",
                "export",
                scan_id,
                "--as",
                "markdown",
                "--output",
                str(out),
            ]
        )
        assert code == 0
        md = out.read_text(encoding="utf-8")
        assert "# DataSentry 数据质量报告" in md
        assert "## 可复现性" in md

    def test_global_lang_zh_localizes_score_text(
        self, sample_csv: Path, workspace: Path, capsys
    ) -> None:
        main(["--project", str(workspace), "--format", "json", "scan", str(sample_csv)])
        scan_id = json.loads(capsys.readouterr().out)["data"]["scan_run_id"]
        code = main(["--project", str(workspace), "--lang", "zh", "score", scan_id])
        assert code == 0
        out = capsys.readouterr().out
        assert "总体质量分" in out

    def test_global_lang_default_en_score_text(
        self, sample_csv: Path, workspace: Path, capsys
    ) -> None:
        main(["--project", str(workspace), "--format", "json", "scan", str(sample_csv)])
        scan_id = json.loads(capsys.readouterr().out)["data"]["scan_run_id"]
        code = main(["--project", str(workspace), "score", scan_id])
        assert code == 0
        out = capsys.readouterr().out
        assert "Overall quality score:" in out

    def test_global_lang_zh_localizes_issues_count(
        self, sample_csv: Path, workspace: Path, capsys
    ) -> None:
        main(["--project", str(workspace), "--format", "json", "scan", str(sample_csv)])
        code = main(["--project", str(workspace), "--lang", "zh", "issues", "list"])
        assert code == 0
        assert "问题数" in capsys.readouterr().out

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

    def test_score_latest_resolves_most_recent_scan(
        self, sample_csv: Path, workspace: Path, capsys
    ) -> None:
        main(["--project", str(workspace), "--format", "json", "scan", str(sample_csv)])
        first_id = json.loads(capsys.readouterr().out)["data"]["scan_run_id"]
        main(["--project", str(workspace), "--format", "json", "scan", str(sample_csv)])
        second_id = json.loads(capsys.readouterr().out)["data"]["scan_run_id"]
        assert first_id != second_id
        code = main(["--project", str(workspace), "--format", "json", "score", "latest"])
        assert code == 0
        payload = json.loads(capsys.readouterr().out)
        assert payload["command"] == "score"
        assert payload["data"]["scored"] is True
        assert payload["data"]["scan_run_id"] == second_id

    def test_score_latest_empty_workspace_exit_2(self, workspace: Path, capsys) -> None:
        code = main(["--project", str(workspace), "score", "latest"])
        assert code == 2

    def test_issues_list_limit_truncates(self, sample_csv: Path, workspace: Path, capsys) -> None:
        main(["--project", str(workspace), "--format", "json", "scan", str(sample_csv)])
        capsys.readouterr()
        code = main(["--project", str(workspace), "issues", "list", "--limit", "2"])
        assert code == 0
        out = capsys.readouterr().out
        assert "issues: 2" in out
        assert out.count("[") < 10  # 截断后不会列出全部问题

    def test_score_missing_exit_2(self, workspace: Path, capsys) -> None:
        code = main(["--project", str(workspace), "score", "nope"])
        assert code == 2

    def test_trend_list_empty(self, workspace: Path, capsys) -> None:
        code = main(["--project", str(workspace), "--format", "json", "trend", "list"])
        assert code == 0
        payload = json.loads(capsys.readouterr().out)
        assert payload["command"] == "trend list"
        assert payload["data"] == {"trends": [], "count": 0}

    def test_trend_list_groups_datasets_with_summary(
        self, sample_csv: Path, workspace: Path, capsys
    ) -> None:
        main(["--project", str(workspace), "--format", "json", "scan", str(sample_csv)])
        capsys.readouterr()
        main(["--project", str(workspace), "--format", "json", "scan", str(sample_csv)])
        capsys.readouterr()
        code = main(["--project", str(workspace), "--format", "json", "trend", "list"])
        assert code == 0
        payload = json.loads(capsys.readouterr().out)
        assert payload["data"]["count"] == 1
        trend = payload["data"]["trends"][0]
        assert trend["dataset_id"] == "orders"
        assert len(trend["points"]) == 2
        assert trend["points"][0]["run_id"] != trend["points"][1]["run_id"]
        assert all(
            "score" in p and "issues_total" in p and "finished_at" in p for p in trend["points"]
        )
        assert "delta" in trend and "direction" in trend and "latest_score" in trend

    def test_trend_list_dataset_filter(self, sample_csv: Path, workspace: Path, capsys) -> None:
        main(["--project", str(workspace), "--format", "json", "scan", str(sample_csv)])
        capsys.readouterr()
        code = main(
            [
                "--project",
                str(workspace),
                "--format",
                "json",
                "trend",
                "list",
                "--dataset-id",
                "nope",
            ]
        )
        assert code == 0
        payload = json.loads(capsys.readouterr().out)
        assert payload["data"] == {"trends": [], "count": 0}

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

    def test_scan_contract_gate_binds_quality_gate(
        self,
        sample_csv: Path,
        workspace: Path,
        capsys,
    ) -> None:
        contract = workspace / "c.yaml"
        contract.write_text(
            "version: '1.0'\n"
            "dataset:\n"
            "  name: orders\n"
            "quality_gate:\n"
            "  fail_on: [medium]\n"
            "  maximum_failed_rows_ratio: 0.01\n",
            encoding="utf-8",
        )
        code = main(
            [
                "--project",
                str(workspace),
                "--format",
                "json",
                "scan",
                str(sample_csv),
                "--contract",
                str(contract),
            ]
        )
        assert code == 1  # 契约 fail_on=medium → 门禁失败
        payload = json.loads(capsys.readouterr().out)
        assert payload["data"]["gate"]["passed"] is False
        assert "gate" in payload["data"]
        assert payload["data"]["gate"]["failed_count"] > 0

    def test_scan_contract_fail_on_override(
        self,
        sample_csv: Path,
        workspace: Path,
        capsys,
    ) -> None:
        contract = workspace / "c.yaml"
        contract.write_text(
            "version: '1.0'\ndataset:\n  name: orders\nquality_gate:\n  fail_on: [medium]\n",
            encoding="utf-8",
        )
        code = main(
            [
                "--project",
                str(workspace),
                "--format",
                "json",
                "scan",
                str(sample_csv),
                "--contract",
                str(contract),
                "--fail-on",
                "critical",
            ]
        )
        assert code == 0  # 显式 --fail-on 覆盖契约 gate
        payload = json.loads(capsys.readouterr().out)
        assert payload["data"]["gate"]["passed"] is True

    def test_scan_contract_rules_apply(self, workspace: Path, capsys) -> None:
        data = workspace / "payments.csv"
        data.write_text("id,amount\n1,-50\n2,30\n3,2000\n", encoding="utf-8")
        contract = workspace / "c.yaml"
        contract.write_text(
            "version: '1.0'\n"
            "dataset:\n"
            "  name: payments\n"
            "rules:\n"
            "  - id: negative_amount\n"
            "    type: value_range\n"
            "    severity: high\n"
            "    when:\n"
            "      column: amount\n"
            "      operator: gte\n"
            "      value: 0\n"
            "    description: amount must be non-negative\n",
            encoding="utf-8",
        )
        code = main(
            [
                "--project",
                str(workspace),
                "--format",
                "json",
                "scan",
                str(data),
                "--contract",
                str(contract),
            ]
        )
        assert code == 0
        payload = json.loads(capsys.readouterr().out)
        assert payload["data"]["total_issues"] >= 1  # 契约规则参与扫描

    def test_scan_contract_missing_file_exit_2(
        self,
        sample_csv: Path,
        workspace: Path,
        capsys,
    ) -> None:
        code = main(
            [
                "--project",
                str(workspace),
                "--format",
                "json",
                "scan",
                str(sample_csv),
                "--contract",
                str(workspace / "nope.yaml"),
            ]
        )
        assert code == 2

    def test_scan_contract_invalid_schema_exit_2(
        self,
        sample_csv: Path,
        workspace: Path,
        capsys,
    ) -> None:
        contract = workspace / "bad.yaml"
        contract.write_text("dataset: [unclosed", encoding="utf-8")
        code = main(
            [
                "--project",
                str(workspace),
                "--format",
                "json",
                "scan",
                str(sample_csv),
                "--contract",
                str(contract),
            ]
        )
        assert code == 2
        payload = json.loads(capsys.readouterr().out)
        assert payload["data"]["valid"] is False  # 契约校验失败 envelope

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

    # ---- Step 48：llm restore / rotate-key（PII 加密还原） ----------------

    def test_llm_status_shows_vault(self, workspace: Path, capsys) -> None:
        code = main(["--project", str(workspace), "--format", "json", "llm", "status"])
        assert code == 0
        payload = json.loads(capsys.readouterr().out)
        assert payload["data"]["pii_vault"]["key_source"] == "dev"
        assert payload["data"]["pii_vault"]["mappings"] == 0

    def test_llm_status_key_fingerprint(
        self, workspace: Path, capsys, monkeypatch, tmp_path
    ) -> None:
        monkeypatch.setattr("datasentry.pii_vault._key_file", lambda: tmp_path / "vault.key")
        assert main(["--project", str(workspace), "--format", "json", "llm", "rotate-key"]) == 0
        capsys.readouterr()
        assert main(["--project", str(workspace), "--format", "json", "llm", "status"]) == 0
        payload = json.loads(capsys.readouterr().out)
        vault = payload["data"]["pii_vault"]
        assert vault["key_source"] == "file"
        assert len(vault["key_fingerprint"]) == 8
        assert vault["key_file"]["path"].endswith("vault.key")
        assert vault["key_file"]["mtime"] is not None

    def test_llm_restore_list_empty(self, workspace: Path, capsys) -> None:
        code = main(["--project", str(workspace), "--format", "json", "llm", "restore"])
        assert code == 0
        payload = json.loads(capsys.readouterr().out)
        assert payload["data"]["sessions"] == []

    def test_llm_restore_unknown_session_exit_3(self, workspace: Path, capsys) -> None:
        code = main(["--project", str(workspace), "--format", "json", "llm", "restore", "pii_nope"])
        assert code == 3

    def test_llm_rotate_key_writes_key_file(
        self, workspace: Path, capsys, monkeypatch, tmp_path: Path
    ) -> None:
        monkeypatch.setattr("datasentry.pii_vault._key_file", lambda: tmp_path / "vault.key")
        code = main(["--project", str(workspace), "--format", "json", "llm", "rotate-key"])
        assert code == 0
        payload = json.loads(capsys.readouterr().out)
        assert payload["command"] == "llm rotate-key"
        assert payload["data"]["rotated"] == 0
        assert len(payload["data"]["new_key"]) >= 32
        assert (tmp_path / "vault.key").is_file()

    def test_llm_rotate_key_reencrypts_mappings(
        self, workspace: Path, capsys, monkeypatch, tmp_path: Path
    ) -> None:
        from datasentry import repair_ai
        from datasentry.client import DataSentry as SDKClient
        from datasentry.pii_vault import PIIVault

        monkeypatch.setattr("datasentry.pii_vault._key_file", lambda: tmp_path / "vault.key")
        pii_csv = workspace / "pii.csv"
        pii_csv.write_text("name,email\n a1 ,a1@x.io\n a2 ,a2@x.io\n", encoding="utf-8")
        provider = type(
            "P",
            (),
            {
                "provider_id": "fake",
                "model": "fake-model",
                "complete": lambda self, req: type(
                    "R",
                    (),
                    {
                        "text": '{"operation": "trim_whitespace", "parameters": {}, '
                        '"rationale": "strip"}',
                        "input_tokens": 1,
                        "output_tokens": 1,
                        "cache_hit": False,
                        "model": "fake-model",
                        "latency_ms": 1,
                    },
                )(),
            },
        )()
        client = SDKClient(project=workspace)
        try:
            _, _, issues = client.scan_file(pii_csv)
            issue = next(i for i in issues if "leading_or_trailing_whitespace" in i.detector_ids)
            repair_ai.AIRepairService(
                store=client._store, provider=provider, vault=PIIVault(client._store)
            ).propose(issue.id, str(pii_csv))
        finally:
            client.close()
        code = main(["--project", str(workspace), "--format", "json", "llm", "rotate-key"])
        assert code == 0
        payload = json.loads(capsys.readouterr().out)
        assert payload["data"]["rotated"] == 1

    # ---- Step 103：llm restore --purge --older-than（V18，ADR-103） -------

    def test_llm_purge_deletes_old_keeps_new(
        self, workspace: Path, capsys, monkeypatch, tmp_path: Path
    ) -> None:
        from datetime import timedelta

        from datasentry.client import DataSentry as SDKClient
        from datasentry_core.models.evidence import utcnow

        monkeypatch.setenv("DATASENTRY_ENCRYPTION_KEY", "cli-purge-test-key")
        monkeypatch.setattr("datasentry.pii_vault._key_file", lambda: tmp_path / "vault.key")
        store = SDKClient(project=workspace)._store
        store.save_pii_mapping(
            "pii_old", "ct", key_version="env", created_at=utcnow() - timedelta(days=60)
        )
        store.save_pii_mapping(
            "pii_new", "ct2", key_version="env", created_at=utcnow() - timedelta(days=2)
        )
        code = main(
            [
                "--project",
                str(workspace),
                "--format",
                "json",
                "llm",
                "restore",
                "--purge",
                "--older-than",
                "30",
            ]
        )
        assert code == 0
        payload = json.loads(capsys.readouterr().out)
        assert payload["data"]["purged"] == 1
        listed = main(["--project", str(workspace), "--format", "json", "llm", "restore"])
        assert listed == 0
        sessions = json.loads(capsys.readouterr().out)["data"]["sessions"]
        assert [s["session_id"] for s in sessions] == ["pii_new"]

    def test_llm_purge_works_without_key(
        self, workspace: Path, capsys, monkeypatch, tmp_path: Path
    ) -> None:
        from datetime import timedelta

        from datasentry.client import DataSentry as SDKClient
        from datasentry_core.models.evidence import utcnow

        monkeypatch.delenv("DATASENTRY_ENCRYPTION_KEY", raising=False)
        monkeypatch.setattr("datasentry.pii_vault._key_file", lambda: tmp_path / "vault.key")
        store = SDKClient(project=workspace)._store
        store.save_pii_mapping(
            "pii_old", "ct", key_version="env", created_at=utcnow() - timedelta(days=60)
        )
        code = main(
            [
                "--project",
                str(workspace),
                "--format",
                "json",
                "llm",
                "restore",
                "--purge",
                "--older-than",
                "30",
            ]
        )
        assert code == 0  # purge 不需要密钥（与 --delete 同语义）
        payload = json.loads(capsys.readouterr().out)
        assert payload["data"]["purged"] == 1

    def test_llm_purge_rejects_bad_args(self, workspace: Path, capsys) -> None:
        code = main(
            [
                "--project",
                str(workspace),
                "--format",
                "json",
                "llm",
                "restore",
                "pii_x",
                "--purge",
            ]
        )
        assert code == 3
        payload = json.loads(capsys.readouterr().out)
        assert "cannot be combined" in payload["data"]["error"]
        code = main(
            [
                "--project",
                str(workspace),
                "--format",
                "json",
                "llm",
                "restore",
                "--purge",
                "--older-than",
                "0",
            ]
        )
        assert code == 3
        payload = json.loads(capsys.readouterr().out)
        assert "must be >= 1" in payload["data"]["error"]


class TestSecretsCli:
    """Step 59 凭据管理（ADR-059）：secrets set/get/list/rm 子命令族。"""

    @pytest.fixture(autouse=True)
    def _isolate_secrets(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("DATASENTRY_CONFIG_HOME", str(tmp_path / "cfg"))

    @staticmethod
    def _fake_getpass(
        monkeypatch: pytest.MonkeyPatch, value: str = "postgresql://u:p@h/db"
    ) -> None:
        import getpass

        monkeypatch.setattr(getpass, "getpass", lambda prompt="": value)

    def test_set_get_list_rm_roundtrip(self, capsys, monkeypatch) -> None:
        self._fake_getpass(monkeypatch)
        assert main(["secrets", "set", "DATASENTRY_PG_DSN"]) == 0
        out = json.loads(capsys.readouterr().out)
        assert out["key"] == "DATASENTRY_PG_DSN"
        assert "secrets.env" in out["path"]

        assert main(["--format", "json", "secrets", "list"]) == 0
        listed = json.loads(capsys.readouterr().out)
        assert listed["data"]["keys"] == ["DATASENTRY_PG_DSN"]

        assert main(["--format", "json", "secrets", "get", "DATASENTRY_PG_DSN"]) == 0
        got = json.loads(capsys.readouterr().out)
        assert got["data"]["value"] == "postgresql://u:p@h/db"

        assert main(["secrets", "rm", "DATASENTRY_PG_DSN"]) == 0
        capsys.readouterr()
        assert main(["--format", "json", "secrets", "list"]) == 0
        assert json.loads(capsys.readouterr().out)["data"]["keys"] == []

    def test_list_never_shows_values(self, capsys, monkeypatch) -> None:
        self._fake_getpass(monkeypatch, value="super-secret-value")
        assert main(["secrets", "set", "DATASENTRY_PG_DSN"]) == 0
        assert main(["--format", "json", "secrets", "list"]) == 0
        out = capsys.readouterr().out
        assert "super-secret-value" not in out

    def test_set_confirmation_mismatch_exit_2(self, capsys, monkeypatch) -> None:
        import getpass

        calls = iter(["first", "second"])
        monkeypatch.setattr(getpass, "getpass", lambda prompt="": next(calls))
        assert main(["secrets", "set", "DATASENTRY_PG_DSN"]) == 2
        assert json.loads(capsys.readouterr().out)["error"].startswith("confirmation mismatch")
        assert main(["--format", "json", "secrets", "list"]) == 0
        assert json.loads(capsys.readouterr().out)["data"]["keys"] == []

    def test_invalid_key_rejected(self, capsys) -> None:
        assert main(["secrets", "set", "lowercase"]) == 2
        out = capsys.readouterr().out
        assert "invalid key" in out

    def test_get_missing_exit_2(self, capsys) -> None:
        assert main(["--format", "json", "secrets", "get", "DATASENTRY_PG_DSN"]) == 2
        assert json.loads(capsys.readouterr().out)["data"]["error"].startswith("secret not set")

    def test_rm_missing_exit_2(self, capsys) -> None:
        assert main(["--format", "json", "secrets", "rm", "DATASENTRY_PG_DSN"]) == 2

    def test_file_perms_after_set(self, capsys, monkeypatch) -> None:
        self._fake_getpass(monkeypatch)
        assert main(["secrets", "set", "DATASENTRY_PG_DSN"]) == 0
        listed = json.loads(capsys.readouterr().out)
        from pathlib import Path as P

        mode = P(listed["path"]).stat().st_mode & 0o777
        assert mode == 0o600
