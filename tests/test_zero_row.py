"""D2-01 回归：零行 CSV（仅表头）不再让空值检测器崩溃。

DuckDB 对 0 行做 sum(x IS NULL) 返回 NULL；旧代码 int(None) 抛 TypeError，
整次扫描 status=failed 却仍 exit 0 + quality_score 100。修复后检测器返回
空候选，扫描 completed，CLI 非门禁路径保持 exit 0。
"""

from __future__ import annotations

import hashlib
from pathlib import Path

from datasentry_core.connectors import CsvConnector, DataSourceSpec, DataSourceType
from datasentry_core.detectors import DetectionContext
from datasentry_core.detectors.initial.missing import ExcessiveNullRateDetector
from datasentry_core.detectors.missingness import _column_nulls


def _context(tmp_path: Path) -> DetectionContext:
    p = tmp_path / "hdr_only.csv"
    p.write_text("a,b\n", encoding="utf-8")
    spec = DataSourceSpec(source_type=DataSourceType.CSV, path=p, options={"dataset_id": "t"})
    handle = CsvConnector().open(spec)
    return DetectionContext(
        dataset_id="t",
        table_name=None,
        columns=handle.schema().column_names,
        handle=handle,
    )


class TestZeroRowCsv:
    def test_excessive_null_rate_no_crash(self, tmp_path: Path) -> None:
        ctx = _context(tmp_path)
        try:
            assert ExcessiveNullRateDetector().detect(ctx) == []
        finally:
            ctx.handle.close()

    def test_column_nulls_zero_row(self, tmp_path: Path) -> None:
        ctx = _context(tmp_path)
        try:
            assert _column_nulls(ctx, "a") == (0, 0)
        finally:
            ctx.handle.close()

    def test_scan_zero_row_completes(self, tmp_path: Path) -> None:
        from datasentry import DataSentry

        src = tmp_path / "hdr_only.csv"
        src.write_text("a,b\n", encoding="utf-8")
        before = hashlib.sha256(src.read_bytes()).hexdigest()
        client = DataSentry(tmp_path / "ws")
        try:
            run, runs, issues = client.scan_file(src)
            assert run.status == "completed"
            assert issues == []
            assert [r for r in runs if r.status == "failed"] == []
            assert src.read_bytes() is not None
            assert hashlib.sha256(src.read_bytes()).hexdigest() == before
        finally:
            client.close()
