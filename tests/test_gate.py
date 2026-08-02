"""Step 12 质量门禁测试（22 章场景 C + ADR-014）。"""

from __future__ import annotations

from datasentry_core.models.contract import QualityGate
from datasentry_core.models.enums import QualityDimension, Severity
from datasentry_core.models.issue import Issue
from datasentry_core.scoring import GateResult, QualityGateEvaluator


def _issue(
    issue_id: str,
    severity: Severity,
    affected_ratio: float,
    confidence: float = 1.0,
) -> Issue:
    return Issue(
        id=issue_id,
        scan_run_id="scan_1",
        issue_type="numeric_outlier",
        title="t",
        dataset_id="ds",
        columns=["v"],
        quality_dimensions=[QualityDimension.VALIDITY],
        severity=severity,
        confidence=confidence,
        priority_score=0.0,
        affected_count=int(affected_ratio * 100),
        affected_ratio=affected_ratio,
        detector_ids=["d1"],
    )


class TestGateEvaluator:
    def test_defaults_critical_and_001(self) -> None:
        gate = QualityGate()
        assert gate.fail_on == [Severity.CRITICAL]
        assert gate.maximum_failed_rows_ratio == 0.01

    def test_clean_issues_pass(self) -> None:
        result = QualityGateEvaluator().evaluate([], QualityGate())
        assert result.passed is True
        assert result.failed_issues == []
        assert isinstance(result, GateResult)

    def test_fails_on_severity_threshold(self) -> None:
        issues = [_issue("i1", Severity.CRITICAL, affected_ratio=0.5)]
        result = QualityGateEvaluator().evaluate(issues, QualityGate(fail_on=[Severity.CRITICAL]))
        assert result.passed is False
        assert result.failed_issues == ["i1"]
        assert any("maximum_failed_rows_ratio" in r for r in result.reasons)

    def test_ignores_lower_severity(self) -> None:
        issues = [_issue("i1", Severity.HIGH, affected_ratio=0.9)]
        result = QualityGateEvaluator().evaluate(issues, QualityGate(fail_on=[Severity.CRITICAL]))
        assert result.passed is True

    def test_max_failed_rows_ratio_boundary(self) -> None:
        evaluator = QualityGateEvaluator()
        below = [_issue("i1", Severity.CRITICAL, affected_ratio=0.005)]
        assert evaluator.evaluate(below, QualityGate()).passed is True
        above = [_issue("i1", Severity.CRITICAL, affected_ratio=0.02)]
        assert evaluator.evaluate(above, QualityGate()).passed is False

    def test_maximum_issues_limit(self) -> None:
        issues = [_issue("i1", Severity.HIGH, affected_ratio=0.001)]
        gate = QualityGate(
            fail_on=[Severity.CRITICAL],
            maximum_issues={Severity.HIGH: 0},
        )
        result = QualityGateEvaluator().evaluate(issues, gate)
        assert result.passed is False
        assert result.failed_issues == ["i1"]
        assert any("maximum_issues" in r for r in result.reasons)

    def test_repair_validation_unsupported_fails(self) -> None:
        result = QualityGateEvaluator().evaluate([], QualityGate(require_repair_validation=True))
        assert result.passed is False
        assert "require_repair_validation" in result.reasons[0]
