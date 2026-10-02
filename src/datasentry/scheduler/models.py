"""Step 51（V2-D 云侧调度）领域模型：计划任务与执行记录（ADR-051）。"""

from __future__ import annotations

import ipaddress
import socket
from collections.abc import Callable
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any
from urllib.parse import urlsplit

from pydantic import BaseModel, Field, field_validator

from datasentry_core.models.scan import ScanConfig

_ISO = "%Y-%m-%dT%H:%M:%S"

# D5-03, P26 option B: the address classes a server-side webhook fetch may never reach, and the
# ones the project documents as its use case (notifying a service on the same box or LAN).
# Deny-by-class is checked against *resolved* addresses, so decimal (2130706433), hex, short form
# (127.1) and `http://good.example@169.254.169.254/` decoys cannot walk around it -- those are
# exactly what the OS resolver expands.
DENIED_NETWORKS = (
    ipaddress.ip_network("0.0.0.0/8"),
    ipaddress.ip_network("100.64.0.0/10"),
    ipaddress.ip_network("169.254.0.0/16"),
    ipaddress.ip_network("224.0.0.0/4"),
    ipaddress.ip_network("240.0.0.0/4"),
    ipaddress.ip_network("::/128"),
    ipaddress.ip_network("fe80::/10"),
    ipaddress.ip_network("ff00::/8"),
)
ALLOWED_PRIVATE = (
    ipaddress.ip_network("10.0.0.0/8"),
    ipaddress.ip_network("172.16.0.0/12"),
    ipaddress.ip_network("192.168.0.0/16"),
)


def _address_is_denied(address: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    # `http://[::ffff:169.254.169.254]/` is the metadata address wearing IPv6 spelling: an
    # IPv6Address compared against an IPv4Network is silently False, so every v4 range in the two
    # tables below would be judged against the outer address and missed. Unwrap first, then judge.
    mapped = getattr(address, "ipv4_mapped", None)
    if mapped is not None:
        address = mapped
    if address.is_loopback or any(address in net for net in ALLOWED_PRIVATE):
        return False  # the documented local/LAN notification case
    return (
        address.is_unspecified
        or address.is_link_local
        or address.is_multicast
        or address.is_reserved
        or any(address in net for net in DENIED_NETWORKS)
    )


def webhook_target_refusal(
    url: str,
    resolver: Callable[[str, int], list[Any]] = socket.getaddrinfo,
) -> str | None:
    """Why this webhook target must not be fetched from the server, or None if it may.

    Called at delivery time, not only at registration: the address that matters is the one the
    resolver answers with. Residual, stated rather than hidden: httpx resolves again when it
    connects, so a DNS record flipped between this check and the request is not covered -- pinning
    the connection to the checked address would be, and is left as a follow-up.
    """
    host = urlsplit(url).hostname
    if not host:
        return f"webhook_url has no host to deliver to: {url!r}"
    try:
        literal = ipaddress.ip_address(host.strip("[]"))
    except ValueError:
        literal = None
    if literal is not None:
        return (
            None if not _address_is_denied(literal) else f"{host} is a non-routable address class"
        )
    try:
        resolved = {ipaddress.ip_address(item[4][0]) for item in resolver(host, 0)}
    except (OSError, ValueError):
        # Nothing can be delivered to a host that does not resolve, so letting the attempt proceed
        # is not an opening: the request fails on its own. Refusing here would only make the rule
        # untestable and would also refuse hosts whose DNS is merely unavailable, where the
        # attacker-relevant case -- a name that resolves to a metadata address -- is caught above.
        return None
    for address in sorted(resolved, key=str):
        if _address_is_denied(address):
            return f"{host} resolves to {address}, a non-routable address class"
    return None


def _check_webhook_scheme(value: str | None) -> str | None:
    """D5-03：webhook 只允许 http/https（单点校验，覆盖 REST/MCP/CLI 三端）。

    拦截 file:///gopher:// 等非 HTTP scheme，杜绝服务端代打任意协议。

    D5-03 / P26 的行 r4 把「地址类别」这一层从"暂不拦截"改为**投递时拦截**：
    `webhook_target_refusal()` 在真正发请求前对解析后的地址判定，拒绝
    link-local/元数据、保留、多播、unspecified 与 CGNAT；loopback 与 RFC1918
    仍放行，因为「通知本机/内网服务」是本项目写明的使用场景。之所以不在这里
    做判定：十进制 `2130706433`、短写 `127.1`、`http://good.example@169.254.169.254/`
    这类写法要到解析阶段才显出真实目的地。test-webhook 不再回显远端状态码。
    """
    if value is None:
        return None
    scheme = value.split("://", 1)[0].lower() if "://" in value else ""
    if scheme not in {"http", "https"}:
        raise ValueError(f"webhook_url must use http(s) scheme, got {value!r}")
    return value


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
    CANCELLED = "cancelled"


class JobCommand(BaseModel):
    """计划任务执行的扫描命令（持久化为 JSON）。"""

    project: str
    path: str
    dataset_id: str | None = None
    table_name: str | None = None
    export_report: bool = False
    config: ScanConfig | None = None
    # V22（Step 115，ADR-115）：本次执行的调度端 run_id——Scheduler 每次
    # 触发注入（内部实现细节，不持久化；旧命令 JSON 缺省 None 向后兼容）。
    # worker 用它作 cancel 标记的 run_token（worker 无 job 概念）。
    run_token: str | None = None

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
    report_path: str | None = None
    report_size: int | None = None
    # V22（Step 115，ADR-115）：远端执行期间被调度端取消 → 结果作废回执
    # （可选字段，本地路径与旧契约零变化）。
    cancelled: bool = False


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
    export_report: bool = False
    config: ScanConfig | None = None

    @field_validator("webhook_url")
    @classmethod
    def _validate_webhook(cls, value: str | None) -> str | None:
        return _check_webhook_scheme(value)


class JobUpdate(BaseModel):
    """PATCH /jobs/{job_id} 可更新字段（None = 不变）。"""

    enabled: bool | None = None
    cron: str | None = None
    retry_attempts: int | None = Field(default=None, ge=0, le=10)
    webhook_url: str | None = None
    gate_quality_min: float | None = Field(default=None, ge=0.0, le=100.0)

    @field_validator("webhook_url")
    @classmethod
    def _validate_webhook(cls, value: str | None) -> str | None:
        return _check_webhook_scheme(value)


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
    export_report: bool = False
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
            "export_report": self.export_report,
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
