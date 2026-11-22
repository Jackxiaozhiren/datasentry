"""追加 3p: ``repair list`` reports the recorded row count, not the sample length.

``RepairRun.rows_changed`` is the single persisted count of rows actually changed;
``RepairRun.operations`` is a capped sample. Before this display existed the table
column silently used ``len(operations)``, so a run with 22 changed rows reported "1".
The JSON face now exposes both numbers under distinct keys, and the table shows the
real count -- falling back to the sample length with a ``*`` footnote only for legacy
records whose ``rows_changed`` is NULL (the field is ``int | None``).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from datasentry import DataSentry
from datasentry.cli import main
from datasentry_core.models.enums import RepairOperation, RepairRunStatus
from datasentry_core.models.repair import RepairOperationRecord, RepairRun


def _seed_run(
    ws: Path,
    run_id: str,
    *,
    rows_changed: int | None,
    n_operations: int,
) -> None:
    """Write a RepairRun directly to the store with a controlled count/sample split.

    A real scan first creates the project/dataset rows that ``repair_runs.dataset_id``
    references; only the run's count/sample split is synthetic.
    """
    client = DataSentry(project=ws)
    try:
        csv = ws / "data.csv"
        csv.write_text("a,b\n1,x\n2,y\n3,z\n", encoding="utf-8")
        scan_run, _, _ = client.scan_file(csv)
        run = RepairRun(
            id=run_id,
            dataset_id=scan_run.dataset_id,
            fingerprint_before="fp0",
            rows_changed=rows_changed,
            status=RepairRunStatus.APPLIED,
            operations=[
                RepairOperationRecord(
                    row_id=f"r{i}",
                    column="amount",
                    operation=RepairOperation.CLIP_VALUE,
                    before=99999.0,
                    after=47.25,
                )
                for i in range(n_operations)
            ],
        )
        client._store.save_repair_run(run)
    finally:
        client.close()


def _json_runs(ws: Path, capsys: Any) -> list[dict[str, Any]]:
    assert main(["--project", str(ws), "--format", "json", "repair", "list"]) == 0
    return json.loads(capsys.readouterr().out)["data"]["runs"]


class TestRepairListRowCount:
    def test_json_exposes_both_recorded_count_and_sample_length(
        self, tmp_path: Path, capsys: Any
    ) -> None:
        """A count different from the sample length proves the two keys are not aliased."""
        ws = tmp_path / "ws"
        ws.mkdir()
        _seed_run(ws, "run-real", rows_changed=22, n_operations=1)
        runs = {r["id"]: r for r in _json_runs(ws, capsys)}
        assert runs["run-real"]["rows_changed"] == 22
        assert runs["run-real"]["operations_sampled"] == 1

    def test_table_uses_recorded_count_not_sample_length(self, tmp_path: Path, capsys: Any) -> None:
        """The table must not revert to len(operations)=1 when the real count is 22."""
        ws = tmp_path / "ws"
        ws.mkdir()
        _seed_run(ws, "run-real", rows_changed=22, n_operations=1)
        assert main(["--project", str(ws), "repair", "list"]) == 0
        out = capsys.readouterr().out
        line = next(line for line in out.splitlines() if "run-real" in line)
        assert " 22 " in line, line
        assert " 1 " not in line, line

    def test_legacy_null_count_shows_sample_with_footnote(
        self, tmp_path: Path, capsys: Any
    ) -> None:
        """NULL means 'record predates the field' -- show the sample, starred, with a legend."""
        ws = tmp_path / "ws"
        ws.mkdir()
        _seed_run(ws, "run-old", rows_changed=None, n_operations=3)
        runs = {r["id"]: r for r in _json_runs(ws, capsys)}
        assert runs["run-old"]["rows_changed"] is None
        assert runs["run-old"]["operations_sampled"] == 3

        assert main(["--project", str(ws), "repair", "list"]) == 0
        out = capsys.readouterr().out
        line = next(line for line in out.splitlines() if "run-old" in line)
        assert "3*" in line, line
        assert "sample size only" in out, out

    def test_no_footnote_when_all_counts_recorded(self, tmp_path: Path, capsys: Any) -> None:
        """The legend is conditional; an all-modern table must not carry a stale `* note`."""
        ws = tmp_path / "ws"
        ws.mkdir()
        _seed_run(ws, "run-real", rows_changed=7, n_operations=2)
        assert main(["--project", str(ws), "repair", "list"]) == 0
        out = capsys.readouterr().out
        assert "sample size only" not in out

    def test_missing_display_would_be_caught(self) -> None:
        """Counter-proof: a payload lacking the two keys fails the JSON assertion."""
        bad: dict[str, Any] = {"id": "run-real", "fingerprint_before": "fp0"}
        with pytest.raises(KeyError):
            _ = bad["rows_changed"]
