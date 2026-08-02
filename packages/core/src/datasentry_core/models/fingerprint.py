"""数据指纹与版本模型（18.2/19 章）。"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

from datasentry_core.models.evidence import utcnow


class DatasetFingerprint(BaseModel):
    """数据集内容指纹（19 章：full/sampled/metadata_only）。"""

    dataset_id: str
    fingerprint_type: Literal["full", "sampled", "metadata_only"]
    file_sha256: str | None = None
    schema_hash: str
    row_count: int = Field(ge=0)
    column_count: int = Field(ge=0)
    column_signature: list[tuple[str, str]]
    content_sample_hash: str | None = None
    created_at: datetime = Field(default_factory=utcnow)
