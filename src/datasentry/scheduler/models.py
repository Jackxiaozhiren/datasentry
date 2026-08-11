"""Step 51（V2-D 云侧调度）领域模型：计划任务与执行记录（ADR-051）。"""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field

_ISO = "%Y-%m-%dT%H:%M:%S"


def iso(dt: datetime) -> str:
    return dt.strftime(_ISO)


def from_iso(value: str) -> datetime:
    return datetime.strptime(value, _ISO)


def utcnow() -> datetime:
    """当前 UTC 时间（naive，与 core 存储的 `_iso` 惯例一致）。"""
    return datetime.now(UTC).replace(tzinfo=None)


class JobStatus(StrEnum):
    IDLE = "idle"
    QUEUED = "queued"
    RUNNING = "running"
    DEAD = "dead"


class RunStatus(StrEnum):
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


class JobCommand(BaseModel):
    """计划任务执行的扫描命令（持久化为 JSON）。"""

    project: str
    path: str
    dataset_id: str | None = None
    table_name: str | None = None

    def to_storage(self) -> str:
        return self.model_dump_json()

    @classmethod
    def from_storage(cls, raw: str) -> JobCommand:
        return cls.model_validate_json(raw)


class JobResult(BaseModel):
    """一次扫描执行的结果摘要（供 webhook / 状态展示）。"""

    scan_run_id: str | None = None
    total_issues: int = 0
    quality_score: float = 0.0
    issues_by_severity: dict[str, int] = Field(default_factory=dict)
    gate: GateResult | None = None
    file_hash: str | None = None
    skipped: bool = False


class JobCreate(BaseModel):
    """POST /jobs 请求体。"""

    name: str = Field(min_length=1, max_length=200)
    path: str = Field(min_length=1)
    project: str | None = None
    dataset_id: str | None = None
    table_name: str | None = None
    cron: str = Field(min_length=5, max_length=100)
    retry_attempts: int = Field(default=0, ge=0, le=10)
    webhook_url: str | None = Field(default=None, max_length=500)
    gate_quality_min: float | None = Field(default=None, ge=0.0, le=100.0)


class JobUpdate(BaseModel):
    """PATCH /jobs/{job_id} 可更新字段（None = 不变）。"""

    enabled: bool | None = None
    cron: str | None = None
    retry_attempts: int | None = Field(default=None, ge=0, le=10)
    webhook_url: str | None = None
    gate_quality_min: float | None = Field(default=None, ge=0.0, le=100.0)


class GateResult(BaseModel):
    """质量门禁判定（Step 52）：未配置门禁时 passed 为 None。"""

    configured: bool
    quality_min: float | None
    quality_score: float | None
    passed: bool | None


class ScheduledJob(BaseModel):
    """计划任务（持久化行）。"""

    job_id: str
    name: str
    project: str
    command: JobCommand
    cron: str
    enabled: bool = True
    retry_attempts: int = 0
    webhook_url: str | None = None
    gate_quality_min: float | None = None
    status: JobStatus = JobStatus.IDLE
    next_run_at: datetime
    last_run_at: datetime | None = None
    last_result: str | None = None
    created_at: datetime
    updated_at: datetime

    def view(self) -> dict[str, Any]:
        """API 视图（JSON 友好，PII 无关）。"""
        return {
            "job_id": self.job_id,
            "name": self.name,
            "project": self.project,
            "command": self.command.model_dump(),
            "cron": self.cron,
            "enabled": self.enabled,
            "retry_attempts": self.retry_attempts,
            "webhook_url": self.webhook_url,
            "gate_quality_min": self.gate_quality_min,
            "status": self.status.value,
            "next_run_at": iso(self.next_run_at),
            "last_run_at": iso(self.last_run_at) if self.last_run_at else None,
            "last_result": self.last_result,
        }


class JobRun(BaseModel):
    """一次执行记录（attempt 级别）。"""

    run_id: str
    job_id: str
    status: RunStatus
    attempt: int = 0
    started_at: datetime
    finished_at: datetime | None = None
    scan_run_id: str | None = None
    summary: str | None = None
    error: str | None = None
    webhook_at: datetime | None = None
    file_hash: str | None = None
    skipped: bool = False

    def view(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "job_id": self.job_id,
            "status": self.status.value,
            "attempt": self.attempt,
            "started_at": iso(self.started_at),
            "finished_at": iso(self.finished_at) if self.finished_at else None,
            "scan_run_id": self.scan_run_id,
            "summary": self.summary,
            "error": self.error,
            "file_hash": self.file_hash,
            "skipped": self.skipped,
        }
