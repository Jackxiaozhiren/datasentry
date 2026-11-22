"""R-15: four wall-clock stages on the run record, with byte-identical default output.

The four stages are read / profile / detect / score. ``ScanRun.stage_timings_ms`` is
``exclude=True`` so the default JSON/text output does not gain a field; the store keeps
them in a dedicated ``scan_runs.stage_timings`` column (schema v11 -> v12) and reads them
back on reload. A pre-v12 row with NULL reads back as ``{}`` ("not recorded", not "zero").
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from datasentry import DataSentry
from datasentry.cli import main

_STAGES = ("read", "profile", "detect", "score")


def _csv(ws: Path) -> Path:
    ws.mkdir(parents=True, exist_ok=True)
    csv = ws / "c.csv"
    csv.write_text("a,b\n1,x\n2,y\n3,z\n", encoding="utf-8")
    return csv


class TestStageTimings:
    def test_scan_run_records_four_stage_wall_clocks(self, tmp_path: Path) -> None:
        csv = _csv(tmp_path / "ws")
        client = DataSentry(project=csv.parent)
        try:
            scan_run, _, _ = client.scan_file(csv)
        finally:
            client.close()
        timings = scan_run.stage_timings_ms
        assert set(timings) == set(_STAGES), timings
        assert all(isinstance(v, int) and v >= 0 for v in timings.values()), timings

    def test_timings_survive_a_database_reload(self, tmp_path: Path) -> None:
        csv = _csv(tmp_path / "ws")
        client = DataSentry(project=csv.parent)
        try:
            original, _, _ = client.scan_file(csv)
            reloaded = next(r for r in client.list_scan_runs() if r.id == original.id)
        finally:
            client.close()
        assert set(reloaded.stage_timings_ms) == set(_STAGES), reloaded.stage_timings_ms
        assert reloaded.stage_timings_ms == original.stage_timings_ms

    def test_timings_are_excluded_from_model_dump(self, tmp_path: Path) -> None:
        csv = _csv(tmp_path / "ws")
        client = DataSentry(project=csv.parent)
        try:
            scan_run, _, _ = client.scan_file(csv)
        finally:
            client.close()
        assert set(scan_run.stage_timings_ms) == set(_STAGES), "a missing record makes this vacuous"
        assert "stage_timings_ms" not in scan_run.model_dump(), "default output bytes would change"
        assert "stage_timings_ms" not in scan_run.model_dump_json()

    def test_default_scan_output_has_no_timing_field(self, tmp_path: Path, capsys: Any) -> None:
        csv = _csv(tmp_path / "ws")
        assert main(["--project", str(csv.parent), "--format", "json", "scan", str(csv)]) == 0
        payload = json.loads(capsys.readouterr().out)
        assert "stage_timings_ms" not in json.dumps(payload)
        assert "stage_timings" not in json.dumps(payload)
        # Denominator guard: the run really has timings, so "absent from output" is meaningful.
        client = DataSentry(project=csv.parent)
        try:
            runs = client.list_scan_runs()
            assert runs and set(runs[0].stage_timings_ms) == set(_STAGES)
        finally:
            client.close()

    def test_rest_scan_detail_has_no_timing_field(self, tmp_path: Path) -> None:
        """The byte ratchet covers REST too: ``GET /scans/{id}`` is an outward face."""
        from fastapi.testclient import TestClient

        from datasentry.api import create_app

        csv = _csv(tmp_path / "ws")
        http = TestClient(create_app(project=csv.parent))
        created = http.post("/scans", json={"path": str(csv)})
        assert created.status_code == 201, created.text
        run_id = created.json()["run"]["id"]
        detail = http.get(f"/scans/{run_id}")
        assert detail.status_code == 200
        assert "stage_timings" not in detail.text, "the REST face leaked the new field"

    def test_injected_timing_in_default_output_would_be_caught(self) -> None:
        """Counter-proof: the byte ratchet is not vacuous."""
        payload = {"data": {"scan_run_id": "scan_x", "stage_timings_ms": {"read": 1}}}
        assert "stage_timings_ms" in json.dumps(payload)
        with pytest.raises(AssertionError):
            assert "stage_timings_ms" not in json.dumps(payload)
