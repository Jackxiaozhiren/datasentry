"""Markdown 报告渲染（26.1：表格化摘要 + 关键证据）。

V8（ADR-069）：框架文案经 i18n 本地化（lang 参数，默认 en）。
"""

from __future__ import annotations

from datasentry_core.reporting import Report, critical_findings, mask_text_pii
from datasentry_core.reporting.i18n import t


def render_markdown(report: Report, *, lang: str = "en") -> str:
    """26 章报告 → 表格化 Markdown（PR/文档场景）。"""
    lines: list[str] = [
        f"# {t(lang, 'report.title')}",
        "",
        f"- report_schema_version: `{report['report_schema_version']}`",
        f"- datasentry_version: `{report['datasentry_version']}`",
        f"- scan_run_id: `{report['scan_run_id']}`",
        f"- generated_at: `{report['generated_at']}`",
        f"- reproducible: `{report['reproducible']}` / llm_used: `{report['llm_used']}`",
        "",
        f"## {t(lang, 'section.executive_summary')}",
        "",
    ]
    scan = report["scan"]
    quality = report["quality"]
    overall = f"{quality['overall']:.1f}" if quality else f"n/a ({t(lang, 'md.not_scored')})"
    lines.append(
        f"{t(lang, 'md.overall_issues_detectors_rows_cols')}\n"
        f"|---|---|---|---|---|\n"
        f"| {overall} | {len(report['issues'])} | {len(report['detector_runs'])} "
        f"| {scan['fingerprint']['row_count']} | {scan['fingerprint']['column_count']} |"
    )
    lines += ["", f"## {t(lang, 'section.quality_score')}", ""]
    if quality:
        lines.append(t(lang, "md.dimension_score_weight"))
        lines.append("|---|---|---|")
        for dim, value in quality["dimensions"].items():
            weight = quality["weights"].get(dim)
            lines.append(
                f"| {dim} | {value if value is not None else t(lang, 'md.no_detector')} "
                f"| {weight if weight is not None else '-'} |"
            )
        lines += [
            "",
            f"{t(lang, 'md.score_version')} `{quality['score_version']}`",
            "",
            f"{t(lang, 'md.formula')} {quality['calculation_notes']}",
        ]
    else:
        lines.append(f"_{t(lang, 'md.not_scored')}_")
    lines += ["", f"## {t(lang, 'section.issue_breakdown')}", ""]
    if report["issues"]:
        lines.append(t(lang, "md.severity_priority_issue"))
        lines.append("|---|---|---|---|---|")
        for issue in report["issues"]:
            lines.append(
                f"| {issue['severity']} | {issue['priority_score']:.1f} "
                f"| {_escape_cell(mask_text_pii(issue['title']))} | {', '.join(issue['columns'])} "
                f"| {', '.join(issue['detector_ids'])} |"
            )
    else:
        lines.append(f"_{t(lang, 'md.no_issues')}_")
    findings = critical_findings(report)
    if findings:
        lines += ["", f"## {t(lang, 'section.critical_findings')}", ""]
        for issue in findings:
            lines.append(
                f"- **[{issue['severity']}]** {mask_text_pii(issue['title'])} "
                f"({t(lang, 'md.priority')}={issue['priority_score']:.1f}, "
                f"{t(lang, 'md.affected')}={issue['affected_count']} rows, "
                f"{t(lang, 'md.ratio')}={issue['affected_ratio']:.4f})"
            )
    lines += ["", f"## {t(lang, 'md.reproducibility')}", ""]
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
