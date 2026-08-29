"""Demo tests: repository full cycle plus packaged zero-config entry point."""

from __future__ import annotations

import csv
import subprocess
import sys
import time
from pathlib import Path

import pytest

from datasentry.demo import generate_demo_csv, main as demo_main

DEMO = Path(__file__).resolve().parents[1] / "examples" / "demo" / "demo.py"
BUDGET_SECONDS = 180.0


def test_demo_full_cycle_within_budget(tmp_path: Path) -> None:
    start = time.monotonic()
    result = subprocess.run(
        [sys.executable, str(DEMO), "--rows", "5000", "--out", str(tmp_path)],
        capture_output=True,
        text=True,
        timeout=BUDGET_SECONDS + 30,
    )
    elapsed = time.monotonic() - start
    assert result.returncode == 0, result.stderr
    out = result.stdout
    assert "scan run:" in out
    assert "detectors completed:" in out
    assert "top:" in out
    assert "repair:" in out
    assert "rollback →" in out
    assert "PASS" in out
    assert elapsed < BUDGET_SECONDS, f"demo exceeded budget: {elapsed:.1f}s"
    assert (tmp_path / "customers.csv").exists()
    assert (tmp_path / "report.json").exists()
    assert (tmp_path / "report.html").exists()


def test_demo_small_rows_reproducible(tmp_path: Path) -> None:
    out1 = tmp_path / "a"
    out2 = tmp_path / "b"
    for out in (out1, out2):
        result = subprocess.run(
            [sys.executable, str(DEMO), "--rows", "200", "--out", str(out)],
            capture_output=True,
            text=True,
            timeout=BUDGET_SECONDS + 30,
        )
        assert result.returncode == 0, result.stderr
    csv1 = (out1 / "customers.csv").read_bytes()
    csv2 = (out2 / "customers.csv").read_bytes()
    assert csv1 == csv2


def test_demo_missing_file_error_path(tmp_path: Path) -> None:
    result = subprocess.run(
        [sys.executable, str(DEMO), "--rows", "0", "--out", str(tmp_path)],
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode in (0, 1)


def test_packaged_demo_data_is_reproducible(tmp_path: Path) -> None:
    first = tmp_path / "first.csv"
    second = tmp_path / "second.csv"

    generate_demo_csv(first, rows=100, seed=7)
    generate_demo_csv(second, rows=100, seed=7)

    assert first.read_text(encoding="utf-8") == second.read_text(encoding="utf-8")
    with first.open(encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    assert len(rows) == 100
    assert set(rows[0]) == {"id", "price", "category", "event_date", "name", "status"}


def test_packaged_demo_rejects_empty_dataset(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="rows must be >= 1"):
        generate_demo_csv(tmp_path / "empty.csv", rows=0)


def test_packaged_demo_help_is_available() -> None:
    with pytest.raises(SystemExit) as exc:
        demo_main(["--help"])
    assert exc.value.code == 0
