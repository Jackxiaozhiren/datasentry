"""Step 11 质量总分引擎测试（27 章 + ADR-003/013 归一）。"""

from __future__ import annotations

import pytest

from datasentry_core.models.enums import QualityDimension, Severity
from datasentry_core.models.issue import Issue
from datasentry_core.scoring import DIMENSION_WEIGHTS, SCORE_VERSION, QualityScoreEngine
from datasentry_core.scoring.quality import _canonical

_ALL_DIMS = set(DIMENSION_WEIGHTS)


def _issue(
    issue_id: str = "iss_1",
    severity: Severity = Severity.HIGH,
    confidence: float = 1.0,
    affected_ratio: float = 0.5,
    dimensions: list[QualityDimension] | None = None,
) -> Issue:
    return Issue(
        id=issue_id,
        scan_run_id="scan_1",
        issue_type="numeric_outlier",
        title="t",
        dataset_id="ds",
        columns=["v"],
        quality_dimensions=dimensions or [QualityDimension.VALIDITY],
        severity=severity,
        confidence=confidence,
        priority_score=0.0,
        affected_count=5,
        affected_ratio=affected_ratio,
        detector_ids=["d1"],
    )


class TestDimensionWeights:
    def test_defaults_match_spec_272(self) -> None:
        assert {d.value: w for d, w in DIMENSION_WEIGHTS.items()} == {
            "completeness": 0.20,
            "validity": 0.20,
            "uniqueness": 0.15,
            "consistency": 0.20,
            "integrity": 0.15,
            "timeliness": 0.10,
        }
        assert sum(DIMENSION_WEIGHTS.values()) == 1.0

    def test_adr001_canonical_merge(self) -> None:
        assert _canonical(QualityDimension.ACCURACY_PROXY) is QualityDimension.VALIDITY
        assert _canonical(QualityDimension.DISTRIBUTION_STABILITY) is QualityDimension.INTEGRITY
        assert _canonical(QualityDimension.COMPLETENESS) is QualityDimension.COMPLETENESS


class TestQualityScoreEngine:
    def test_worked_example_exact_math(self) -> None:
        """27.1 完整算例：high(0.75) × ratio 0.5 × conf 1.0 → impact 0.375。"""
        engine = QualityScoreEngine()
        result = engine.score([_issue()], ran_dimensions=_ALL_DIMS)
        assert result.dimensions["validity"] == 76.6  # 100 × (1 − 0.375 / 1.6)
        assert result.dimensions["completeness"] == 100.0
        assert result.overall == 95.3  # 0.2×76.6 + 0.8×100
        assert result.score_version == SCORE_VERSION
        assert result.dimension_contributions == {"validity": {"iss_1": 0.375}}

    def test_critical_full_deduction_floor(self) -> None:
        """critical 严重度 + 100% 受影响 + 全置信 → NORMAL 字段下限 37.5。"""
        engine = QualityScoreEngine()
        result = engine.score(
            [_issue(severity=Severity.CRITICAL, affected_ratio=1.0)],
            ran_dimensions=_ALL_DIMS,
        )
        assert result.dimensions["validity"] == 37.5

    def test_accumulation_and_clamp_floor(self) -> None:
        """多 Issue 累计；扣分超过理论最坏时 clamp 到 37.5 下限（NORMAL 字段）。"""
        engine = QualityScoreEngine()
        issues = [
            _issue(f"iss_{i}", severity=Severity.CRITICAL, affected_ratio=1.0) for i in range(10)
        ]
        result = engine.score(issues, ran_dimensions=_ALL_DIMS)
        assert result.dimensions["validity"] == 37.5

    def test_no_issues_all_perfect(self) -> None:
        engine = QualityScoreEngine()
        result = engine.score([], ran_dimensions=_ALL_DIMS)
        assert all(v == 100.0 for v in result.dimensions.values())
        assert result.overall == 100.0

    def test_null_dimensions_renormalize_weights(self) -> None:
        """27.1：无相关检测器运行的维度为 None，权重重新归一化。"""
        engine = QualityScoreEngine()
        ran = {
            QualityDimension.VALIDITY,
            QualityDimension.COMPLETENESS,
            QualityDimension.UNIQUENESS,
        }
        result = engine.score([_issue()], ran_dimensions=ran)
        assert result.dimensions["consistency"] is None
        assert result.dimensions["integrity"] is None
        assert result.dimensions["timeliness"] is None
        assert result.dimensions["validity"] == 76.6
        assert result.weights["validity"] == pytest.approx(0.20 / 0.55)
        assert result.weights["completeness"] == pytest.approx(0.20 / 0.55)
        assert result.weights["uniqueness"] == pytest.approx(0.15 / 0.55)
        assert result.overall == pytest.approx(91.5)
        assert "renormalized" in result.calculation_notes

    def test_custom_weights(self) -> None:
        """27.2：权重可配（不要求总和 1，引擎重新归一化）。"""
        engine = QualityScoreEngine()
        weights = {QualityDimension.VALIDITY: 1.0}
        result = engine.score([_issue()], ran_dimensions=_ALL_DIMS, weights=weights)
        assert result.overall == 76.6
        assert result.weights == {"validity": 1.0}

    def test_negative_weight_rejected(self) -> None:
        engine = QualityScoreEngine()
        with pytest.raises(ValueError):
            engine.score(
                [],
                ran_dimensions=_ALL_DIMS,
                weights={QualityDimension.VALIDITY: -0.1},
            )

    def test_no_dimension_scored_raises(self) -> None:
        engine = QualityScoreEngine()
        with pytest.raises(ValueError, match="no dimension scored"):
            engine.score([_issue()], ran_dimensions=set())

    def test_adr001_issue_maps_into_validity(self) -> None:
        """ACCURACY_PROXY Issue 按 ADR-001 归入 Validity 计分。"""
        engine = QualityScoreEngine()
        issue = _issue(dimensions=[QualityDimension.ACCURACY_PROXY])
        result = engine.score([issue], ran_dimensions=_ALL_DIMS)
        assert result.dimensions["validity"] == 76.6
        assert result.dimension_contributions == {"validity": {"iss_1": 0.375}}

    def test_multi_dimension_issue_counts_in_each(self) -> None:
        """Issue 标注多个维度时对每个维度计分（MVP 检测器为单维度，防御性约定）。"""
        engine = QualityScoreEngine()
        issue = _issue(dimensions=[QualityDimension.COMPLETENESS, QualityDimension.VALIDITY])
        result = engine.score([issue], ran_dimensions=_ALL_DIMS)
        assert result.dimensions["validity"] == 76.6
        assert result.dimensions["completeness"] == 76.6
