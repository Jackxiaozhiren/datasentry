"""Step 15 日期时间检测器族测试（11.8，C-13 P0 化 6 个核心）。"""

from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path

from datasentry_core.connectors import CsvConnector, DataSourceSpec, DataSourceType
from datasentry_core.detectors import DetectionContext
from datasentry_core.detectors.datetime import (
    DuplicateTimestampDetector,
    FutureDateDetector,
    ImpossibleDateDetector,
    InvalidDateDetector,
    MixedDateFormatDetector,
    StaleDateDetector,
)

TODAY = date.today()
RECENT = (TODAY - timedelta(days=10)).isoformat()
OLD = (TODAY - timedelta(days=730)).isoformat()
FAR_FUTURE = (TODAY + timedelta(days=3650)).isoformat()


def _ctx(tmp_path: Path, csv_text: str) -> DetectionContext:
    p = tmp_path / "dt.csv"
    p.write_text(csv_text, encoding="utf-8")
    spec = DataSourceSpec(source_type=DataSourceType.CSV, path=p, options={"dataset_id": "ds_dt"})
    handle = CsvConnector().open(spec)
    return DetectionContext(
        dataset_id="ds_dt",
        table_name=None,
        columns=handle.schema().column_names,
        handle=handle,
    )


def _detect(detector, ctx: DetectionContext) -> list:
    try:
        return detector.detect(ctx)
    finally:
        ctx.handle.close()


class TestInvalidDate:
    def test_reports_non_date_strings(self, tmp_path: Path) -> None:
        ctx = _ctx(
            tmp_path,
            "event_date\n2024-01-01\nnot-a-date\n2024-03-15\n",
        )
        candidates = _detect(InvalidDateDetector(), ctx)
        assert len(candidates) == 1
        assert candidates[0].affected_count == 1
        assert candidates[0].columns == ["event_date"]

    def test_skips_clean_date_column(self, tmp_path: Path) -> None:
        ctx = _ctx(tmp_path, "event_date\n2024-01-01\n2024-03-15\n")
        assert _detect(InvalidDateDetector(), ctx) == []

    def test_skips_non_date_columns(self, tmp_path: Path) -> None:
        ctx = _ctx(tmp_path, "name\nalice\nbob\n")
        assert _detect(InvalidDateDetector(), ctx) == []

    def test_timestamp_column_not_flagged_as_invalid(self, tmp_path: Path) -> None:
        ctx = _ctx(
            tmp_path,
            "created_at\n2024-01-01 10:30:00\n2024-03-15 08:00:00\n",
        )
        assert _detect(InvalidDateDetector(), ctx) == []


class TestImpossibleDate:
    def test_reports_calendar_invalid_dates(self, tmp_path: Path) -> None:
        ctx = _ctx(
            tmp_path,
            "event_date\n2024-02-30\n2024-01-15\n2024-13-01\n",
        )
        candidates = _detect(ImpossibleDateDetector(), ctx)
        assert len(candidates) == 1
        assert candidates[0].affected_count == 2
        assert candidates[0].suggested_severity == "high"

    def test_clean_dates_no_issue(self, tmp_path: Path) -> None:
        ctx = _ctx(tmp_path, "event_date\n2024-01-15\n2024-02-29\n2024-12-31\n")
        assert _detect(ImpossibleDateDetector(), ctx) == []


class TestFutureDate:
    def test_reports_future_dates(self, tmp_path: Path) -> None:
        ctx = _ctx(
            tmp_path,
            f"event_date\n{FAR_FUTURE}\n{RECENT}\n",
        )
        candidates = _detect(FutureDateDetector(), ctx)
        assert len(candidates) == 1
        assert candidates[0].affected_count == 1

    def test_no_future_dates(self, tmp_path: Path) -> None:
        ctx = _ctx(tmp_path, f"event_date\n{RECENT}\n{TODAY.isoformat()}\n")
        assert _detect(FutureDateDetector(), ctx) == []


class TestStaleDate:
    def test_reports_stale_dates(self, tmp_path: Path) -> None:
        ctx = _ctx(
            tmp_path,
            f"event_date\n{OLD}\n{RECENT}\n",
        )
        candidates = _detect(StaleDateDetector(), ctx)
        assert len(candidates) == 1
        assert candidates[0].affected_count == 1

    def test_exempts_historical_fields(self, tmp_path: Path) -> None:
        ctx = _ctx(tmp_path, f"birth_date\n{OLD}\n")
        assert _detect(StaleDateDetector(), ctx) == []

    def test_recent_dates_no_issue(self, tmp_path: Path) -> None:
        ctx = _ctx(tmp_path, f"event_date\n{RECENT}\n{TODAY.isoformat()}\n")
        assert _detect(StaleDateDetector(), ctx) == []


class TestMixedDateFormat:
    def test_reports_mixed_formats(self, tmp_path: Path) -> None:
        iso_rows = [f"2024-01-{d:02d}" for d in range(1, 11)]
        compact_rows = [f"2024{d:02d}01" for d in range(1, 11)]
        ctx = _ctx(tmp_path, "event_date\n" + "\n".join(iso_rows + compact_rows) + "\n")
        candidates = _detect(MixedDateFormatDetector(), ctx)
        assert len(candidates) == 1
        assert candidates[0].affected_count == 10
        evidence = candidates[0].evidence[0]
        assert set(evidence.data["formats"]) >= {"iso", "compact"}

    def test_single_format_no_issue(self, tmp_path: Path) -> None:
        rows = [f"2024-01-{d:02d}" for d in range(1, 15)]
        ctx = _ctx(tmp_path, "event_date\n" + "\n".join(rows) + "\n")
        assert _detect(MixedDateFormatDetector(), ctx) == []

    def test_too_few_distinct_values_skipped(self, tmp_path: Path) -> None:
        ctx = _ctx(tmp_path, "event_date\n2024-01-15\n15/01/2024\n")
        assert _detect(MixedDateFormatDetector(), ctx) == []


class TestDuplicateTimestamp:
    def test_reports_timestamps_above_threshold(self, tmp_path: Path) -> None:
        ctx = _ctx(
            tmp_path,
            "ts\n2024-01-01 10:00:00\n2024-01-01 10:00:00\n2024-01-01 10:00:00\n"
            "2024-01-02 10:00:00\n",
        )
        candidates = _detect(DuplicateTimestampDetector(), ctx)
        assert len(candidates) == 1
        assert candidates[0].affected_count == 2  # 3 次出现 → 2 行多余

    def test_unique_timestamps_no_issue(self, tmp_path: Path) -> None:
        ctx = _ctx(
            tmp_path,
            "ts\n2024-01-01 10:00:00\n2024-01-01 11:00:00\n2024-01-01 12:00:00\n",
        )
        assert _detect(DuplicateTimestampDetector(), ctx) == []
