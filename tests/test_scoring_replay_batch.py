"""O-D5-01 接线（AC-11a）：批量重放、差异表归档与 ``replay --fail-on-drift`` 门禁。

覆盖三层：
- 核心 ``replay_runs``：选择方式、未评分 run 显式跳过、漂移检出、序列化；
- 门面 ``DataSentry.replay_scores``：读的是 store 里的历史快照；
- CLI：归档落点、``--output``、退出码 2/1/0，以及门禁**确实会红**（篡改存档分数）。
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterator
from pathlib import Path
from typing import cast

import pytest

from datasentry import DataSentry
from datasentry.cli import EXIT_CONFIG, EXIT_GATE_FAILED, EXIT_OK, main
from datasentry_core.models.issue import Issue
from datasentry_core.models.scan import ScanRun
from datasentry_core.scoring.replay import ReplayReport, replay_runs


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


class _StubSource:
    """最小 ReplaySource：只实现 replay_runs 实际调用的三个方法。"""

    def __init__(self, runs: list[ScanRun], issues: dict[str, list[Issue]]) -> None:
        self._runs = runs
        self._issues = issues

    def list_scan_runs(self) -> list[ScanRun]:
        return self._runs

    def get_scan_run(self, scan_run_id: str) -> ScanRun | None:
        return next((r for r in self._runs if r.id == scan_run_id), None)

    def get_issues(self, scan_run_id: str) -> list[Issue]:
        return self._issues[scan_run_id]


def _tampered(scan: ScanRun) -> ScanRun:
    """压掉一个维度并下调总分，模拟「分数由另一套评分代码产出」。"""
    assert scan.quality_score is not None
    quality = scan.quality_score.model_copy(deep=True)
    quality.dimensions["completeness"] = 0.0
    quality.overall = max(0.0, quality.overall - 20.0)
    return scan.model_copy(update={"quality_score": quality})


def _archive(workspace: Path) -> Path:
    return workspace / ".datasentry" / "replay"


class TestBatchReplay:
    def test_scored_run_replays_with_zero_drift(
        self, scanned: tuple[DataSentry, ScanRun, list[Issue]]
    ) -> None:
        _client, scan, issues = scanned
        report = replay_runs(_StubSource([scan], {scan.id: issues}))
        assert report.checked == 1
        assert report.replayable == 1
        assert report.drifted == 0
        row = report.rows[0]
        assert row.run_id == scan.id
        assert row.delta == pytest.approx(0.0, abs=1e-9)
        assert row.recorded_score_version == row.current_score_version

    def test_unscored_run_is_skipped_with_reason(
        self, scanned: tuple[DataSentry, ScanRun, list[Issue]]
    ) -> None:
        _client, scan, _issues = scanned
        unscored = scan.model_copy(update={"quality_score": None, "status": "failed"})
        report = replay_runs(_StubSource([unscored], {}))
        assert report.rows == ()
        assert report.checked == 1
        assert report.replayable == 0
        assert report.drifted == 0
        assert report.skipped[0][0] == scan.id
        assert "status=failed" in report.skipped[0][1]

    def test_limit_takes_most_recent_runs(
        self, scanned: tuple[DataSentry, ScanRun, list[Issue]]
    ) -> None:
        _client, scan, issues = scanned
        older = scan.model_copy(update={"id": "run-older"})
        report = replay_runs(
            _StubSource([scan, older], {scan.id: issues, older.id: issues}), limit=1
        )
        assert [row.run_id for row in report.rows] == [scan.id]

    def test_explicit_run_ids_preserve_given_order(
        self, scanned: tuple[DataSentry, ScanRun, list[Issue]]
    ) -> None:
        _client, scan, issues = scanned
        other = scan.model_copy(update={"id": "run-other"})
        report = replay_runs(
            _StubSource([scan, other], {scan.id: issues, other.id: issues}),
            run_ids=["run-other", scan.id],
        )
        assert [row.run_id for row in report.rows] == ["run-other", scan.id]

    def test_unknown_run_id_raises_key_error(
        self, scanned: tuple[DataSentry, ScanRun, list[Issue]]
    ) -> None:
        _client, scan, issues = scanned
        source = _StubSource([scan], {scan.id: issues})
        with pytest.raises(KeyError, match="scan run not found"):
            replay_runs(source, run_ids=["missing"])

    def test_limit_must_be_positive(self, scanned: tuple[DataSentry, ScanRun, list[Issue]]) -> None:
        _client, scan, issues = scanned
        with pytest.raises(ValueError, match="limit must be >= 1"):
            replay_runs(_StubSource([scan], {scan.id: issues}), limit=0)

    def test_tampered_recorded_score_is_reported_as_drift(
        self, scanned: tuple[DataSentry, ScanRun, list[Issue]]
    ) -> None:
        _client, scan, issues = scanned
        report = replay_runs(_StubSource([_tampered(scan)], {scan.id: issues}))
        assert report.drifted == 1
        row = report.rows[0]
        assert row.exceeds()
        assert "completeness" in row.changed_dimensions

    def test_payload_is_json_serializable_and_counts_add_up(
        self, scanned: tuple[DataSentry, ScanRun, list[Issue]]
    ) -> None:
        _client, scan, issues = scanned
        unscored = scan.model_copy(update={"id": "run-unscored", "quality_score": None})
        report = replay_runs(
            _StubSource([_tampered(scan), unscored], {scan.id: issues}),
        )
        payload = report.to_dict()
        text = json.dumps(payload, ensure_ascii=False)
        assert json.loads(text)["checked"] == 2
        assert payload["checked"] == 2
        assert payload["replayable"] == 1
        assert payload["drifted"] == 1
        skipped = cast("list[dict[str, str]]", payload["skipped"])
        assert skipped[0]["run_id"] == "run-unscored"
        rows = cast("list[dict[str, object]]", payload["rows"])
        assert rows[0]["drifted"] is True

    def test_markdown_marks_version_change_without_drift(
        self, scanned: tuple[DataSentry, ScanRun, list[Issue]]
    ) -> None:
        """版本号变了但公式没变：delta=0 也要在表里显式标出，不能当成"什么都没发生"。"""
        _client, scan, issues = scanned
        assert scan.quality_score is not None
        quality = scan.quality_score.model_copy(deep=True)
        quality.score_version = "0"
        old = scan.model_copy(update={"quality_score": quality})
        report = replay_runs(_StubSource([old], {scan.id: issues}))
        row = report.rows[0]
        assert row.version_changed is True
        assert row.exceeds() is False
        assert "0 -> 1" in report.to_markdown()

    def test_markdown_table_lists_rows_and_skips(
        self, scanned: tuple[DataSentry, ScanRun, list[Issue]]
    ) -> None:
        _client, scan, issues = scanned
        report = ReplayReport(
            rows=replay_runs(_StubSource([scan], {scan.id: issues})).rows,
            skipped=(("run-x", "no score"),),
        )
        text = report.to_markdown()
        assert "| run_id | recorded | replayed | delta |" in text
        assert scan.id in text
        assert "Skipped:" in text
        assert "run-x: no score" in text


class TestReplayCli:
    def test_archives_diff_table_and_exits_zero(
        self,
        workspace: Path,
        scanned: tuple[DataSentry, ScanRun, list[Issue]],
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        _client, scan, _issues = scanned
        code = main(["--project", str(workspace), "--format", "json", "replay"])
        assert code == EXIT_OK
        payload = json.loads(capsys.readouterr().out)
        data = payload["data"]
        assert data["checked"] == 1
        assert data["drifted"] == 0
        assert data["rows"][0]["run_id"] == scan.id
        archive = Path(data["archive"])
        assert archive.is_file()
        assert archive.parent == _archive(workspace)
        assert (
            json.loads(archive.read_text(encoding="utf-8"))["score_version"]
            == data["score_version"]
        )

    def test_output_override_writes_where_told(
        self,
        tmp_path: Path,
        workspace: Path,
        scanned: tuple[DataSentry, ScanRun, list[Issue]],
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        target = tmp_path / "diff.json"
        code = main(
            ["--project", str(workspace), "replay", "--output", str(target)],
        )
        assert code == EXIT_OK
        out = capsys.readouterr().out
        assert "checked 1" in out
        assert "archive:" in out
        assert json.loads(target.read_text(encoding="utf-8"))["checked"] == 1

    def test_fail_on_drift_turns_red_after_score_tamper(
        self,
        workspace: Path,
        scanned: tuple[DataSentry, ScanRun, list[Issue]],
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        _client, scan, _issues = scanned
        db = workspace / ".datasentry" / "metadata.db"
        with sqlite3.connect(db) as conn:
            raw = conn.execute(
                "SELECT quality_breakdown FROM scan_runs WHERE id = ?", (scan.id,)
            ).fetchone()
            score = json.loads(raw[0])
            score["dimensions"]["completeness"] = 0.0
            score["overall"] = max(0.0, score["overall"] - 20.0)
            conn.execute(
                "UPDATE scan_runs SET quality_breakdown = ?, quality_score = ? WHERE id = ?",
                (json.dumps(score), score["overall"], scan.id),
            )
            conn.commit()

        assert main(["--project", str(workspace), "replay"]) == EXIT_OK
        capsys.readouterr()
        code = main(["--project", str(workspace), "replay", "--fail-on-drift"])
        out = capsys.readouterr().out
        assert code == EXIT_GATE_FAILED
        assert "drifted 1" in out

    def test_no_runs_exits_config(
        self, workspace: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        code = main(["--project", str(workspace), "replay"])
        assert code == EXIT_CONFIG
        assert "no scan runs yet" in capsys.readouterr().out

    def test_zero_limit_is_rejected(
        self, workspace: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        code = main(["--project", str(workspace), "replay", "--limit", "0"])
        assert code == EXIT_CONFIG
        assert "--limit must be >= 1" in capsys.readouterr().out
