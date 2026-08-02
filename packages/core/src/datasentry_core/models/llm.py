"""LLM 相关模型（13.7/13.11/53 章）。"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

from datasentry_core.models.enums import RiskLevel
from datasentry_core.models.evidence import utcnow


class CauseHypothesis(BaseModel):
    """根因候选（13.7）：kind 限定，禁止自由断言。"""

    description: str
    evidence_ids: list[str] = Field(default_factory=list)
    confidence: float = Field(ge=0.0, le=1.0)
    kind: Literal["data_entry", "integration", "migration", "measurement", "systematic", "unknown"]


class RecommendedAction(BaseModel):
    """推荐行动（13.7）：action_type 限定。"""

    action_type: Literal["inspect", "rule", "repair", "ignore", "escalate"]
    description: str
    risk: RiskLevel = RiskLevel.LOW


class AIExplanation(BaseModel):
    """AI 解释输出（13.7），必须经结构化校验；supporting_evidence_ids 须 ⊆ 实际证据集。"""

    summary: str
    likely_causes: list[CauseHypothesis] = Field(default_factory=list)
    supporting_evidence_ids: list[str] = Field(default_factory=list)
    assumptions: list[str] = Field(default_factory=list)
    uncertainty: str = ""
    recommended_actions: list[RecommendedAction] = Field(default_factory=list)


class LLMUsageSummary(BaseModel):
    """扫描级 LLM 用量汇总（18.2 引用，13.9 预算口径）。"""

    calls: int = Field(default=0, ge=0)
    tokens_in: int = Field(default=0, ge=0)
    tokens_out: int = Field(default=0, ge=0)
    cache_hits: int = Field(default=0, ge=0)
    degraded: bool = False


class LLMInvocation(BaseModel):
    """单次 LLM 调用审计（13.11）：日志与报告只展示字段名与统计量。"""

    invocation_id: str
    task_type: str
    template_version: str
    provider_id: str
    model: str
    input_tokens: int = Field(ge=0)
    output_tokens: int = Field(ge=0)
    cache_hit: bool = False
    latency_ms: int = Field(ge=0)
    status: Literal["ok", "retried", "schema_failed", "failed", "degraded"]
    prompt_hash: str
    masked_sample_count: int = Field(default=0, ge=0)
    injection_flagged: bool = False
    error_message: str | None = None
    created_at: datetime = Field(default_factory=utcnow)
