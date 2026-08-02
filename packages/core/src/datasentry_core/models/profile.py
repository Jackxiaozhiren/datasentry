"""数据画像模型（18.2/10 章语义推断）。"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

from datasentry_core.models.evidence import utcnow


class SemanticEvidence(BaseModel):
    """语义推断的单条证据（10 章输出示例）。"""

    type: str
    value: Any
    weight: float = Field(ge=0.0, le=1.0)


class SemanticProfile(BaseModel):
    """字段语义画像（10 章）：确定性推断（Phase A）与用户纠正的输出载体。"""

    column_name: str
    physical_type: str
    semantic_type: str = "unknown"
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    evidence: list[SemanticEvidence] = Field(default_factory=list)
    contains_pii: bool = False
    confidence_band: Literal["confident", "ambiguous"] = "ambiguous"


class ColumnProfile(BaseModel):
    """单列画像（18.2）。examples 必须为脱敏样本。"""

    dataset_id: str
    column_name: str
    physical_type: str
    semantic_type: str = "unknown"
    semantic_confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    contains_pii: bool = False
    null_ratio: float = Field(default=0.0, ge=0.0, le=1.0)
    unique_ratio: float = Field(default=0.0, ge=0.0, le=1.0)
    distinct_count: int = Field(default=0, ge=0)
    min: Any | None = None
    q25: float | None = None
    median: float | None = None
    q75: float | None = None
    max: Any | None = None
    mean: float | None = None
    std: float | None = None
    top_categories: list[tuple[str, int]] | None = None
    pattern_summary: dict[str, float] | None = None
    examples: list[str] = Field(default_factory=list)
    issues_reference: list[str] = Field(default_factory=list)


class SamplingInfo(BaseModel):
    """抽样信息（20.3）：报告中必须注明抽样方法与可推广性。"""

    sampled: bool
    method: Literal["random", "stratified", "reservoir", "time_based", "rare_oversampling", "none"]
    sample_size: int = Field(default=0, ge=0)
    full_size: int = Field(default=0, ge=0)
    generalizable: bool = False
    full_stats_columns: list[str] = Field(default_factory=list)


class DatasetProfile(BaseModel):
    """数据集画像（18.2）。"""

    dataset_id: str
    row_count: int = Field(ge=0)
    column_count: int = Field(ge=0)
    memory_estimate_mb: float = Field(default=0.0, ge=0.0)
    sampling: SamplingInfo | None = None
    column_profiles: dict[str, ColumnProfile] = Field(default_factory=dict)
    profiled_at: datetime = Field(default_factory=utcnow)
    profiler_version: str
