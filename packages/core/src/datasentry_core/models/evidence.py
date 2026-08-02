"""证据模型（12.1/12.3）。"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, Field

from datasentry_core.models.enums import EvidenceType


def utcnow() -> datetime:
    """带时区的当前 UTC 时间（默认工厂，保证可复现时区语义）。"""
    return datetime.now(UTC)


class EvidenceProvenance(BaseModel):
    """证据来源链（12.3），保证可追溯。"""

    scan_run_id: str | None = None
    detector_run_id: str | None = None
    source_type: str = Field(default="detector", pattern=r"^(detector|rule|user|llm)$")
    rule_id: str | None = None
    created_at: datetime = Field(default_factory=utcnow)


class Evidence(BaseModel):
    """单条结构化证据（12.1）。"""

    evidence_id: str
    evidence_type: EvidenceType
    detector_id: str
    detector_version: str
    description: str
    data: dict[str, Any] = Field(default_factory=dict)
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)
    provenance: EvidenceProvenance | None = None
    created_at: datetime = Field(default_factory=utcnow)
