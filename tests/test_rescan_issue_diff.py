"""O-D5-01 后半（AC-11b）：重扫 fixture、比对 issue 集、``rescan --fail-on-drift`` 门禁。

与 ``test_scoring_replay_batch.py`` 的分工：那个证明"分数可重放", 这个证明"检测器变更可复核"。
两者**不可互相顶替**——本文件里 ``test_changed_source_is_uncovered_not_clean`` 就是这条边界的
钉子：源文件一变, 差异不再归因于检测器, 工具必须说"没比", 不能说"没问题"。
"""

from __future__ import annotations

import json
import sqlite3
import uuid
from collections.abc import Iterator
from pathlib import Path

import pytest

from datasentry import DataSentry
from datasentry.cli import EXIT_CONFIG, EXIT_GATE_FAILED, EXIT_OK, main
from datasentry_core.models.enums import Severity
from datasentry_core.models.issue import Issue
from datasentry_core.models.scan import ScanRun
from datasentry_core.rescan import (
    IssueSetDiff,
    RescanReport,
    RescanStatus,
    diff_issue_sets,
    signature,
)

#: 所有"没比成"的状态。用显式元组而不是字符串列表, 才能让 parametrize 仍受类型检查。
NON_COMPARABLE: tuple[RescanStatus, ...] = (
    "run_incomplete",
    "source_missing",
    "source_unsupported",
    "source_changed",
    "scan_failed",
)


def _issue(
    issue_type: str,
    columns: tuple[str, ...] = ("id",),
    detectors: tuple[str, ...] = ("det_a",),
    *,
    severity: Severity = Severity.MEDIUM,
    affected: int = 1,
    table: str | None = None,
) -> Issue:
    """最小可构造 Issue：只填比对用得到的字段, 其余走模型默认值。"""
    return Issue(
        id=f"issue_{uuid.uuid4().hex[:8]}",
        scan_run_id="scan_stub",
        issue_type=issue_type,
        title=issue_type,
        dataset_id="ds",
        table_name=table,
        columns=list(columns),
        severity=severity,
        confidence=0.9,
        priority_score=50.0,
        affected_count=affected,
        affected_ratio=0.1,
        detector_ids=list(detectors),
    )


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


@pytest.fixture
def scanned(workspace: Path, sample_csv: Path) -> Iterator[tuple[DataSentry, ScanRun, list[Issue]]]:
    client = DataSentry(project=workspace)
    scan, _runs, issues = client.scan_file(sample_csv)
    yield client, scan, issues
    client.close()


def _db(workspace: Path) -> Path:
    return workspace / ".datasentry" / "metadata.db"


def _archive(workspace: Path) -> Path:
    return workspace / ".datasentry" / "rescan"


class TestIssueSetDiff:
    """纯比对层：不碰文件、不碰数据库。"""

    def test_identical_sets_are_clean(self) -> None:
        stored = [_issue("missingness"), _issue("string_format", ("email",), ("invalid_email",))]
        diff = diff_issue_sets("scan_1", stored, stored)
        assert diff.comparable
        assert not diff.drifted
        assert diff.unchanged == 2
        assert diff.added == ()
        assert diff.removed == ()
        assert diff.changed == ()

    def test_new_issue_is_added(self) -> None:
        stored = [_issue("missingness")]
        rescanned = [*stored, _issue("numeric_outlier", ("amount",), ("modified_zscore",))]
        diff = diff_issue_sets("scan_1", stored, rescanned)
        assert diff.drifted
        assert [sig.issue_type for sig in diff.added] == ["numeric_outlier"]
        assert diff.removed == ()

    def test_dropped_issue_is_removed(self) -> None:
        stored = [_issue("missingness"), _issue("string_format", ("email",), ("invalid_email",))]
        diff = diff_issue_sets("scan_1", stored, stored[:1])
        assert diff.drifted
        assert [sig.issue_type for sig in diff.removed] == ["string_format"]
        assert diff.added == ()

    def test_same_key_with_new_count_is_changed_not_replaced(self) -> None:
        stored = [_issue("missingness", affected=1)]
        rescanned = [_issue("missingness", affected=7)]
        diff = diff_issue_sets("scan_1", stored, rescanned)
        assert diff.drifted
        assert diff.added == ()
        assert diff.removed == ()
        assert len(diff.changed) == 1
        assert diff.changed[0].fields == ("affected_count",)
        assert diff.changed[0].before.affected_count == 1
        assert diff.changed[0].after.affected_count == 7

    def test_severity_change_is_reported_as_its_own_field(self) -> None:
        stored = [_issue("missingness", severity=Severity.MEDIUM)]
        rescanned = [_issue("missingness", severity=Severity.HIGH)]
        diff = diff_issue_sets("scan_1", stored, rescanned)
        assert diff.changed[0].fields == ("severity",)

    def test_key_ignores_column_and_detector_order(self) -> None:
        """顺序不同不是变更：键在建的时候排序, 否则同一条问题会被判成两条。"""
        stored = [_issue("numeric_outlier", ("amount", "id"), ("det_a", "det_b"))]
        rescanned = [_issue("numeric_outlier", ("id", "amount"), ("det_b", "det_a"))]
        diff = diff_issue_sets("scan_1", stored, rescanned)
        assert not diff.drifted
        assert diff.unchanged == 1

    def test_table_name_is_part_of_the_key(self) -> None:
        stored = [_issue("missingness", table="orders")]
        rescanned = [_issue("missingness", table="refunds")]
        diff = diff_issue_sets("scan_1", stored, rescanned)
        assert len(diff.added) == 1
        assert len(diff.removed) == 1

    def test_duplicate_keys_are_counted_not_silently_dropped(self) -> None:
        stored = [_issue("missingness", affected=1), _issue("missingness", affected=2)]
        diff = diff_issue_sets("scan_1", stored, [])
        assert diff.duplicate_keys == 1

    @pytest.mark.parametrize("status", NON_COMPARABLE)
    def test_non_comparable_status_is_never_reported_clean(self, status: RescanStatus) -> None:
        """不可比 ≠ 无差异：drifted 为 False 只因为"没比", 必须同时不可比。"""
        diff = diff_issue_sets("scan_1", [_issue("missingness")], [], status=status)
        assert not diff.comparable
        assert not diff.drifted
        assert diff.note

    def test_empty_batch_markdown_still_has_header(self) -> None:
        assert RescanReport().to_markdown().startswith("| run_id | status |")

    def test_markdown_lists_uncovered_rows_with_reason(self) -> None:
        """不可比的行必须出现在文本输出里, 而不是只在 JSON 里可见。"""
        report = RescanReport(
            rows=(
                IssueSetDiff(run_id="scan_ok"),
                IssueSetDiff(run_id="scan_gone", status="source_missing"),
            )
        )
        rendered = report.to_markdown()
        assert "scan_gone | source_missing (uncovered)" in rendered
        assert "Uncovered:" in rendered
        assert "fixture 不存在" in rendered

    def test_to_dict_carries_status_and_counts(self) -> None:
        diff = diff_issue_sets("scan_1", [], [_issue("missingness")])
        payload = diff.to_dict()
        assert payload["status"] == "compared"
        assert payload["comparable"] is True
        assert payload["drifted"] is True
        added = payload["added"]
        assert isinstance(added, list) and added[0]["issue_type"] == "missingness"

    def test_signature_uses_enum_value_not_repr(self) -> None:
        assert signature(_issue("missingness")).severity == "medium"


class TestRescanRuns:
    """门面层：重扫真实 fixture, 对照 store 里的历史快照。"""

    def test_unchanged_fixture_yields_zero_drift(
        self, scanned: tuple[DataSentry, ScanRun, list[Issue]]
    ) -> None:
        client, scan, issues = scanned
        report = client.rescan_runs()
        assert report.checked == 1
        assert report.comparable == 1
        assert report.drifted == 0
        assert report.uncovered == 0
        row = report.rows[0]
        assert row.run_id == scan.id
        assert row.status == "compared"
        assert row.unchanged == len(issues)

    def test_tampered_history_shows_up_as_drift(
        self, workspace: Path, scanned: tuple[DataSentry, ScanRun, list[Issue]]
    ) -> None:
        """把存档里一条 issue 的影响行数改大, 等价于"检测器现在算出别的数"。"""
        client, scan, _issues = scanned
        with sqlite3.connect(_db(workspace)) as conn:
            conn.execute(
                "UPDATE issues SET affected_count = affected_count + 5 "
                "WHERE scan_run_id = ? AND issue_type = ?",
                (scan.id, "string_format"),
            )
            conn.commit()
        report = client.rescan_runs()
        assert report.drifted == 1
        changed = report.rows[0].changed
        assert len(changed) == 1
        assert changed[0].fields == ("affected_count",)

    def test_changed_source_is_uncovered_not_clean(
        self, workspace: Path, sample_csv: Path, scanned: tuple[DataSentry, ScanRun, list[Issue]]
    ) -> None:
        client, _scan, _issues = scanned
        with sample_csv.open("a", encoding="utf-8") as fh:
            fh.write("9,42,d@x.co\n")
        report = client.rescan_runs()
        assert report.checked == 1
        assert report.comparable == 0
        assert report.uncovered == 1
        assert report.rows[0].status == "source_changed"
        assert report.drifted == 0

    def test_missing_fixture_is_uncovered(
        self, sample_csv: Path, scanned: tuple[DataSentry, ScanRun, list[Issue]]
    ) -> None:
        client, _scan, _issues = scanned
        sample_csv.unlink()
        report = client.rescan_runs()
        assert report.rows[0].status == "source_missing"
        assert report.uncovered == 1

    def test_remote_source_is_refused_without_touching_it(
        self, workspace: Path, scanned: tuple[DataSentry, ScanRun, list[Issue]]
    ) -> None:
        """DSN 不得被重扫：那等于对生产库再跑一次查询。"""
        client, scan, _issues = scanned
        with sqlite3.connect(_db(workspace)) as conn:
            conn.execute(
                "UPDATE scan_runs SET source_path = ? WHERE id = ?",
                ("postgresql://user@localhost:5432/prod", scan.id),
            )
            conn.commit()
        report = client.rescan_runs()
        assert report.rows[0].status == "source_unsupported"
        assert "远程源" in report.rows[0].note

    def test_incomplete_run_is_not_a_baseline(
        self, workspace: Path, scanned: tuple[DataSentry, ScanRun, list[Issue]]
    ) -> None:
        """失败 run 的 issue 集本身不完整, 拿它当基线会把旧缺陷记成"新检出"。"""
        client, scan, _issues = scanned
        with sqlite3.connect(_db(workspace)) as conn:
            conn.execute("UPDATE scan_runs SET status = 'failed' WHERE id = ?", (scan.id,))
            conn.commit()
        report = client.rescan_runs()
        assert report.rows[0].status == "run_incomplete"
        assert report.uncovered == 1

    def test_run_id_selection_and_limit(self, workspace: Path, sample_csv: Path) -> None:
        client = DataSentry(project=workspace)
        try:
            first, _runs, _issues = client.scan_file(sample_csv)
            second, _runs2, _issues2 = client.scan_file(sample_csv, dataset_id="other")
            assert [row.run_id for row in client.rescan_runs(run_ids=[first.id]).rows] == [first.id]
            assert client.rescan_runs(limit=1).rows[0].run_id == second.id
            with pytest.raises(KeyError):
                client.rescan_runs(run_ids=["scan_does_not_exist"])
            with pytest.raises(ValueError):
                client.rescan_runs(limit=0)
        finally:
            client.close()


class TestRescanCli:
    def test_no_scans_exits_config(
        self, workspace: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        code = main(["--project", str(workspace), "--format", "json", "rescan"])
        assert code == EXIT_CONFIG
        assert json.loads(capsys.readouterr().out)["data"]["error"] == "no scan runs yet"

    def test_archives_diff_table_and_exits_zero(
        self,
        workspace: Path,
        scanned: tuple[DataSentry, ScanRun, list[Issue]],
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        _client, scan, _issues = scanned
        code = main(["--project", str(workspace), "--format", "json", "rescan"])
        assert code == EXIT_OK
        data = json.loads(capsys.readouterr().out)["data"]
        assert data["checked"] == 1
        assert data["comparable"] == 1
        assert data["drifted"] == 0
        assert data["rows"][0]["run_id"] == scan.id
        archive = Path(data["archive"])
        assert archive.is_file()
        assert archive.parent == _archive(workspace)
        assert json.loads(archive.read_text(encoding="utf-8"))["checked"] == 1

    def test_output_override_writes_where_told(
        self,
        tmp_path: Path,
        workspace: Path,
        scanned: tuple[DataSentry, ScanRun, list[Issue]],
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        target = tmp_path / "issue-diff.json"
        code = main(["--project", str(workspace), "rescan", "--output", str(target)])
        assert code == EXIT_OK
        out = capsys.readouterr().out
        assert "checked 1" in out
        assert "archive:" in out
        assert json.loads(target.read_text(encoding="utf-8"))["uncovered"] == 0

    def test_fail_on_drift_turns_red_after_issue_tamper(
        self,
        workspace: Path,
        scanned: tuple[DataSentry, ScanRun, list[Issue]],
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        _client, scan, _issues = scanned
        with sqlite3.connect(_db(workspace)) as conn:
            conn.execute(
                "UPDATE issues SET severity = ? WHERE scan_run_id = ? AND issue_type = ?",
                (Severity.CRITICAL.value, scan.id, "missingness"),
            )
            conn.commit()
        assert main(["--project", str(workspace), "rescan"]) == EXIT_OK
        capsys.readouterr()
        code = main(["--project", str(workspace), "rescan", "--fail-on-drift"])
        assert code == EXIT_GATE_FAILED

    def test_limit_zero_is_a_config_error(
        self, workspace: Path, scanned: tuple[DataSentry, ScanRun, list[Issue]]
    ) -> None:
        assert main(["--project", str(workspace), "rescan", "--limit", "0"]) == EXIT_CONFIG


def test_report_totals_add_up() -> None:
    report = RescanReport(
        rows=(IssueSetDiff(run_id="a"), IssueSetDiff(run_id="b", status="source_missing"))
    )
    assert report.checked == 2
    assert report.comparable == 1
    assert report.uncovered == 1
    assert report.drifted == 0
