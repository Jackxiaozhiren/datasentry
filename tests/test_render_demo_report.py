"""样例生成器能跑通，且不会就地改动 demo 数据。

`docs/demo/orders-report.html` 的可信度完全依赖这个入口：它一旦失效，样例要么
停止更新（退回手写快照），要么由某次手工导出覆盖。这里跑一次真实生成（约 3 s），
钉三件事：脚本退出 0；产物含导航/画像/修复记录三节；源 CSV 逐字节未变
（不变量 3——修复只写副本，永不覆盖源文件）。
"""

from __future__ import annotations

import hashlib
import subprocess
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]
_SCRIPT = _REPO / "scripts" / "render_demo_report.py"
_CSV = _REPO / "demo-data" / "orders.csv"


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_generator_writes_a_report_and_leaves_the_source_alone(tmp_path: Path) -> None:
    before = _digest(_CSV)
    out = tmp_path / "orders-report.html"
    proc = subprocess.run(
        [sys.executable, str(_SCRIPT), "--out", str(out)],
        cwd=_REPO,
        capture_output=True,
        text=True,
        timeout=180,
        check=False,
    )
    assert proc.returncode == 0, f"generator failed: {proc.stderr[-800:]}"
    assert out.is_file(), "生成器没写出文件"

    html = out.read_text(encoding="utf-8")
    for marker in ('id="report-nav"', 'id="profiles"', 'id="repair_record"'):
        assert marker in html, f"生成物缺少 {marker}"
    assert 'name="viewport"' in html
    assert html.count("<table") == html.count('<div class="table-scroll">'), (
        "生成物里有表格没被滚动容器包住"
    )
    assert _digest(_CSV) == before, "修复步骤改动了源 CSV (不变量 3)"
    assert str(out) in proc.stdout, "生成器应报告落盘路径"
