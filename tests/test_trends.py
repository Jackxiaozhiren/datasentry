"""跨扫描趋势数据层（Step 45）：build_trends 纯函数 + 空/过滤边界。"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from datasentry.trends import build_trends
from datasentry_core import __version__
from datasentry_core.models.enums import Severity
from datasentry_core.models.quality import QualityScore
from datasentry_core.models.scan import ReproducibilityInfo, ScanConfig, ScanRun


def _scan(
    run_id: str,
    dataset_id: str,
    score: float | None,
    *,
    finished: datetime | None = None,
    status: str = "completed",
    issues: int = 0,
) -> ScanRun:
    issues_count = {Severity.HIGH: issues} if issues else {}
    return ScanRun(
        id=run_id,
        dataset_id=dataset_id,
        status=status,
        config=ScanConfig(),
        fingerprint={
            "dataset_id": dataset_id,
            "fingerprint_type": "full",
            "schema_hash": "h",
            "row_count": 10,
            "column_count": 1,
            "column_signature": [("c", "BIGINT")],
        },
        quality_score=QualityScore(overall=score) if score is not None else None,
        issues_count=issues_count,
        started_at=finished or datetime(2026, 1, 1, tzinfo=UTC),
        finished_at=finished or datetime(2026, 1, 1, tzinfo=UTC),
        reproducibility=ReproducibilityInfo(
            datasentry_version=__version__,
            detector_versions={},
            seed=42,
        ),
    )


class TestBuildTrends:
    def test_empty(self) -> None:
        assert build_trends([]) == []

    def test_groups_by_dataset_and_orders_by_time(self) -> None:
        t0 = datetime(2026, 1, 1)
        scans = [
            _scan("r1", "ds_a", 90.0, finished=t0 + timedelta(days=2)),
            _scan("r2", "ds_a", 85.0, finished=t0),
            _scan("r3", "ds_b", 60.0, finished=t0 + timedelta(days=1)),
        ]
        trends = build_trends(scans)
        assert [t.dataset_id for t in trends] == ["ds_a", "ds_b"]
        ds_a = trends[0]
        assert [p.run_id for p in ds_a.points] == ["r2", "r1"]
        assert ds_a.points[0].score == 85.0
        assert ds_a.delta == pytest.approx(5.0)

    def test_direction(self) -> None:
        t0 = datetime(2026, 1, 1)
        up = build_trends(
            [_scan("r1", "a", 50.0, finished=t0), _scan("r2", "a", 90.0, finished=t0)]
        )[0]
        down = build_trends(
            [_scan("r1", "b", 90.0, finished=t0), _scan("r2", "b", 50.0, finished=t0)]
        )[0]
        flat = build_trends(
            [_scan("r1", "c", 50.0, finished=t0), _scan("r2", "c", 50.2, finished=t0)]
        )[0]
        assert up.direction == "up"
        assert down.direction == "down"
        assert flat.direction == "flat"

    def test_filters_non_completed_and_unscored(self) -> None:
        t0 = datetime(2026, 1, 1)
        scans = [
            _scan("r1", "a", 90.0, finished=t0),
            _scan("r2", "a", None, finished=t0, status="completed"),
            _scan("r3", "a", 80.0, finished=t0, status="failed"),
        ]
        trends = build_trends(scans)
        assert len(trends) == 1
        assert [p.run_id for p in trends[0].points] == ["r1"]

    def test_single_point_delta_zero_and_latest(self) -> None:
        trends = build_trends([_scan("r1", "a", 77.5, finished=datetime(2026, 1, 1))])
        trend = trends[0]
        assert trend.delta == 0.0
        assert trend.direction == "flat"
        assert trend.latest_score == 77.5

    def test_issues_total_accumulates_severities(self) -> None:
        scan = _scan("r1", "a", 50.0, finished=datetime(2026, 1, 1), issues=3)
        trends = build_trends([scan])
        assert trends[0].points[0].issues_total == 3
