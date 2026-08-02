"""规则模型（14.1~14.4）。"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

from datasentry_core.models.enums import BusinessCriticality, RuleType, Severity
from datasentry_core.models.evidence import utcnow


class Condition(BaseModel):
    """规则条件（14.1）：operator 白名单限定。"""

    column: str
    operator: Literal[
        "equals",
        "not_equals",
        "gt",
        "gte",
        "lt",
        "lte",
        "in",
        "not_in",
        "not_null",
        "is_null",
        "matches",
        "between",
        "not_between",
    ]
    value: str | int | float | list[object] | None = None
    expression: str | None = None


class Rule(BaseModel):
    """数据质量规则（14.1）：每次修改 version +1。"""

    id: str
    type: RuleType
    severity: Severity = Severity.MEDIUM
    description: str = ""
    when: Condition | None = None
    then: Condition | None = None
    expression: str | None = None
    columns: list[str] = Field(default_factory=list)
    source: Literal["user", "contract", "llm_candidate", "builtin", "learned"] = "user"
    enabled: bool = True
    criticality_override: BusinessCriticality | None = None
    created_by: str = "local-user"
    created_at: datetime = Field(default_factory=utcnow)
    version: int = Field(default=1, ge=1)


class RuleCandidate(BaseModel):
    """LLM 生成的规则候选（14.4）：必须先预运行 + 用户批准。"""

    rule: Rule
    paraphrase: str
    confidence: float = Field(ge=0.0, le=1.0)
    notes: list[str] = Field(default_factory=list)


class RulePreflightSampleRun(BaseModel):
    """规则预运行结果（14.3）。"""

    rows_tested: int = Field(ge=0)
    failures: int = Field(ge=0)
    failure_ratio: float = Field(ge=0.0, le=1.0)
    example_rows: list[str] = Field(default_factory=list)


class RulePreflightReport(BaseModel):
    """规则预运行协议输出（14.3）。"""

    rule_id: str
    valid: bool
    schema_valid: bool
    columns_exist: list[str] = Field(default_factory=list)
    dangerous: bool = False
    sample_run: RulePreflightSampleRun | None = None
