"""质量总分模型（18.2/27 章）。"""

from __future__ import annotations

from pydantic import BaseModel, Field


class QualityScore(BaseModel):
    """质量总分（27 章）：0-100，维度可解释，权重可配。"""

    overall: float = Field(ge=0.0, le=100.0)
    dimensions: dict[str, float | None] = Field(default_factory=dict)
    weights: dict[str, float] = Field(default_factory=dict)
    calculation_notes: str = ""
    score_version: str = "1"
    dimension_contributions: dict[str, dict[str, float]] | None = Field(default=None)
