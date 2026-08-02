"""用户反馈模型（31.1）。反馈学习为 V1，模型先行定义。"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

from datasentry_core.models.enums import Severity
from datasentry_core.models.evidence import utcnow


class FeedbackEntry(BaseModel):
    """用户对 Issue 的反馈（31.1）。"""

    feedback_id: str
    issue_id: str
    label: Literal[
        "true_issue",
        "false_positive",
        "expected_exception",
        "need_more_context",
        "wrong_severity",
        "wrong_repair",
    ]
    severity_correction: Severity | None = None
    repair_rating: Literal["good", "bad"] | None = None
    note: str = ""
    created_at: datetime = Field(default_factory=utcnow)
