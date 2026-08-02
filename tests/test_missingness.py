"""Step 16 缺失模式检测器族测试（11.4，ADR-017）。"""

from __future__ import annotations

from pathlib import Path

from datasentry_core.connectors import CsvConnector, DataSourceSpec, DataSourceType
from datasentry_core.detectors import DetectionContext
from datasentry_core.detectors.missingness import (
    ConditionalMissingnessDetector,
    CorrelatedMissingnessDetector,
    GroupMissingnessDetector,
    SuddenMissingnessDetector,
)
from datasentry_core.models.detector import IssueCandidate


def _ctx(tmp_path: Path, csv_text: str) -> DetectionContext:
    p = tmp_path / "data.csv"
    p.write_text(csv_text, encoding="utf-8")
    spec = DataSourceSpec(source_type=DataSourceType.CSV, path=p, options={"dataset_id": "test"})
    handle = CsvConnector().open(spec)
    return DetectionContext(
        dataset_id="test",
        table_name=None,
        columns=handle.schema().column_names,
        handle=handle,
    )


def _detect(detector: object, ctx: DetectionContext) -> list[IssueCandidate]:
    try:
        return detector.detect(ctx)  # type: ignore[attr-defined]
    finally:
        ctx.handle.close()


class TestCorrelatedMissingness:
    def test_reports_cooccurring_columns(self, tmp_path: Path) -> None:
        ctx = _ctx(
            tmp_path,
            "a,b,c\n"
            + "\n".join("1,1,1" for _ in range(100))
            + "\n"
            + "\n".join(",," for _ in range(10))
            + "\n",
        )
        candidates = _detect(CorrelatedMissingnessDetector(), ctx)
        assert len(candidates) == 3
        for candidate in candidates:
            assert candidate.affected_count == 10
            assert candidate.suggested_severity == "low"

    def test_reports_nothing_without_cooccurrence(self, tmp_path: Path) -> None:
        ctx = _ctx(
            tmp_path,
            "a,b\n"
            + "\n".join("1,1" for _ in range(100))
            + "\n"
            + "\n".join(",1" for _ in range(10))
            + "\n"
            + "\n".join("1," for _ in range(10))
            + "\n",
        )
        # a 缺 10、b 缺 10，但无共现（both_null=0）
        candidates = _detect(CorrelatedMissingnessDetector(), ctx)
        assert candidates == []


class TestConditionalMissingness:
    def test_reports_cascade_missingness(self, tmp_path: Path) -> None:
        ctx = _ctx(
            tmp_path,
            "parent_id,child_id\n"
            + "\n".join(f"{i},{i}" for i in range(90))
            + "\n"
            + "\n".join("," for _ in range(10))
            + "\n",
        )
        candidates = _detect(ConditionalMissingnessDetector(), ctx)
        assert len(candidates) == 1
        assert candidates[0].affected_count == 10
        assert set(candidates[0].columns) == {"parent_id", "child_id"}

    def test_reports_nothing_when_partially_present(self, tmp_path: Path) -> None:
        ctx = _ctx(
            tmp_path,
            "parent_id,child_id\n"
            + "\n".join(f"{i},{i}" for i in range(90))
            + "\n"
            + "\n".join("," for _ in range(10))
            + "\n"
            + "\n".join("p,1" for _ in range(5))
            + "\n",
        )
        # parent 缺失 10 行，其中 child 缺失 10（coverage 1.0）；
        # child 缺失 10+5 行——parent 缺失 ⟹ child 缺失方向仍成立，
        # 反向（child 缺失 ⟹ parent 缺失）coverage = 10/15 < 0.8
        candidates = _detect(ConditionalMissingnessDetector(), ctx)
        assert len(candidates) == 1
        assert candidates[0].columns == ["parent_id", "child_id"]


class TestGroupMissingness:
    def test_reports_anomalous_group(self, tmp_path: Path) -> None:
        ctx = _ctx(
            tmp_path,
            "city,value\n"
            + "\n".join(f"beijing,{i}" for i in range(50))
            + "\n"
            + "\n".join("shanghai," for _ in range(30))
            + "\n"
            + "\n".join(f"guangzhou,{i}" for i in range(20))
            + "\n",
        )
        candidates = _detect(GroupMissingnessDetector(), ctx)
        assert len(candidates) == 1
        assert candidates[0].columns == ["value"]
        assert candidates[0].evidence[0].data["group_value"] == "shanghai"
        assert candidates[0].affected_count == 30

    def test_reports_nothing_when_uniform(self, tmp_path: Path) -> None:
        ctx = _ctx(
            tmp_path,
            "city,value\n"
            + "\n".join(f"beijing,{i}" for i in range(50))
            + "\n"
            + "\n".join(f"shanghai,{i}" for i in range(30))
            + "\n",
        )
        candidates = _detect(GroupMissingnessDetector(), ctx)
        assert candidates == []


class TestSuddenMissingness:
    def test_reports_spike_bucket(self, tmp_path: Path) -> None:
        csv_lines = ["event_date,value"]
        for d in range(1, 7):
            for i in range(20):
                csv_lines.append(f"2024-01-0{d},{i}")
        # 2024-01-05 桶额外 20 行全缺失（桶缺失率 0.5，整体 0.143）
        csv_lines.extend("2024-01-05," for _ in range(20))
        ctx = _ctx(tmp_path, "\n".join(csv_lines) + "\n")
        candidates = _detect(SuddenMissingnessDetector(), ctx)
        assert len(candidates) == 1
        assert candidates[0].evidence[0].data["bucket"] == "2024-01-05"
        assert candidates[0].affected_count == 20
        assert candidates[0].evidence[0].data["null_ratio"] == 0.5

    def test_reports_nothing_without_temporal_column(self, tmp_path: Path) -> None:
        ctx = _ctx(
            tmp_path,
            "a,b\n" + "\n".join(f"{i},{i}" for i in range(50)) + "\n",
        )
        detector = SuddenMissingnessDetector()
        assert not detector.supports(ctx)
        _detect(detector, ctx)
