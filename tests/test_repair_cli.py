"""Step 21 修复 CLI / SDK 接入测试（15 章 + ADR-020）。"""

from __future__ import annotations

import hashlib
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

    def test_repair_diff_cli(
        self, repair_csv: Path, workspace: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """V46：repair diff——变更行 line/before/after 输出（text + json）。"""
        _scan_run(repair_csv, workspace)
        client = _client(workspace)
        issue = _issue_for_detector(repair_csv, workspace, "leading_or_trailing_whitespace")
        run = client.repair_apply(issue.id, repair_csv)
        client.close()
        code = main(["--project", str(workspace), "repair", "diff", run.id])
        assert code == 0
        out = capsys.readouterr().out
        assert "changed row" in out
        assert "line " in out
        assert "->" in out
        code = main(["--project", str(workspace), "--format", "json", "repair", "diff", run.id])
        assert code == 0
        data = json.loads(capsys.readouterr().out)["data"]
        assert data["run_id"] == run.id
        assert data["changed_rows"], "expected at least one changed row"
        row = data["changed_rows"][0]
        assert row["line"] >= 2
        assert row["before"] != row["after"]

    def test_repair_verify_cli(
        self, repair_csv: Path, workspace: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """V41：repair verify——重扫副本，残留 0 时退出码 0，有残留时 EXIT_GATE_FAILED。"""
        _scan_run(repair_csv, workspace)
        client = _client(workspace)
        issue = _issue_for_detector(repair_csv, workspace, "leading_or_trailing_whitespace")
        run = client.repair_apply(issue.id, repair_csv)
        client.close()
        code = main(
            [
                "--project",
                str(workspace),
                "--format",
                "json",
                "repair",
                "verify",
                run.id,
            ]
        )
        assert code == 0
        data = json.loads(capsys.readouterr().out)["data"]
        assert data["verify_issue_count"] < data["source_issue_count"]
        assert "string_format" in data["fixed_types"]
        assert data["new_types"] == []
        assert data["source_scan_run_id"].startswith("scan_")
        assert data["source_scan_run_id"] != data["verify_scan_run_id"]
        code = main(
            [
                "--project",
                str(workspace),
                "--format",
                "json",
                "repair",
                "verify",
                "--require-clean",
                run.id,
            ]
        )
        assert code == 1

    def test_repair_list_text_table(self, repair_csv: Path, workspace: Path, capsys) -> None:
        """V45：非 json 模式下 repair list 输出人类可读表格 + summary 行。"""
        _scan_run(repair_csv, workspace)
        client = _client(workspace)
        issue = _issue_for_detector(repair_csv, workspace, "leading_or_trailing_whitespace")
        client.repair_apply(issue.id, repair_csv)
        client.close()
        code = main(["--project", str(workspace), "repair", "list"])
        assert code == 0
        out = capsys.readouterr().out
        assert "run id" in out
        assert "applied / " in out and "rolled back / " in out

    def test_repair_list_dataset_filter_cli(
        self, repair_csv: Path, workspace: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """V40：repair list --dataset 按 dataset id 过滤。"""
        _scan_run(repair_csv, workspace)
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
                "--dataset",
                "customers",
            ]
        )
        assert code == 0
        listing = json.loads(capsys.readouterr().out)["data"]
        assert listing["summary"]["applied"] >= 1
        assert all(r["dataset_id"] == "customers" for r in listing["runs"])
        code = main(
            [
                "--project",
                str(workspace),
                "--format",
                "json",
                "repair",
                "list",
                "--dataset",
                "no_such.csv",
            ]
        )
        assert code == 0
        empty = json.loads(capsys.readouterr().out)["data"]
        assert empty["runs"] == [] and empty["summary"]["total"] == 0

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


def _artefacts(workspace: Path) -> list[Path]:
    return sorted((workspace / ".datasentry" / "repairs").glob("*.csv"))


def _repaired_copies(workspace: Path) -> list[Path]:
    """Repaired copies only.

    `*.csv` alone is satisfied by the engine's own `<id>.before.csv` backup, which `apply` writes
    *before* the copy is produced -- so a regression that deleted the copy but kept the backup left
    the denominator non-empty and all three guards green (independent review C-7).
    """
    return [
        p for p in _artefacts(workspace) if ".before" not in p.name and ".rolled_back" not in p.name
    ]


def _rolled_back_copies(workspace: Path) -> list[Path]:
    return [p for p in _artefacts(workspace) if ".rolled_back" in p.name]


class TestCliNeverOverwritesSource:
    """D3-01（r9）：不变量 3 在 CLI 面的字节级守护。

    engine 层已由外部改动补上（`test_repair_engine.py:136` 点名 D3-01），本类补 CLI 的两条
    通路：单条 `repair apply` 与 `repair apply-batch`。断言的是**源文件字节**前后相同——
    不是回滚副本、不是副本内容，因为这条不变量的失效形态是"原始数据集被静默销毁而全套测试仍绿"。
    """

    def test_single_apply_leaves_source_bytes_untouched(
        self, repair_csv: Path, workspace: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        issue = _issue_for_detector(repair_csv, workspace, "leading_or_trailing_whitespace")
        before = hashlib.sha256(repair_csv.read_bytes()).hexdigest()
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
        assert json.loads(capsys.readouterr().out)["data"]["applied"] is True
        assert hashlib.sha256(repair_csv.read_bytes()).hexdigest() == before, (
            "不变量 3 失效: CLI apply 改动了源文件"
        )
        assert _repaired_copies(workspace), "apply 未产出修复副本, 上面的「相同」就没有分母"

    def test_apply_batch_leaves_source_bytes_untouched(
        self, repair_csv: Path, workspace: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        scan = _scan_run(repair_csv, workspace)
        before = hashlib.sha256(repair_csv.read_bytes()).hexdigest()
        for argv in (
            ["repair", "propose-batch", scan, "--file", str(repair_csv), "--all"],
            ["repair", "apply-batch", scan, "--file", str(repair_csv), "--all"],
        ):
            code = main(["--project", str(workspace), "--format", "json", *argv])
            assert code == 0, argv
            capsys.readouterr()
        assert hashlib.sha256(repair_csv.read_bytes()).hexdigest() == before, (
            "不变量 3 失效: CLI apply-batch 改动了源文件"
        )
        assert _repaired_copies(workspace), "apply-batch 未产出修复副本, 上面的「相同」就没有分母"

    def test_rollback_leaves_source_bytes_untouched(
        self, repair_csv: Path, workspace: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """回滚同样不许碰源文件——它的产物是 `.rolled_back.csv`。

        基线取在 **apply 之前**: 若只在 apply 之后取, 一次"apply 顺手原地覆盖"就偷过了这条守护
        （第一版正是如此, 负向控制下它仍绿）。整段 apply → rollback 旅程比对同一份原始字节。
        """
        issue = _issue_for_detector(repair_csv, workspace, "leading_or_trailing_whitespace")
        original = hashlib.sha256(repair_csv.read_bytes()).hexdigest()
        main(
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
        run_id = json.loads(capsys.readouterr().out)["data"]["run_id"]
        assert hashlib.sha256(repair_csv.read_bytes()).hexdigest() == original, (
            "不变量 3 失效: apply 改动了源文件"
        )
        code = main(["--project", str(workspace), "--format", "json", "repair", "rollback", run_id])
        assert code == 0
        capsys.readouterr()
        assert hashlib.sha256(repair_csv.read_bytes()).hexdigest() == original
        assert _rolled_back_copies(workspace), "rollback 未产出 .rolled_back 副本"
