"""修复引擎模型（15 章）。"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

from datasentry_core.models.enums import (
    RepairOperation,
    RepairProposalStatus,
    RepairRunStatus,
    RiskLevel,
)
from datasentry_core.models.evidence import utcnow


class RepairProposal(BaseModel):
    """修复提案（15.3）。"""

    proposal_id: str
    issue_id: str
    operation: RepairOperation
    target_columns: list[str]
    target_row_ids: list[str] | None = None
    parameters: dict[str, Any] = Field(default_factory=dict)
    rationale: str = ""
    evidence_ids: list[str] = Field(default_factory=list)
    risk_level: RiskLevel = RiskLevel.MEDIUM
    reversibility: Literal["fully_reversible", "partially_reversible", "irreversible"] = (
        "fully_reversible"
    )
    estimated_rows_changed: int = Field(ge=0)
    preconditions: list[str] = Field(default_factory=list)
    postconditions: list[str] = Field(default_factory=list)
    status: RepairProposalStatus = RepairProposalStatus.PROPOSED
    created_at: datetime = Field(default_factory=utcnow)


class RowBeforeAfter(BaseModel):
    """修复预览的单行变更（15.5/15.6）。"""

    row_id: str
    column: str
    before: Any | None = None
    after: Any | None = None
    reason: str = ""
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)


class RepairPreview(BaseModel):
    """修复预览统计面板（15.6）：必须能回答规则通过率变化。"""

    proposal_id: str
    rows_changed: int = Field(ge=0)
    rows_changed_ratio: float = Field(ge=0.0, le=1.0)
    null_delta: dict[str, int] = Field(default_factory=dict)
    unique_delta: dict[str, int] = Field(default_factory=dict)
    distribution_shift: dict[str, str] = Field(default_factory=dict)
    rule_failures_after: dict[str, int] = Field(default_factory=dict)
    rule_failures_before: dict[str, int] = Field(default_factory=dict)
    side_effects: list[str] = Field(default_factory=list)
    changed_examples: list[RowBeforeAfter] = Field(default_factory=list)


class RepairOperationRecord(BaseModel):
    """行级 before/after 记录（15.8），回滚的数据源。"""

    row_id: str
    column: str
    operation: RepairOperation
    before: Any | None = None
    after: Any | None = None


class RepairRun(BaseModel):
    """一次已执行的修复事务（15.7）：不可变日志 + 可回滚。"""

    id: str
    dataset_id: str
    proposal_id: str | None = None
    dataset_version_from: str | None = None
    dataset_version_to: str | None = None
    fingerprint_before: str
    fingerprint_after: str | None = None
    operations: list[RepairOperationRecord] = Field(default_factory=list)
    approved_by: str = "local-user"
    approval_kind: Literal["manual", "yes_typed"] = "manual"
    approved_at: datetime | None = None
    status: RepairRunStatus = RepairRunStatus.APPLIED
    rollback_artifact: str | None = None
    created_at: datetime = Field(default_factory=utcnow)
