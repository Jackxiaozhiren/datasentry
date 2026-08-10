"""报告引擎（Step 12 / 26 章 + ADR-014）。

26.2 报告头（所有格式一致包含）：

    report_schema_version, datasentry_version, scan_run_id,
    generated_at, reproducible, llm_used

JSON 报告即 26.1 机器契约（build_report 返回的可序列化 dict），
CLI `report export --as json` 与 API/SDK 消费同一结构（26.2 无差异消费）。
HTML/Markdown 为同一 dict 的纯函数渲染（无外部依赖，HTML 单文件自包含）。
"""

from __future__ import annotations

import re
from datetime import UTC, datetime
from typing import Any

from datasentry_core import __version__
from datasentry_core.models.issue import Issue
from datasentry_core.models.quality import QualityScore
from datasentry_core.models.scan import DetectorRun, ScanRun
from datasentry_core.privacy.redactor import redact

#: 26 章规范报告结构（build_report 返回，JSON 机器契约）
type Report = dict[str, Any]

REPORT_SCHEMA_VERSION = "1.0"
CRITICAL_FINDINGS_LIMIT = 5

#: 26 章 HTML 节中 MVP 可填充的节（Drift/规则/修复历史归 V1；
#: Column Profiles 内容并入 dataset_overview 的列签名区）
HTML_SECTIONS = (
    "executive_summary",
    "dataset_overview",
    "quality_score",
    "issue_breakdown",
    "critical_findings",
    "methodology",
    "reproducibility",
)


def build_report(
    scan: ScanRun,
    runs: list[DetectorRun],
    issues: list[Issue],
    quality: QualityScore | None,
    *,
    generated_at: datetime | None = None,
) -> Report:
    """26 章规范报告：头（26.2）+ scan + detector_runs + issues + quality。"""
    return {
        "report_schema_version": REPORT_SCHEMA_VERSION,
        "datasentry_version": __version__,
        "scan_run_id": scan.id,
        "generated_at": (generated_at or datetime.now(UTC)).isoformat(),
        "reproducible": True,  # MVP 确定性检测器 + 无 LLM（ADR-014）
        "llm_used": False,
        "scan": scan.model_dump(mode="json"),
        "detector_runs": [r.model_dump(mode="json") for r in runs],
        "issues": [i.model_dump(mode="json") for i in issues],
        "quality": quality.model_dump(mode="json") if quality else None,
    }


def _severity_rank(severity: str) -> int:
    return {"critical": 0, "high": 1, "medium": 2, "low": 3, "info": 4}.get(severity, 9)


_PII_HOLDER_RE = re.compile(r"\{\{REDACTED:\w+:\d+\}\}")


def mask_text_pii(text: str) -> str:
    """人类可读输出前的 PII 防御性掩码（Step 48，ADR-048）。

    命中的 PII（email/手机号/身份证/IP/URL）替换为 [REDACTED]；
    无 PII 文本原样返回。JSON 机器契约不调用（保留完整证据链）。
    """
    if not text:
        return text
    return _PII_HOLDER_RE.sub("[REDACTED]", redact(text).masked)


def critical_findings(report: Report) -> list[dict[str, Any]]:
    """Critical Findings：按严重度与 priority_score 排序取前 N。"""
    return sorted(
        report["issues"],
        key=lambda i: (_severity_rank(i["severity"]), -i["priority_score"]),
    )[:CRITICAL_FINDINGS_LIMIT]


__all__ = [
    "CRITICAL_FINDINGS_LIMIT",
    "HTML_SECTIONS",
    "REPORT_SCHEMA_VERSION",
    "Report",
    "build_report",
    "critical_findings",
    "render_junit",
    "render_sarif",
]


def render_junit(report: Report) -> str:
    """26 章报告 → JUnit XML（28 章 CI）。"""
    from datasentry_core.reporting.junit import render_junit as _render

    return _render(report)


def render_sarif(report: Report) -> dict[str, Any]:
    """26 章报告 → SARIF 2.1.0 字典（28 章 CI）。"""
    from datasentry_core.reporting.sarif import render_sarif as _render

    return _render(report)
