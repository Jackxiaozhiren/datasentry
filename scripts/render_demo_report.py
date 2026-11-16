#!/usr/bin/env python3
"""`docs/demo/orders-report.html` 的唯一生成入口。

为什么需要这个脚本：README 与 Pages 把这份 HTML 当作"当前渲染器的产物"。
此前它是手工保存的快照，停在 `report_schema_version 1.0` / `datasentry_version 0.1.0`
的旧版式上——没有导航、没有 Column Profiles、没有 Repair Record，而文档仍指向它。
快照本身没问题，问题是没有生成路径，谁也不知道它代表哪个版本。

用法：

    uv run python scripts/render_demo_report.py            # 重写 docs/demo/orders-report.html
    uv run python scripts/render_demo_report.py --out /tmp/x.html

数据固定取 `demo-data/orders.csv`（`record-demo.sh --csv` 的确定性产物，seed 42），
所以每次都扫同一批 200 行。**产物不是逐字节可复现的**：`scan_run_id`、`generated_at`
与各 detector 的 `duration_ms` 都随运行变化，因此 `tests/test_reporting_html.py`
只钉结构性判据（viewport / 表格包裹 / 无外链 / 章节存在），不比对全文。
"""

from __future__ import annotations

import argparse
import contextlib
import io
import sys
import tempfile
from pathlib import Path

from datasentry import DataSentry
from datasentry.cli import EXIT_OK, main

REPO = Path(__file__).resolve().parents[1]
DEFAULT_CSV = REPO / "demo-data" / "orders.csv"
DEFAULT_OUT = REPO / "docs" / "demo" / "orders-report.html"


def render(csv: Path, out: Path) -> dict[str, object]:
    """扫一遍、修一条、导出 HTML。返回给终端看的摘要。"""
    if not csv.is_file():
        raise SystemExit(f"missing demo data: {csv}\nrun: bash docs/demo/record-demo.sh --csv")

    with tempfile.TemporaryDirectory(prefix="ds-demo-report-") as tmp:
        ws = Path(tmp) / "ws"
        ws.mkdir()
        client = DataSentry(project=ws)
        try:
            scan, _, issues = client.scan_file(csv)
            fixable = next(
                (i for i in issues if "leading_or_trailing_whitespace" in i.detector_ids),
                None,
            )
            run = client.repair_apply(fixable.id, csv) if fixable is not None else None
        finally:
            client.close()

        out.parent.mkdir(parents=True, exist_ok=True)
        # CLI 会把 JSON envelope 打到 stdout；这里只要文件，摘要由本脚本自己打印。
        with contextlib.redirect_stdout(io.StringIO()):
            code = main(
                [
                    "--project",
                    str(ws),
                    "--format",
                    "json",
                    "report",
                    "export",
                    scan.id,
                    "--as",
                    "html",
                    "--output",
                    str(out),
                ]
            )
        if code != EXIT_OK:
            raise SystemExit(f"report export failed with exit code {code}")

    return {
        "scan_id": scan.id,
        "issues": len(issues),
        "repair_run": run.id if run is not None else None,
        "rows_changed": run.rows_changed if run is not None else None,
        "bytes": out.stat().st_size,
        "path": str(out),
    }


def main_cli(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="render the committed demo report")
    parser.add_argument("--csv", type=Path, default=DEFAULT_CSV)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    args = parser.parse_args(argv)

    summary = render(args.csv, args.out)
    print(
        "wrote {path} ({bytes} bytes) from scan {scan_id}: "
        "{issues} issues, repair {repair_run} changed {rows_changed} rows".format(**summary)
    )
    if summary["repair_run"] is None:
        print("warning: no fixable issue found; the Repair Record section will be absent")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main_cli())
