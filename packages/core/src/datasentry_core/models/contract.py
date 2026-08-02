"""数据契约模型（16.1）。契约引擎为 V1（ADR-004），模型先行定义。"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from datasentry_core.models.enums import BusinessCriticality, Severity
from datasentry_core.models.rules import Rule


class ColumnCheck(BaseModel):
    """列级检查（16.1 checks 项）。"""

    type: Literal["regex", "range", "allowed_values", "not_null", "unique", "custom"]
    pattern: str | None = None
    min: float | None = None
    max: float | None = None
    allowed_values: list[str] | None = None
    message: str = ""
    severity: Severity = Severity.MEDIUM


class ColumnContract(BaseModel):
    """列契约（16.1）。"""

    type: str = "string"
    nullable: bool = True
    unique: bool = False
    semantic_type: str = "unknown"
    pii: bool = False
    criticality: BusinessCriticality = BusinessCriticality.NORMAL
    min: float | None = None
    max: float | None = None
    allowed_values: list[str] | None = None
    regex: str | None = None
    format: str | None = None
    unit: str | None = None
    checks: list[ColumnCheck] = Field(default_factory=list)
    description: str = ""


class DatasetContract(BaseModel):
    """数据集契约（16.1）。"""

    name: str
    description: str = ""
    primary_key: list[str] = Field(default_factory=list)
    expected_rows: int | None = None
    expected_columns: list[str] | None = None
    frequency: Literal["daily", "weekly", "monthly", "adhoc"] | None = None


class QualityGate(BaseModel):
    """质量门禁（16.1/22 章场景 C）。"""

    fail_on: list[Severity] = Field(default_factory=lambda: [Severity.CRITICAL])
    maximum_failed_rows_ratio: float = Field(default=0.01, ge=0.0, le=1.0)
    maximum_issues: dict[Severity, int] | None = None
    require_repair_validation: bool = False


class Contract(BaseModel):
    """数据契约（16.1）：version 与 checksum 用于差异比较。"""

    version: str = "1.0"
    dataset: DatasetContract
    columns: dict[str, ColumnContract] = Field(default_factory=dict)
    rules: list[Rule] = Field(default_factory=list)
    quality_gate: QualityGate | None = None
    metadata: dict[str, str] = Field(default_factory=dict)
