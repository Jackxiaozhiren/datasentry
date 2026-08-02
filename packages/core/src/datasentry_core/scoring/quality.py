"""质量总分引擎（Step 11 / 27 章 + ADR-013）。

维度得分（27.1，ADR-003/013 归一）：

    dimension = 100 × (1 − Σ(weight_issue × severity_norm × affected_ratio) / max_possible)

- weight_issue = 12.4 字段关键度权重（MVP 默认 NORMAL=1.0，Contract 覆盖归 V1）
  × 置信度 × 覆盖调节（MVP 固定 1.0，规则覆盖调节归 V1）
- severity_norm 采用 SEVERITY_WEIGHTS（ADR-003，27.1 的 severity_norm 表废弃）
- max_possible = 该维度 Issue 数 × 12.4 最大字段权重（critical=1.6）
  × 最坏取值（severity/ratio/confidence/coverage 全为 1.0），即「critical 字段 100% 受影响」
  → NORMAL 字段的维度得分下限 37.5，CRITICAL 字段可至 0（字段关键程度参与扣分）
- 无相关检测器运行的维度 → None（27.1），不参与加权，权重重新归一化
- ADR-001 归并：ACCURACY_PROXY → VALIDITY、DISTRIBUTION_STABILITY → INTEGRITY
- 历史报告保留原权重与 score_version（27.2：趋势重算归 V1 UI）
"""

from __future__ import annotations

from datasentry_core.models.enums import BusinessCriticality, QualityDimension
from datasentry_core.models.issue import Issue
from datasentry_core.models.quality import QualityScore
from datasentry_core.scoring.weights import CRITICALITY_WEIGHTS, SEVERITY_WEIGHTS

#: 27.2 默认权重（总和 = 1.0）
DIMENSION_WEIGHTS: dict[QualityDimension, float] = {
    QualityDimension.COMPLETENESS: 0.20,
    QualityDimension.VALIDITY: 0.20,
    QualityDimension.UNIQUENESS: 0.15,
    QualityDimension.CONSISTENCY: 0.20,
    QualityDimension.INTEGRITY: 0.15,
    QualityDimension.TIMELINESS: 0.10,
}

#: 27.1 max_possible 的固定字段权重基准 = 12.4 critical 权重（1.6）：
#: 单 Issue 理论最坏影响 = critical 字段 × 100% 受影响 × critical 严重度 × 全置信 × 全覆盖
_MAX_DIMENSION_DEDUCTION = CRITICALITY_WEIGHTS[BusinessCriticality.CRITICAL]


SCORE_VERSION = "1"


def _canonical(dimension: QualityDimension) -> QualityDimension:
    """ADR-001 归并：8 维度 → 6 维度。"""
    if dimension is QualityDimension.ACCURACY_PROXY:
        return QualityDimension.VALIDITY
    if dimension is QualityDimension.DISTRIBUTION_STABILITY:
        return QualityDimension.INTEGRITY
    return dimension


class QualityScoreEngine:
    """27 章质量总分引擎（纯函数式，无状态）。"""

    def score(
        self,
        issues: list[Issue],
        *,
        ran_dimensions: set[QualityDimension] | None = None,
        weights: dict[QualityDimension, float] | None = None,
    ) -> QualityScore:
        """按 27.1 公式计算总分；无任何可评分维度时抛 ValueError。

        ran_dimensions：有检测器实际运行的维度集合；None 视为 6 维度全运行。
        权重可配（27.2）：weights 覆盖默认值（不要求总和为 1，引擎重新归一化）。
        """
        configured = weights or DIMENSION_WEIGHTS
        if any(w < 0 for w in configured.values()):
            raise ValueError("dimension weights must be non-negative")
        covered = ran_dimensions if ran_dimensions is not None else set(configured)

        contributions: dict[QualityDimension, dict[str, float]] = {d: {} for d in configured}
        impact_sum: dict[QualityDimension, float] = {d: 0.0 for d in configured}
        for issue in issues:
            dims = {_canonical(d) for d in issue.quality_dimensions}
            dims = {d for d in dims if d in configured}
            for dim in dims:
                impact = self._issue_impact(issue)
                impact_sum[dim] += impact
                contributions[dim][issue.id] = impact

        scored_values: dict[QualityDimension, float] = {}
        for dim in configured:
            if dim not in covered:
                continue
            n = len(contributions[dim])
            if n == 0:
                scored_values[dim] = 100.0
                continue
            max_possible = n * _MAX_DIMENSION_DEDUCTION
            score = 100.0 * (1.0 - impact_sum[dim] / max_possible)
            scored_values[dim] = round(min(100.0, max(0.0, score)), 1)

        scored = list(scored_values)
        if not scored:
            raise ValueError("no dimension scored: no detectors ran")

        used_weights = {d: configured[d] / sum(configured[d] for d in scored) for d in scored}
        overall = round(sum(used_weights[d] * scored_values[d] for d in scored), 1)
        notes = self._notes(configured, used_weights, scored)
        dimension_scores = {d: scored_values.get(d) for d in configured}
        return QualityScore(
            overall=overall,
            dimensions={d.value: dimension_scores[d] for d in configured},
            weights={d.value: round(w, 6) for d, w in used_weights.items()},
            calculation_notes=notes,
            score_version=SCORE_VERSION,
            dimension_contributions={
                d.value: dict(contributions[d]) for d in configured if contributions[d]
            },
        )

    @staticmethod
    def _issue_impact(issue: Issue) -> float:
        """单 Issue 对该维度的扣分影响（weight_issue × severity_norm × ratio）。"""
        criticality = 1.0  # 12.4 默认 NORMAL；字段语义推断与 Contract 覆盖归 V1
        weight_issue = criticality * issue.confidence * 1.0  # 覆盖调节 MVP 固定 1.0
        return weight_issue * SEVERITY_WEIGHTS[issue.severity] * issue.affected_ratio

    @staticmethod
    def _notes(
        configured: dict[QualityDimension, float],
        used_weights: dict[QualityDimension, float],
        scored: list[QualityDimension],
    ) -> str:
        renormalized = any(abs(configured[d] - used_weights[d]) > 1e-9 for d in scored)
        tail = (
            "weights renormalized across scored dimensions" if renormalized else "default weights"
        )
        return (
            "dimension = 100 * (1 - sum(weight_issue * severity_norm * affected_ratio) "
            f"/ (n * {_MAX_DIMENSION_DEDUCTION})); severity_norm = SEVERITY_WEIGHTS (ADR-003); "
            f"field criticality = NORMAL default (contract V1); coverage adjust = 1.0 (V1); {tail}"
        )


__all__ = ["DIMENSION_WEIGHTS", "SCORE_VERSION", "QualityScoreEngine"]
