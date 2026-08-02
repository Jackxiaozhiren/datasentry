"""Issue 模型（18.1/12 章）。"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field

from datasentry_core.models.enums import IssueStatus, QualityDimension, RiskLevel, Severity
from datasentry_core.models.evidence import Evidence, utcnow
from datasentry_core.models.llm import AIExplanation
from datasentry_core.models.repair import RepairProposal


class Issue(BaseModel):
    """融合后的统一质量问题（18.1）。"""

    id: str
    scan_run_id: str
    issue_type: str
    title: str
    description: str = ""

    dataset_id: str
    table_name: str | None = None
    columns: list[str]

    quality_dimensions: list[QualityDimension] = Field(default_factory=list)

    severity: Severity = Severity.MEDIUM
    confidence: float = Field(ge=0.0, le=1.0)
    priority_score: float = Field(ge=0.0, le=100.0)
    false_positive_risk: RiskLevel = RiskLevel.MEDIUM

    affected_count: int = Field(ge=0)
    affected_ratio: float = Field(ge=0.0, le=1.0)
    affected_row_ids: list[str] | None = None

    evidence: list[Evidence] = Field(default_factory=list)
    detector_ids: list[str] = Field(default_factory=list)

    ai_explanation: AIExplanation | None = None
    repair_proposals: list[RepairProposal] = Field(default_factory=list)

    status: IssueStatus = IssueStatus.OPEN
    created_at: datetime = Field(default_factory=utcnow)


Issue.model_rebuild()
