"""Step 14 跨字段规则检测器测试（11.10 + ADR-015）。"""

from __future__ import annotations

from pathlib import Path

import pytest

from datasentry_core.connectors import CsvConnector, DataSourceSpec, DataSourceType
from datasentry_core.detectors import DetectionContext
from datasentry_core.detectors.cross_field import CrossFieldRuleDetector
from datasentry_core.detectors.safe_eval import ExpressionSecurityError


def _ctx(tmp_path: Path, csv_text: str) -> DetectionContext:
    p = tmp_path / "cf.csv"
    p.write_text(csv_text, encoding="utf-8")
    spec = DataSourceSpec(source_type=DataSourceType.CSV, path=p, options={"dataset_id": "ds_cf"})
    handle = CsvConnector().open(spec)
    return DetectionContext(
        dataset_id="ds_cf",
        table_name=None,
        columns=handle.schema().column_names,
        handle=handle,
    )


def _detect(ctx: DetectionContext) -> list:
    try:
        return CrossFieldRuleDetector().detect(ctx)
    finally:
        ctx.handle.close()


class TestCrossFieldRuleDetector:
    def test_reports_start_end_violations(self, tmp_path: Path) -> None:
        ctx = _ctx(
            tmp_path,
            "start_date,end_date\n"
            "2024-01-01,2024-01-02\n"
            "2024-01-05,2024-01-01\n"
            "2024-02-01,2024-02-02\n",
        )
        candidates = _detect(ctx)
        assert len(candidates) == 1
        issue = candidates[0]
        assert issue.issue_type == "cross_field_violation"
        assert issue.columns == ["start_date", "end_date"]
        assert issue.affected_count == 1
        assert issue.affected_rows == ["2"]

    def test_numeric_min_max_pair(self, tmp_path: Path) -> None:
        ctx = _ctx(
            tmp_path,
            "min_price,max_price\n10,20\n30,5\n1,100\n",
        )
        candidates = _detect(ctx)
        assert len(candidates) == 1
        assert candidates[0].columns == ["min_price", "max_price"]
        assert candidates[0].affected_count == 1

    def test_clean_data_no_issues(self, tmp_path: Path) -> None:
        ctx = _ctx(
            tmp_path,
            "start_date,end_date\n2024-01-01,2024-01-02\n2024-01-05,2024-01-06\n",
        )
        assert _detect(ctx) == []

    def test_null_values_skipped(self, tmp_path: Path) -> None:
        ctx = _ctx(
            tmp_path,
            "start_date,end_date\n2024-01-01,\n,2024-01-05\n",
        )
        assert _detect(ctx) == []

    def test_unpaired_columns_no_issue(self, tmp_path: Path) -> None:
        ctx = _ctx(tmp_path, "a,b\n1,2\n3,4\n")
        assert _detect(ctx) == []

    def test_mixed_types_not_paired(self, tmp_path: Path) -> None:
        ctx = _ctx(tmp_path, "start,end\n1,hello\n2,world\n")
        assert _detect(ctx) == []

    def test_supports_only_with_pairs(self, tmp_path: Path) -> None:
        detector = CrossFieldRuleDetector()
        assert detector.supports(_ctx(tmp_path, "start_date,end_date\n1,2\n"))
        assert not detector.supports(_ctx(tmp_path, "a,b\n1,2\n"))

    def test_whitelist_applies_to_bound_rules(self) -> None:
        detector = CrossFieldRuleDetector()
        assert detector._evaluator.validate("start_date <= end_date") is not None
        with pytest.raises(ExpressionSecurityError):
            detector._evaluator.validate("start_date.__class__ <= end_date")
