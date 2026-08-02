"""验证结果模型（18.2）。"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field

from datasentry_core.models.evidence import utcnow


class ValidationResult(BaseModel):
    """规则验证结果（18.2）：用于质量门禁与修复后验证。"""

    rule_id: str
    rule_version: int = Field(ge=1)
    failures: int = Field(ge=0)
    rows_tested: int = Field(ge=0)
    failure_ratio: float = Field(ge=0.0, le=1.0)
    example_row_ids: list[str] = Field(default_factory=list)
    duration_ms: int = Field(ge=0)
    ran_at: datetime = Field(default_factory=utcnow)
