"""漂移模型（18.2）。漂移引擎为 V1，模型先行定义。"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

from datasentry_core.models.enums import Severity
from datasentry_core.models.evidence import utcnow


class SchemaChange(BaseModel):
    """Schema 变更记录（18.2）。"""

    change_type: Literal[
        "added", "removed", "renamed", "dtype_changed", "nullable_changed", "order_changed"
    ]
    column: str
    before: Any | None = None
    after: Any | None = None


class ColumnDrift(BaseModel):
    """单列漂移（18.2/11.12）。"""

    column: str
    drift_type: Literal["numeric", "categorical", "missingness", "uniqueness", "timeseries"]
    metric: str
    value: float
    threshold: float
    direction: Literal["increase", "decrease", "shift", "new_category", "gone_category"]
    severity: Severity = Severity.MEDIUM
    sample_sizes: tuple[int, int] = (0, 0)


class DriftReport(BaseModel):
    """漂移报告（18.2）。"""

    id: str
    reference_dataset_id: str
    current_dataset_id: str
    schema_changes: list[SchemaChange] = Field(default_factory=list)
    column_drifts: list[ColumnDrift] = Field(default_factory=list)
    issue_ids: list[str] = Field(default_factory=list)
    generated_at: datetime = Field(default_factory=utcnow)
