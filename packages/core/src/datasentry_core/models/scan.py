"""扫描运行模型（18.2）。"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

from datasentry_core.models.enums import Severity
from datasentry_core.models.evidence import utcnow
from datasentry_core.models.fingerprint import DatasetFingerprint
from datasentry_core.models.llm import LLMUsageSummary
from datasentry_core.models.profile import SamplingInfo
from datasentry_core.models.quality import QualityScore
from datasentry_core.models.rules import Rule


class SamplingConfig(BaseModel):
    """扫描抽样配置（18.2 引用，20.3 采样方法）。"""

    method: Literal[
        "random", "stratified", "reservoir", "time_based", "rare_oversampling", "none"
    ] = "random"
    sample_size: int | None = Field(default=None, ge=1)
    ratio: float | None = Field(default=None, gt=0.0, le=1.0)
    seed: int = 42
    stratified_columns: list[str] = Field(default_factory=list)
    time_column: str | None = None
    generalizable: bool = True


class MaskConfig(BaseModel):
    """脱敏配置（18.2 引用 / 13.4 算法表）：默认值即安全基线（R-PRI-02 三档预设）。"""

    policy: Literal["safe", "balanced", "permissive"] = "safe"
    email_keep_first: int = Field(default=1, ge=0)
    phone_keep_prefix_chars: int = Field(default=3, ge=0)
    phone_keep_last_chars: int = Field(default=4, ge=0)
    id_hash_prefix_len: int = Field(default=12, ge=4)
    numeric_bucket: bool = True
    free_text_max_chars: int = Field(default=200, ge=0, le=200)
    lat_lon_decimals: int = Field(default=1, ge=0)
    max_sample_rows_per_column: int = Field(default=50, ge=1)
    max_sample_rows_per_table: int = Field(default=200, ge=1)


class ScanConfig(BaseModel):
    """扫描配置（18.2）：可复现性的核心输入。"""

    detectors: list[str] | None = None
    sampling: SamplingConfig = Field(default_factory=SamplingConfig)
    seed: int = 42
    masks: MaskConfig = Field(default_factory=MaskConfig)
    llm_enabled: bool = False
    llm_budget_tokens: int = Field(default=20000, ge=0)
    custom_rules: list[Rule] = Field(default_factory=list)
    scan_tags: dict[str, str] = Field(default_factory=dict)


class DetectorRun(BaseModel):
    """单检测器执行记录（18.2）。"""

    id: str
    scan_run_id: str
    detector_id: str
    detector_version: str
    status: Literal["completed", "skipped", "failed"] = "completed"
    rows_scanned: int = Field(ge=0)
    duration_ms: int = Field(ge=0)
    issues_candidates: int = Field(ge=0)
    sampling: SamplingInfo | None = None
    error: str | None = None


class ReproducibilityInfo(BaseModel):
    """可复现性信息（18.2/4.4）。"""

    datasentry_version: str
    detector_versions: dict[str, str] = Field(default_factory=dict)
    rule_versions: dict[str, int] = Field(default_factory=dict)
    seed: int
    models_used: list[str] = Field(default_factory=list)
    prompt_template_versions: dict[str, str] = Field(default_factory=dict)
    python_version: str = ""
    os_platform: str = ""
    hardware_summary: str = ""
    scanned_at: datetime = Field(default_factory=utcnow)


def _empty_issues_count() -> dict[Severity, int]:
    """默认各严重度计数为 0。"""
    return {severity: 0 for severity in Severity}


class ScanRun(BaseModel):
    """一次完整质量扫描（18.2）。"""

    id: str
    dataset_id: str
    contract_id: str | None = None
    status: Literal["queued", "running", "completed", "failed", "cancelled"] = "queued"
    config: ScanConfig
    fingerprint: DatasetFingerprint
    quality_score: QualityScore | None = None
    issues_count: dict[Severity, int] = Field(default_factory=_empty_issues_count)
    started_at: datetime = Field(default_factory=utcnow)
    finished_at: datetime | None = None
    error: str | None = None
    reproducibility: ReproducibilityInfo
    llm_usage: LLMUsageSummary = Field(default_factory=LLMUsageSummary)
