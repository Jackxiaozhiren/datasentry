"""Step 8 评分引擎测试（12.8 公式 + ADR-002 归一化 + C-02 完整算例）。"""

from __future__ import annotations

import pytest

from datasentry_core.models.enums import (
    BusinessCriticality,
    QualityDimension,
    Severity,
)
from datasentry_core.models.issue import Issue
from datasentry_core.scoring import WEIGHTS, ScoringEngine
from datasentry_core.scoring.weights import (
    CRITICALITY_WEIGHTS,
    SEVERITY_WEIGHTS,
    criticality_norm,
)


def _issue(
    severity: Severity = Severity.MEDIUM,
    confidence: float = 0.8,
    affected_ratio: float = 0.05,
    detector_ids: list[str] | None = None,
) -> Issue:
    return Issue(
        id="iss_1",
        scan_run_id="scan_1",
        issue_type="numeric_outlier",
        title="t",
        dataset_id="ds",
        columns=["v"],
        quality_dimensions=[QualityDimension.VALIDITY],
        severity=severity,
        confidence=confidence,
        priority_score=0.0,
        affected_count=5,
        affected_ratio=affected_ratio,
        detector_ids=detector_ids or ["d1"],
    )


class TestWeights:
    def test_single_source_consistency(self) -> None:
        assert set(SEVERITY_WEIGHTS) == set(Severity)
        assert SEVERITY_WEIGHTS[Severity.CRITICAL] == 1.0
        assert set(CRITICALITY_WEIGHTS) == set(BusinessCriticality)
        assert sum(WEIGHTS.values()) == 100

    def test_criticality_norm_normalized_adr002(self) -> None:
        assert criticality_norm(BusinessCriticality.INFORMATIONAL) == 0.0
        assert criticality_norm(BusinessCriticality.NORMAL) == 0.4
        assert criticality_norm(BusinessCriticality.IMPORTANT) == 0.7
        assert criticality_norm(BusinessCriticality.CRITICAL) == 1.0


class TestScoringEngine:
    def test_worked_example_exact_math(self) -> None:
        """C-02 附带要求：完整算例，精确复算写入单测。

        输入：severity=high(0.75)、confidence=0.9、受影响 2%（scope=0.4）、
        critical 字段、3 个检测器一致（agreement=1）、
        reproducibility=1、novelty=1、repairability=0.9。
        = 10×0.75 + 25×0.9 + 15×0.4 + 10×10 + 5×1 + 15×1 + 10×1 + 10×0.9 = 85.0
        """
        issue = _issue(
            severity=Severity.HIGH,
            confidence=0.9,
            affected_ratio=0.02,
            detector_ids=["d1", "d2", "d3"],
        )
        result = ScoringEngine().score(
            issue,
            BusinessCriticality.CRITICAL,
            reproducibility=1.0,
            novelty=1.0,
            repairability=0.9,
        )
        assert result.priority_score == pytest.approx(85.0)
        b = result.breakdown
        assert b.severity == pytest.approx(7.5)
        assert b.confidence == pytest.approx(22.5)
        assert b.affected_scope == pytest.approx(6.0)
        assert b.criticality == pytest.approx(10.0)
        assert b.reproducibility == pytest.approx(5.0)
        assert b.agreement == pytest.approx(15.0)
        assert b.novelty == pytest.approx(10.0)
        assert b.repairability == pytest.approx(9.0)
        assert b.total == pytest.approx(85.0)

    def test_affected_scope_capped_at_full(self) -> None:
        issue = _issue(affected_ratio=0.10)
        result = ScoringEngine().score(issue)
        assert result.breakdown.affected_scope == pytest.approx(15.0)

    def test_agreement_capped_at_full(self) -> None:
        issue = _issue(detector_ids=["d1", "d2", "d3", "d4"])
        result = ScoringEngine().score(issue)
        assert result.breakdown.agreement == pytest.approx(15.0)

    def test_agreement_partial(self) -> None:
        issue = _issue(detector_ids=["d1", "d2"])
        result = ScoringEngine().score(issue)
        assert result.breakdown.agreement == pytest.approx(10.0)

    def test_default_terms(self) -> None:
        """MVP 默认值：NORMAL criticality、reproducibility=1、novelty=1、repairability=0.5。"""
        issue = _issue()
        result = ScoringEngine().score(issue)
        assert result.breakdown.criticality == pytest.approx(4.0)
        assert result.breakdown.reproducibility == pytest.approx(5.0)
        assert result.breakdown.novelty == pytest.approx(10.0)
        assert result.breakdown.repairability == pytest.approx(5.0)

    def test_score_within_0_100(self) -> None:
        max_issue = _issue(
            severity=Severity.CRITICAL,
            confidence=1.0,
            affected_ratio=1.0,
            detector_ids=["d1", "d2", "d3"],
        )
        result = ScoringEngine().score(
            max_issue,
            BusinessCriticality.CRITICAL,
            reproducibility=1.0,
            novelty=1.0,
            repairability=1.0,
        )
        assert result.priority_score == pytest.approx(100.0)
        assert result.priority_score <= 100.0

    def test_apply_fills_issue_copy(self) -> None:
        issue = _issue()
        scored = ScoringEngine().apply(issue)
        assert scored.priority_score > 0.0
        assert issue.priority_score == 0.0  # 原对象不变
        assert scored.id == issue.id
