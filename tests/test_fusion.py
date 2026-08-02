"""Step 7 证据融合引擎 + 调度器测试。"""

from __future__ import annotations

from pathlib import Path

import pytest

from datasentry_core.connectors import CsvConnector, DataSourceSpec, DataSourceType
from datasentry_core.detectors import DetectionContext, DetectorRegistry
from datasentry_core.detectors.initial import register_default_detectors
from datasentry_core.detectors.runner import ScanRunner
from datasentry_core.engine import EvidenceFusionEngine
from datasentry_core.models.detector import IssueCandidate
from datasentry_core.models.enums import (
    EvidenceType,
    QualityDimension,
    RiskLevel,
    Severity,
)
from datasentry_core.models.evidence import Evidence
from datasentry_core.models.scan import ScanConfig


def _candidate(
    issue_type: str,
    columns: list[str],
    confidence: float = 0.8,
    affected_count: int = 2,
    affected_rows: list[str] | None = None,
    severity: str = "low",
    detector_id: str = "d1",
    fpr: float = 0.1,
    dataset_id: str = "ds_x",
) -> IssueCandidate:
    return IssueCandidate(
        issue_type=issue_type,
        detector_id=detector_id,
        detector_version="1.0.0",
        dataset_id=dataset_id,
        columns=columns,
        affected_rows=affected_rows,
        affected_count=affected_count,
        evidence=[
            Evidence(
                evidence_id=f"ev_{issue_type}_{detector_id}",
                evidence_type=EvidenceType.PATTERN_MATCH,
                detector_id=detector_id,
                detector_version="1.0.0",
                description="test",
            )
        ],
        raw_score=float(affected_count),
        confidence=confidence,
        estimated_false_positive_risk=fpr,
        suggested_severity=severity,
    )


class TestFusion:
    def test_same_family_same_columns_merged(self) -> None:
        engine = EvidenceFusionEngine()
        candidates = [
            _candidate("iqr_outlier", ["v"], confidence=0.8),
            _candidate("modified_zscore", ["v"], confidence=0.6),
        ]
        issues = engine.fuse(candidates, scan_run_id="scan_1", row_count=100)
        assert len(issues) == 1
        issue = issues[0]
        assert issue.issue_type == "numeric_outlier"
        assert issue.columns == ["v"]
        assert issue.detector_ids == ["d1", "d1"]
        assert len(issue.evidence) == 2
        # 1 − (1−0.8)(1−0.6) = 0.92
        assert issue.confidence == pytest.approx(0.92)
        assert issue.severity == Severity.LOW

    def test_different_columns_not_merged(self) -> None:
        engine = EvidenceFusionEngine()
        issues = engine.fuse(
            [
                _candidate("iqr_outlier", ["a"]),
                _candidate("iqr_outlier", ["b"]),
            ],
            scan_run_id="scan_1",
            row_count=10,
        )
        assert len(issues) == 2

    def test_step13_issue_types_map_to_families(self) -> None:
        engine = EvidenceFusionEngine()
        cases = [
            ("percentile_outlier", "numeric_outlier"),
            ("histogram_rarity", "numeric_outlier"),
            ("invalid_phone", "string_format"),
            ("invalid_url", "string_format"),
            ("invalid_ip", "string_format"),
            ("inconsistent_case", "categorical_anomaly"),
            ("cross_field_violation", "cross_field_constraint"),
        ]
        for issue_type, family in cases:
            issues = engine.fuse([_candidate(issue_type, ["v"])], "s", 100)
            assert issues[0].issue_type == family, issue_type

    def test_different_families_not_merged(self) -> None:
        engine = EvidenceFusionEngine()
        issues = engine.fuse(
            [
                _candidate("iqr_outlier", ["v"]),
                _candidate("excessive_null_rate", ["v"]),
            ],
            scan_run_id="scan_1",
            row_count=10,
        )
        assert len(issues) == 2
        families = {i.issue_type for i in issues}
        assert families == {"numeric_outlier", "missingness"}

    def test_row_level_union(self) -> None:
        engine = EvidenceFusionEngine()
        issues = engine.fuse(
            [
                _candidate("iqr_outlier", ["v"], affected_rows=["1", "2"]),
                _candidate("tail_probability", ["v"], affected_rows=["2", "3"]),
            ],
            scan_run_id="scan_1",
            row_count=10,
        )
        assert len(issues) == 1
        assert issues[0].affected_count == 3
        assert issues[0].affected_row_ids == ["1", "2", "3"]

    def test_column_level_uses_max(self) -> None:
        engine = EvidenceFusionEngine()
        issues = engine.fuse(
            [
                _candidate("iqr_outlier", ["v"], affected_count=5),
                _candidate("modified_zscore", ["v"], affected_count=9),
            ],
            scan_run_id="scan_1",
            row_count=100,
        )
        assert issues[0].affected_count == 9
        assert issues[0].affected_ratio == pytest.approx(0.09)

    def test_severity_takes_highest(self) -> None:
        engine = EvidenceFusionEngine()
        issues = engine.fuse(
            [
                _candidate("iqr_outlier", ["v"], severity="low"),
                _candidate("modified_zscore", ["v"], severity="high"),
            ],
            scan_run_id="scan_1",
            row_count=100,
        )
        assert issues[0].severity == Severity.HIGH

    def test_risk_level_mapping(self) -> None:
        engine = EvidenceFusionEngine()
        low = engine.fuse([_candidate("iqr_outlier", ["v"], fpr=0.1)], "s", 100)[0]
        high = engine.fuse([_candidate("iqr_outlier", ["v"], fpr=0.7)], "s", 100)[0]
        assert low.false_positive_risk == RiskLevel.LOW
        assert high.false_positive_risk == RiskLevel.HIGH

    def test_unknown_family_passes_through(self) -> None:
        engine = EvidenceFusionEngine()
        issues = engine.fuse([_candidate("custom_check", ["v"])], "s", 100)
        assert issues[0].issue_type == "custom_check"

    def test_empty_candidates(self) -> None:
        assert EvidenceFusionEngine().fuse([], "s", 100) == []

    def test_priority_score_placeholder(self) -> None:
        engine = EvidenceFusionEngine()
        issues = engine.fuse([_candidate("iqr_outlier", ["v"])], "s", 100)
        assert issues[0].priority_score == 0.0  # Step 8 填充


@pytest.fixture
def scan_ctx(tmp_path: Path) -> DetectionContext:
    p = tmp_path / "scan.csv"
    p.write_text(
        "id,amount,email\n1,10,a@x.co\n1,1000,b@x.co\n2,-5,not-an-email\n,500,c@x.co\n",
        encoding="utf-8",
    )
    spec = DataSourceSpec(source_type=DataSourceType.CSV, path=p, options={"dataset_id": "ds_scan"})
    handle = CsvConnector().open(spec)
    return DetectionContext(
        dataset_id="ds_scan",
        table_name=None,
        columns=handle.schema().column_names,
        handle=handle,
    )


class TestScanRunner:
    def test_full_scan_produces_runs_and_issues(self, scan_ctx: DetectionContext) -> None:
        reg = DetectorRegistry()
        register_default_detectors(reg)
        runner = ScanRunner(reg)
        runs, issues = runner.run(scan_ctx, ScanConfig(), scan_run_id="scan_1")
        assert len(runs) == 22
        assert all(r.scan_run_id == "scan_1" for r in runs)
        assert all(r.detector_id for r in runs)
        assert all(r.duration_ms >= 0 for r in runs)
        assert issues, "脏数据应产生至少一个 Issue"

    def test_detector_config_whitelist(self, scan_ctx: DetectionContext) -> None:
        reg = DetectorRegistry()
        register_default_detectors(reg)
        runner = ScanRunner(reg)
        runs, _ = runner.run(
            scan_ctx,
            ScanConfig(detectors=["excessive_null_rate"]),
            scan_run_id="scan_2",
        )
        assert [r.detector_id for r in runs] == ["excessive_null_rate"]

    def test_failed_detector_recorded(self, scan_ctx: DetectionContext) -> None:
        class BoomDetector:
            detector_id = "boom"
            detector_version = "1.0.0"

            def supports(self, context: DetectionContext) -> bool:
                return True

            def detect(self, context: DetectionContext):
                raise RuntimeError("boom")

            def metadata(self):
                from datasentry_core.models.detector import DetectorMeta

                return DetectorMeta(
                    detector_id="boom",
                    display_name="Boom",
                    description="x",
                    quality_dimension=QualityDimension.VALIDITY,
                )

        reg = DetectorRegistry()
        reg.register(BoomDetector())
        runner = ScanRunner(reg)
        runs, issues = runner.run(scan_ctx, ScanConfig(), scan_run_id="scan_3")
        assert runs[0].status == "failed"
        assert "boom" in (runs[0].error or "")
        assert issues == []
        scan_ctx.handle.close()
