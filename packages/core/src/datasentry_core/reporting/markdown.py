"""Markdown 报告渲染（26.1：表格化摘要 + 关键证据）。"""

from __future__ import annotations

from datasentry_core.reporting import Report, critical_findings


def render_markdown(report: Report) -> str:
    """26 章报告 → 表格化 Markdown（PR/文档场景）。"""
    lines: list[str] = [
        "# DataSentry Data Quality Report",
        "",
        f"- report_schema_version: `{report['report_schema_version']}`",
        f"- datasentry_version: `{report['datasentry_version']}`",
        f"- scan_run_id: `{report['scan_run_id']}`",
        f"- generated_at: `{report['generated_at']}`",
        f"- reproducible: `{report['reproducible']}` / llm_used: `{report['llm_used']}`",
        "",
        "## Executive Summary",
        "",
    ]
    scan = report["scan"]
    quality = report["quality"]
    overall = f"{quality['overall']:.1f}" if quality else "n/a (not scored)"
    lines.append(
        f"| Overall | Issues | Detectors | Rows | Columns |\n"
        f"|---|---|---|---|---|\n"
        f"| {overall} | {len(report['issues'])} | {len(report['detector_runs'])} "
        f"| {scan['fingerprint']['row_count']} | {scan['fingerprint']['column_count']} |"
    )
    lines += ["", "## Quality Score", ""]
    if quality:
        lines.append("| Dimension | Score | Weight |")
        lines.append("|---|---|---|")
        for dim, value in quality["dimensions"].items():
            weight = quality["weights"].get(dim)
            lines.append(
                f"| {dim} | {value if value is not None else 'n/a (no detector)'} "
                f"| {weight if weight is not None else '-'} |"
            )
        lines += [
            "",
            f"score_version: `{quality['score_version']}`",
            "",
            f"formula: {quality['calculation_notes']}",
        ]
    else:
        lines.append("_not scored_")
    lines += ["", "## Issue Breakdown", ""]
    if report["issues"]:
        lines.append("| Severity | Priority | Issue | Columns | Detectors |")
        lines.append("|---|---|---|---|---|")
        for issue in report["issues"]:
            lines.append(
                f"| {issue['severity']} | {issue['priority_score']:.1f} "
                f"| {_escape_cell(issue['title'])} | {', '.join(issue['columns'])} "
                f"| {', '.join(issue['detector_ids'])} |"
            )
    else:
        lines.append("_无 Issue_")
    findings = critical_findings(report)
    if findings:
        lines += ["", "## Critical Findings", ""]
        for issue in findings:
            lines.append(
                f"- **[{issue['severity']}]** {issue['title']} "
                f"(priority={issue['priority_score']:.1f}, "
                f"affected={issue['affected_count']} rows, ratio={issue['affected_ratio']:.4f})"
            )
    lines += ["", "## Reproducibility", ""]
    repro = scan["reproducibility"]
    lines.append(
        f"- datasentry_version: `{repro['datasentry_version']}`\n"
        f"- detector_versions: `{repro['detector_versions']}`\n"
        f"- seed: `{repro['seed']}`\n"
        f"- scanned_at: `{repro['scanned_at']}`"
    )
    return "\n".join(lines) + "\n"


def _escape_cell(value: str) -> str:
    """Markdown 表格单元格转义（竖线/换行防破坏表格）。"""
    return value.replace("|", "\\|").replace("\n", " ")
