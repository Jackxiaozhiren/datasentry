"""评分引擎（Step 8 / 12.8 + ADR-002）。

Priority Score (0–100) =
    10 × severity_weight + 25 × confidence + 15 × affected_scope
  + 10 × criticality_term + 5 × reproducibility + 15 × agreement
  + 10 × novelty + 10 × repairability

- affected_scope = min(1, affected_ratio / 0.05)   # 5% 及以上即满分
- agreement      = min(1, num_detectors_agreeing / 3)
- criticality_term = 10 × (weight − 0.6) / 1.0（ADR-002 归一化 ∈ [0, 10]）
- 最终 clamp 到 [0, 100]

MVP 项默认值（无历史/无修复引擎阶段的显式约定，SDK 可覆盖）：
- criticality：NORMAL（字段语义推断与 Contract 覆盖归 V1/Step 13+）
- reproducibility：1.0（确定性检测器同数据重扫必重现）
- novelty：1.0（首次扫描无历史对比；历史对比接入后由扫描历史计算）
- repairability：0.5（中性；修复引擎 Step 15 之后按提案与风险计算）

12.8 要求 UI 展示分数构成（条形分解）而非黑盒数字 → ScoreBreakdown。
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from datasentry_core.models.enums import BusinessCriticality
from datasentry_core.models.issue import Issue
from datasentry_core.scoring.weights import SEVERITY_WEIGHTS, criticality_norm

WEIGHTS = {
    "severity": 10,
    "confidence": 25,
    "affected_scope": 15,
    "criticality": 10,
    "reproducibility": 5,
    "agreement": 15,
    "novelty": 10,
    "repairability": 10,
}

SCOPE_FULL_RATIO = 0.05
AGREEMENT_FULL_DETECTORS = 3


class ScoreBreakdown(BaseModel):
    """分数构成（12.8 条形分解），各分量为权重 × 取值。"""

    severity: float = Field(ge=0.0)
    confidence: float = Field(ge=0.0)
    affected_scope: float = Field(ge=0.0)
    criticality: float = Field(ge=0.0)
    reproducibility: float = Field(ge=0.0)
    agreement: float = Field(ge=0.0)
    novelty: float = Field(ge=0.0)
    repairability: float = Field(ge=0.0)

    @property
    def total(self) -> float:
        """clamp 前的加权和。"""
        return (
            self.severity
            + self.confidence
            + self.affected_scope
            + self.criticality
            + self.reproducibility
            + self.agreement
            + self.novelty
            + self.repairability
        )


class ScoreResult(BaseModel):
    """评分结果：clamp 后的总分 + 构成分解。"""

    priority_score: float = Field(ge=0.0, le=100.0)
    breakdown: ScoreBreakdown


class ScoringEngine:
    """12.8 Priority Score 评分引擎（纯函数式，无状态）。"""

    def score(
        self,
        issue: Issue,
        criticality: BusinessCriticality = BusinessCriticality.NORMAL,
        *,
        reproducibility: float = 1.0,
        novelty: float = 1.0,
        repairability: float = 0.5,
    ) -> ScoreResult:
        breakdown = self._breakdown(issue, criticality, reproducibility, novelty, repairability)
        return ScoreResult(
            priority_score=round(min(100.0, max(0.0, breakdown.total)), 2),
            breakdown=breakdown,
        )

    def _breakdown(
        self,
        issue: Issue,
        criticality: BusinessCriticality,
        reproducibility: float,
        novelty: float,
        repairability: float,
    ) -> ScoreBreakdown:
        return ScoreBreakdown(
            severity=WEIGHTS["severity"] * SEVERITY_WEIGHTS[issue.severity],
            confidence=WEIGHTS["confidence"] * issue.confidence,
            affected_scope=WEIGHTS["affected_scope"]
            * min(1.0, issue.affected_ratio / SCOPE_FULL_RATIO),
            criticality=WEIGHTS["criticality"] * criticality_norm(criticality),
            reproducibility=WEIGHTS["reproducibility"] * reproducibility,
            agreement=WEIGHTS["agreement"]
            * min(1.0, len(issue.detector_ids) / AGREEMENT_FULL_DETECTORS),
            novelty=WEIGHTS["novelty"] * novelty,
            repairability=WEIGHTS["repairability"] * repairability,
        )

    def apply(
        self,
        issue: Issue,
        criticality: BusinessCriticality = BusinessCriticality.NORMAL,
        *,
        reproducibility: float = 1.0,
        novelty: float = 1.0,
        repairability: float = 0.5,
    ) -> Issue:
        """返回填充 priority_score 后的 Issue 副本（不修改原对象）。"""
        return issue.model_copy(
            update={
                "priority_score": self.score(
                    issue,
                    criticality,
                    reproducibility=reproducibility,
                    novelty=novelty,
                    repairability=repairability,
                ).priority_score
            }
        )
