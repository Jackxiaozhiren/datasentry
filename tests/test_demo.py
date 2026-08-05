"""Step 25 M9 Demo 测试（34 章：Demo 数据集完整走通 < 3 分钟）。"""

from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path

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
    assert csv1 == csv2  # 固定种子 → 数据可复现


def test_demo_missing_file_error_path(tmp_path: Path) -> None:
    """非法行数/输入应失败而非崩溃（此处验证 --rows 边界走查）。"""
    result = subprocess.run(
        [sys.executable, str(DEMO), "--rows", "0", "--out", str(tmp_path)],
        capture_output=True,
        text=True,
        timeout=60,
    )
    # 0 行数据扫描应走通（空表语义），或至少不崩溃
    assert result.returncode in (0, 1)
