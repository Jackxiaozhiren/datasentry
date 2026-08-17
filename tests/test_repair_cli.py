"""Step 21 修复 CLI / SDK 接入测试（15 章 + ADR-020）。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from datasentry import DataSentry
from datasentry.cli import main
from datasentry_core.models.contract import QualityGate
from datasentry_core.models.enums import RepairRunStatus, Severity


@pytest.fixture
def repair_csv(tmp_path: Path) -> Path:
    p = tmp_path / "customers.csv"
    rows = [f"user{i},active,{i * 10},2024-01-01" for i in range(30)]
    rows.append(" user30 ,Active,n/a,2024-02-30")
    rows.append("  user31  ,active,5000,2024-13-01")
    p.write_text("name,status,price,event_date\n" + "\n".join(rows) + "\n", encoding="utf-8")
    return p


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    ws = tmp_path / "ws"
    ws.mkdir()
    return ws


def _issue_for_detector(repair_csv: Path, workspace: Path, detector_id: str):
    """CLI 测试辅助：扫一次拿目标 Issue（不入本测试的 client）。"""
    client = DataSentry(project=workspace)
    try:
        _, _, issues = client.scan_file(repair_csv)
    finally:
        client.close()
    return next(i for i in issues if detector_id in i.detector_ids)


def _client(workspace: Path) -> DataSentry:
    return DataSentry(project=workspace)


def _scan_run(repair_csv: Path, workspace: Path) -> str:
    client = _client(workspace)
    try:
        run, _, _ = client.scan_file(repair_csv)
    finally:
        client.close()
    return run.id


class TestRepairClient:
    def test_propose_creates_trim_proposal(self, repair_csv: Path, workspace: Path) -> None:
        client = DataSentry(project=workspace)
        try:
            issue = _issue_using(client, repair_csv, "leading_or_trailing_whitespace")
            proposal = client.repair_propose(issue.id, repair_csv)
            assert proposal is not None
            assert proposal.issue_type == "leading_or_trailing_whitespace"
            assert proposal.operation.value == "trim_whitespace"
            assert proposal.estimated_rows_changed == 2
            stored = client._store.get_repair_proposal(proposal.proposal_id)
            assert stored is not None
            assert stored.issue_type == proposal.issue_type
        finally:
            client.close()

    def test_preview_reports_rule_reduction(self, repair_csv: Path, workspace: Path) -> None:
        client = DataSentry(project=workspace)
        try:
            issue = _issue_using(client, repair_csv, "leading_or_trailing_whitespace")
            result = client.repair_preview(issue.id, repair_csv)
            assert result is not None
            _, preview = result
            assert preview.rule_failures_before["leading_or_trailing_whitespace"] > 0
            assert preview.rule_failures_after["leading_or_trailing_whitespace"] == 0
            assert preview.rows_changed == 2
            assert preview.changed_examples
        finally:
            client.close()

    def test_apply_then_rollback_through_client(self, repair_csv: Path, workspace: Path) -> None:
        client = DataSentry(project=workspace)
        try:
            issue = _issue_using(client, repair_csv, "leading_or_trailing_whitespace")
            run = client.repair_apply(issue.id, repair_csv)
            assert run.status == RepairRunStatus.APPLIED
            assert run.fingerprint_before != run.fingerprint_after
            stored = client._store.get_repair_run(run.id)
            assert stored is not None
            assert stored.fingerprint_after == run.fingerprint_after
            output = workspace / ".datasentry" / "repairs" / f"{run.id}.csv"
            assert output.exists()
            rolled = client.repair_rollback(run.id)
            assert rolled.status == RepairRunStatus.ROLLED_BACK
            rolled_path = workspace / ".datasentry" / "repairs" / f"{run.id}.rolled_back.csv"
            assert rolled_path.exists()
            assert rolled.fingerprint_after == run.fingerprint_after
            assert client.list_repair_runs()
        finally:
            client.close()

    def test_repair_no_proposal_for_unmapped_issue(self, repair_csv: Path, workspace: Path) -> None:
        client = DataSentry(project=workspace)
        try:
            _, _, issues = client.scan_file(repair_csv)
            # 找到至少一个不在 MVP 修复映射中的 Issue（uniqueness/日期类）并拒绝提案
            outside = next(
                i
                for i in issues
                if not any(
                    d
                    in {
                        "leading_or_trailing_whitespace",
                        "inconsistent_case",
                        "suspicious_missing_token",
                        "invalid_date",
                        "impossible_date",
                        "iqr_outlier",
                        "percentile_outlier",
                        "modified_zscore",
                    }
                    for d in i.detector_ids
                )
            )
            assert client.repair_propose(outside.id, repair_csv) is None
        finally:
            client.close()

    def test_repair_validation_evidence_cycle(self, repair_csv: Path, workspace: Path) -> None:
        """Step 35 E2E：require_repair_validation 门禁 = 修复证据闭环。"""
        client = DataSentry(project=workspace)
        try:
            _, _, issues = client.scan_file(repair_csv)
            gate = QualityGate(fail_on=[Severity.HIGH], require_repair_validation=True)
            # 常规求值失败 + 无修复证据 → 拦截
            assert client.evaluate_gate(issues, gate).passed is False
            assert client._store.has_applied_repairs() is False
            # 走修复闭环 → 证据出现 → 放行
            repaired = False
            for issue in sorted(issues, key=lambda i: i.priority_score, reverse=True):
                if client.repair_propose(issue.id, repair_csv) is None:
                    continue
                client.repair_apply(issue.id, repair_csv)
                repaired = True
                break
            assert repaired is True
            assert client._store.has_applied_repairs() is True
            assert client.evaluate_gate(issues, gate).passed is True
        finally:
            client.close()


def _issue_using(client: DataSentry, repair_csv: Path, detector_id: str):
    _, _, issues = client.scan_file(repair_csv)
    return next(i for i in issues if detector_id in i.detector_ids)


class TestRepairCli:
    def test_repair_propose_apply_batch_cli(
        self, repair_csv: Path, workspace: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """V36：propose-batch → apply-batch 全流程（--all，部分失败退出码）。"""
        scan = _scan_run(repair_csv, workspace)
        code = main(
            [
                "--project",
                str(workspace),
                "--format",
                "json",
                "repair",
                "propose-batch",
                scan,
                "--file",
                str(repair_csv),
                "--all",
            ]
        )
        assert code == 0
        out = json.loads(capsys.readouterr().out)["data"]
        assert out["failed"] == 0
        assert any(r["proposed"] for r in out["issues"])

        code = main(
            [
                "--project",
                str(workspace),
                "--format",
                "json",
                "repair",
                "apply-batch",
                scan,
                "--file",
                str(repair_csv),
                "--all",
            ]
        )
        assert code == 0
        out = json.loads(capsys.readouterr().out)["data"]
        assert out["failed"] == 0
        assert len(out["applied"]) >= 1
        run_ids = [r["run_id"] for r in out["applied"] if r.get("applied") and "run_id" in r]
        assert run_ids, "at least one applied run expected"

        code = main(
            [
                "--project",
                str(workspace),
                "--format",
                "json",
                "repair",
                "rollback-batch",
                ",".join(run_ids),
            ]
        )
        assert code == 0
        out = json.loads(capsys.readouterr().out)["data"]
        assert out["failed"] == 0
        assert all(r["status"] == "rolled_back" for r in out["rolled_back"])

    def test_repair_list_run_filter_cli(
        self, repair_csv: Path, workspace: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """V38：repair list --run 只返回该 run 数据集上的修复。"""
        scan = _scan_run(repair_csv, workspace)
        client = _client(workspace)
        issue = _issue_for_detector(repair_csv, workspace, "leading_or_trailing_whitespace")
        client.repair_apply(issue.id, repair_csv)
        client.close()
        code = main(
            [
                "--project",
                str(workspace),
                "--format",
                "json",
                "repair",
                "list",
                "--run",
                scan,
            ]
        )
        assert code == 0
        listing = json.loads(capsys.readouterr().out)["data"]["runs"]
        assert listing, "expected at least one repair for the run's dataset"
        assert all(
            r["dataset_id"] == "customers.csv" or r["dataset_id"] == "customers" for r in listing
        )

    def test_repair_apply_batch_unknown_issue_partial(
        self, repair_csv: Path, workspace: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """V36：未知 issue id → 部分失败（退出码 4 + errors 报告）。"""
        scan = _scan_run(repair_csv, workspace)
        issues = _client(workspace).list_issues(scan_run_id=scan)
        real = issues[0].id if issues else ""
        code = main(
            [
                "--project",
                str(workspace),
                "--format",
                "json",
                "repair",
                "apply-batch",
                scan,
                "--file",
                str(repair_csv),
                "--issues",
                f"{real},iss_not_exists",
            ]
        )
        assert code == 4
        out = json.loads(capsys.readouterr().out)["data"]
        assert "iss_not_exists" in out["errors"]
        assert out["failed"] >= 1

    def test_repair_apply_list_rollback_cli(
        self, repair_csv: Path, workspace: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        issue = _issue_for_detector(repair_csv, workspace, "leading_or_trailing_whitespace")
        code = main(
            [
                "--project",
                str(workspace),
                "--format",
                "json",
                "repair",
                "apply",
                issue.id,
                "--file",
                str(repair_csv),
            ]
        )
        assert code == 0
        applied = json.loads(capsys.readouterr().out)["data"]
        assert applied["applied"] is True
        run_id = applied["run_id"]

        code = main(["--project", str(workspace), "--format", "json", "repair", "list"])
        assert code == 0
        listing = json.loads(capsys.readouterr().out)["data"]["runs"]
        assert any(r["id"] == run_id for r in listing)

        code = main(
            [
                "--project",
                str(workspace),
                "--format",
                "json",
                "repair",
                "rollback",
                run_id,
            ]
        )
        assert code == 0
        rolled = json.loads(capsys.readouterr().out)["data"]
        assert rolled["rolled_back"] is True

    def test_repair_preview_cli(
        self, repair_csv: Path, workspace: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        issue = _issue_for_detector(repair_csv, workspace, "leading_or_trailing_whitespace")
        code = main(
            [
                "--project",
                str(workspace),
                "--format",
                "json",
                "repair",
                "preview",
                issue.id,
                "--file",
                str(repair_csv),
            ]
        )
        assert code == 0
        data = json.loads(capsys.readouterr().out)["data"]
        assert data["previewed"] is True
        assert data["rule_failures_after"]["leading_or_trailing_whitespace"] == 0

    def test_repair_apply_missing_issue_exits_error(
        self, workspace: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        code = main(
            [
                "--project",
                str(workspace),
                "--format",
                "json",
                "repair",
                "apply",
                "iss_nope",
                "--file",
                "x.csv",
            ]
        )
        assert code == 3
