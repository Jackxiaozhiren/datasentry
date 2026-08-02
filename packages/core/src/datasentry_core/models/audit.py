"""审计事件模型（18.4）。"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

from datasentry_core.models.evidence import utcnow

AuditEventType = Literal[
    "scan_started",
    "scan_finished",
    "issue_status_changed",
    "contract_created",
    "contract_updated",
    "rule_created",
    "rule_enabled",
    "rule_disabled",
    "repair_proposed",
    "repair_previewed",
    "repair_approved",
    "repair_rejected",
    "repair_applied",
    "repair_rolled_back",
    "semantic_override",
    "llm_invoked",
    "data_source_added",
    "data_source_removed",
    "export_generated",
    "feedback_submitted",
]


class AuditEvent(BaseModel):
    """不可变审计事件（18.4）：应用层禁止 UPDATE/DELETE。"""

    event_id: str
    event_type: AuditEventType
    actor: str = "local-user"
    project_id: str
    resource_type: str | None = None
    resource_id: str | None = None
    details: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=utcnow)
