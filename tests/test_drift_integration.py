"""Step 39 漂移引擎集成测试：client.drift_compare / drift_latest / CLI。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from datasentry import DataSentry
from datasentry.cli import main


def _write_csv(path: Path, rows: list[str]) -> None:
    path.write_text("\n".join(rows), encoding="utf-8")


class TestDriftThroughClient:
    def test_drift_compare_two_scans(self, tmp_path: Path) -> None:
        data = tmp_path / "orders.csv"
        _write_csv(data, ["id,amount\n1,10.0\n2,20.0\n3,30.0\n"])
        client = DataSentry(project=tmp_path / "ws")
        try:
            scan_a, _, _ = client.scan_file(data)
            scan_b, _, _ = client.scan_file(data)
            report = client.drift_compare(scan_a.id, scan_b.id)
            assert report.reference_dataset_id == "orders"
            assert report.current_dataset_id == "orders"
            assert report.schema_changes == []
            assert report.column_drifts == []
        finally:
            client.close()

    def test_drift_compare_missing_run_raises(self, tmp_path: Path) -> None:
        client = DataSentry(project=tmp_path / "ws")
        try:
            with pytest.raises(KeyError):
                client.drift_compare("missing_a", "missing_b")
        finally:
            client.close()

    def test_drift_latest_requires_two_scans(self, tmp_path: Path) -> None:
        data = tmp_path / "orders.csv"
        _write_csv(data, ["id,amount\n1,10.0\n"])
        client = DataSentry(project=tmp_path / "ws")
        try:
            client.scan_file(data)
            with pytest.raises(ValueError):
                client.drift_latest("orders")
        finally:
            client.close()

    def test_drift_latest_detects_row_growth(self, tmp_path: Path) -> None:
        data = tmp_path / "orders.csv"
        _write_csv(data, ["id,amount\n1,10.0\n2,20.0\n"])
        client = DataSentry(project=tmp_path / "ws")
        try:
            client.scan_file(data)
            _write_csv(data, ["id,amount\n1,10.0\n2,20.0\n3,30.0\n4,40.0\n5,50.0\n6,60.0\n"])
            client.scan_file(data)
            report = client.drift_latest("orders", row_ratio_threshold=0.5)
            row_drift = next(d for d in report.column_drifts if d.metric == "row_count")
            assert row_drift.direction == "increase"
            assert row_drift.sample_sizes == (2, 6)
        finally:
            client.close()


class TestDriftCli:
    def test_drift_compare_cli_json(self, tmp_path: Path, capsys) -> None:
        data = tmp_path / "orders.csv"
        _write_csv(data, ["id,amount\n1,10.0\n2,20.0\n"])
        client = DataSentry(project=tmp_path / "ws")
        scan_a, _, _ = client.scan_file(data)
        scan_b, _, _ = client.scan_file(data)
        client.close()
        code = main(
            [
                "--project",
                str(tmp_path / "ws"),
                "--format",
                "json",
                "drift",
                "compare",
                scan_a.id,
                scan_b.id,
            ]
        )
        assert code == 0
        payload = json.loads(capsys.readouterr().out)
        assert payload["data"]["schema_changes"] == []
        assert payload["data"]["column_drifts"] == []
        assert payload["data"]["drift_report_id"].startswith("drift_")

    def test_drift_latest_cli_insufficient_scans(self, tmp_path: Path, capsys) -> None:
        data = tmp_path / "orders.csv"
        _write_csv(data, ["id,amount\n1,10.0\n"])
        client = DataSentry(project=tmp_path / "ws")
        client.scan_file(data)
        client.close()
        code = main(
            [
                "--project",
                str(tmp_path / "ws"),
                "--format",
                "json",
                "drift",
                "latest",
                "orders",
            ]
        )
        assert code == 2
        payload = json.loads(capsys.readouterr().out)
        assert "fewer than 2 completed scans" in payload["data"]["error"]
