"""Step 34 电商场景示例测试：预算硬约束 + PASS 契约 + 产物存在性。"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

SHOWCASE = Path(__file__).resolve().parents[1] / "examples" / "ecommerce" / "run_showcase.py"


@pytest.mark.parametrize("rows", [500, 2000])
def test_showcase_passes_and_produces_artifacts(rows: int, tmp_path: Path) -> None:
    out = tmp_path / "out"
    result = subprocess.run(
        [
            sys.executable,
            str(SHOWCASE),
            "--rows",
            str(rows),
            "--out",
            str(out),
        ],
        capture_output=True,
        text=True,
        timeout=300,
    )
    output = result.stdout + result.stderr
    assert result.returncode == 0, f"showcase failed:\n{output}"
    assert "→ PASS" in output, f"showcase did not pass:\n{output}"
    assert "gate(orders) --fail-on high: passed=False" in output
    assert "gate(orders-after-repair) --fail-on high: passed=True" in output
    assert "budget" in output
    for name in (
        "orders.csv",
        "customers.csv",
        "report-orders.html",
        "report-customers.html",
        "report-orders-after-repair.html",
    ):
        assert (out / name).is_file(), f"missing artifact: {name}"


def test_showcase_reproducible_with_seed(tmp_path: Path) -> None:
    args = [
        sys.executable,
        str(SHOWCASE),
        "--rows",
        "300",
        "--seed",
        "7",
    ]
    first = subprocess.run(args, capture_output=True, text=True, timeout=300)
    second = subprocess.run(args, capture_output=True, text=True, timeout=300)
    assert first.returncode == 0 and second.returncode == 0

    def norm(output: str) -> str:
        return "\n".join(
            line
            for line in output.splitlines()
            if "workspace:" not in line
            and "run=" not in line
            and not line.startswith(("step timings:", "total:"))
        )

    assert norm(first.stdout) == norm(second.stdout), "same seed must produce identical output"
