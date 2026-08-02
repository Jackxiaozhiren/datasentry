"""检测器接口模型（11 章：IssueCandidate/DetectionContext/DetectorMeta）。"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

from datasentry_core.models.enums import QualityDimension
from datasentry_core.models.evidence import Evidence

ConfigFieldType = Literal["int", "float", "str", "bool"]


class ConfigField(BaseModel):
    """检测器可配置字段声明（供 UI 表单与文档生成）。"""

    name: str
    type: ConfigFieldType
    default: Any = None
    description: str = ""
    min: float | None = None
    max: float | None = None


class DetectorCapabilities(BaseModel):
    """执行策略能力声明（11.2）：按优先级选择执行路径。"""

    requires_full_scan: bool = False
    supports_sampling: bool = True
    supports_streaming: bool = False
    supports_sql_pushdown: bool = False
    requires_row_materialization: bool = False


class DetectorMeta(BaseModel):
    """注册信息（11.1，供 UI 展示与文档生成）。"""

    detector_id: str
    display_name: str
    description: str
    quality_dimension: QualityDimension
    capabilities: DetectorCapabilities = Field(default_factory=DetectorCapabilities)
    default_thresholds: dict[str, float | int | str] = Field(default_factory=dict)
    configurable_fields: list[ConfigField] = Field(default_factory=list)
    needs_llm: bool = False
    requires_reference: bool = False
    experimental: bool = False


class IssueCandidate(BaseModel):
    """单检测器输出（11 章）：Step 7 融合为 Issue 前的中间产物。"""

    issue_type: str
    detector_id: str
    detector_version: str
    dataset_id: str
    table_name: str | None = None
    columns: list[str]
    affected_rows: list[str] | None = None
    affected_count: int = Field(ge=0)
    evidence: list[Evidence] = Field(default_factory=list)
    raw_score: float = Field(ge=0.0)
    confidence: float = Field(ge=0.0, le=1.0)
    estimated_false_positive_risk: float = Field(ge=0.0, le=1.0)
    suggested_severity: str
