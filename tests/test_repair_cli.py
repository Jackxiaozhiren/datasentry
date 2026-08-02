"""Step 21 修复 CLI / SDK 接入测试（15 章 + ADR-020）。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from datasentry import DataSentry
from datasentry.cli import main
from datasentry_core.models.enums import RepairRunStatus


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


def _issue_using(client: DataSentry, repair_csv: Path, detector_id: str):
    _, _, issues = client.scan_file(repair_csv)
    return next(i for i in issues if detector_id in i.detector_ids)


class TestRepairCli:
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
