"""HTML 报告渲染（26.1：自包含单文件，内嵌 CSS/JS，可离线打开）。

MVP 节（26 章清单可填充部分）：Executive Summary / Dataset Overview /
Quality Score（含 27.3 维度条形分解与「哪些 Issue 扣分」悬停）/ Issue Breakdown /
Critical Findings / Column Profiles / Methodology / Reproducibility。
Drift / Suggested Rules / Repair History 归 V1。
Step 49（V2-B，ADR-049）：Issue Breakdown 升级为交互表格（severity/维度筛选、
列排序、详情折叠、分页，原生 JS 零依赖内联）；可选 Quality Trends 迷你 SVG
（消费 trends.py 序列化数据）；server_base_url 非空时 issue 行联动修复工作台。
"""

from __future__ import annotations

from html import escape
from typing import Any

from datasentry_core.reporting import HTML_SECTIONS, Report, critical_findings, mask_text_pii
from datasentry_core.reporting.interactive import render_interactive_issue_table, render_trend_svg

_CSS = """
:root { color-scheme: light; }
body { font-family: -apple-system, "Segoe UI", Roboto, sans-serif; margin: 2rem auto;
       max-width: 960px; color: #1f2328; line-height: 1.5; }
h1 { border-bottom: 2px solid #0969da; padding-bottom: .3rem; }
h2 { margin-top: 2rem; border-bottom: 1px solid #d0d7de; padding-bottom: .2rem; }
table { border-collapse: collapse; width: 100%; margin: .5rem 0; }
th, td { border: 1px solid #d0d7de; padding: .35rem .6rem; text-align: left; font-size: .9rem; }
th { background: #f6f8fa; }
.badge-critical { color: #cf222e; font-weight: 600; }
.badge-high { color: #bc4c00; font-weight: 600; }
.badge-medium { color: #9a6700; font-weight: 600; }
.badge-low, .badge-info { color: #57606a; }
.score-bar { display: flex; width: 100%; height: 1.4rem; border-radius: .4rem;
             overflow: hidden; border: 1px solid #d0d7de; }
.score-bar section { color: #fff; font-size: .7rem; text-align: center; }
footer { margin-top: 3rem; font-size: .8rem; color: #57606a; border-top: 1px solid #d0d7de; }
.notes { font-size: .85rem; color: #57606a; background: #f6f8fa; padding: .6rem;
         border-radius: .4rem; }
.issue-controls { display: flex; gap: .5rem; align-items: center; flex-wrap: wrap;
                  margin: .6rem 0; }
.issue-controls select, .issue-controls input[type=search] { padding: .25rem .45rem;
                  font-size: .85rem; }
.issue-row td { cursor: pointer; }
.issue-detail td { background: #f6f8fa; font-size: .85rem; }
.issue-detail.collapsed { display: none; }
th[data-key] { cursor: pointer; user-select: none; white-space: nowrap; }
th[data-key]::after { content: " \\21C5"; font-size: .7rem; color: #8c959f; }
th[data-key].sorted-asc::after { content: " \\2191"; }
th[data-key].sorted-desc::after { content: " \\2193"; }
.trend-block { margin: 1rem 0; }
.trend-svg { display: block; background: #fff; border: 1px solid #d0d7de;
             border-radius: .4rem; padding: .3rem; }
.issue-pager { display: flex; gap: .6rem; align-items: center; margin: .6rem 0; }
.issue-pager button { background: #f6f8fa; color: #0969da; border: 1px solid #d0d7de;
                      border-radius: .3rem; padding: .2rem .7rem; cursor: pointer; }
.workbench-link { margin-left: .6rem; font-size: .8rem; }
"""

_BAR_COLORS = [
    "#0969da",
    "#1a7f37",
    "#bf8700",
    "#8250df",
    "#cf222e",
    "#57606a",
]


def render_html(
    report: Report,
    *,
    trends: list[dict[str, Any]] | None = None,
    page_size: int = 25,
    server_base_url: str | None = None,
) -> str:
    """26 章报告 → 自包含 HTML（内嵌 CSS/JS，无外部资源）。

    trends：trends.py `DatasetTrend.to_report_dict()` 列表 → Quality Trends 迷你 SVG；
    server_base_url：非空时 issue 行附带修复工作台链接（server 模式联动 REST API）。
    """
    parts = [
        "<!DOCTYPE html>",
        '<html lang="en"><head><meta charset="utf-8">',
        "<title>DataSentry Data Quality Report</title>",
        f"<style>{_CSS}</style></head><body>",
        "<h1>DataSentry Data Quality Report</h1>",
        _meta(report),
        _executive_summary(report),
        _quality_score(report),
    ]
    if trends:
        parts.append(_trends_section(trends))
    parts.extend(
        [
            _issue_breakdown(report, page_size=page_size, server_base_url=server_base_url),
            _critical_findings(report),
            _dataset_overview(report),
            _methodology(report),
            _reproducibility(report),
            _footer(report),
            "</body></html>",
        ]
    )
    return "\n".join(parts)


def _meta(report: Report) -> str:
    return (
        "<p>"
        f"report_schema_version: <code>{escape(report['report_schema_version'])}</code> &middot; "
        f"datasentry_version: <code>{escape(report['datasentry_version'])}</code> &middot; "
        f"scan_run_id: <code>{escape(report['scan_run_id'])}</code> &middot; "
        f"generated_at: <code>{escape(report['generated_at'])}</code> &middot; "
        f"reproducible: <code>{report['reproducible']}</code> &middot; "
        f"llm_used: <code>{report['llm_used']}</code>"
        "</p>"
    )


def _executive_summary(report: Report) -> str:
    scan = report["scan"]
    quality = report["quality"]
    overall = f"{quality['overall']:.1f}" if quality else "not scored"
    return (
        '<h2 id="executive_summary">Executive Summary</h2>'
        "<table>"
        f"<tr><th>Overall</th><td>{overall}</td></tr>"
        f"<tr><th>Issues</th><td>{len(report['issues'])}</td></tr>"
        f"<tr><th>Detector runs</th><td>{len(report['detector_runs'])}</td></tr>"
        f"<tr><th>Rows / Columns</th><td>{scan['fingerprint']['row_count']} / "
        f"{scan['fingerprint']['column_count']}</td></tr>"
        "</table>"
    )


def _quality_score(report: Report) -> str:
    quality = report["quality"]
    if quality is None:
        return '<h2 id="quality_score">Quality Score</h2><p>not scored</p>'
    sections = []
    for index, (dim, value) in enumerate(quality["dimensions"].items()):
        if value is None:
            sections.append(
                f'<section title="{escape(dim)}: no detector ran">{escape(dim)}: n/a</section>'
            )
            continue
        weight = quality["weights"].get(dim, 0.0)
        width = f"{weight * 100:.1f}%"
        color = _BAR_COLORS[index % len(_BAR_COLORS)]
        title = escape(_contributions_tooltip(quality, dim))
        sections.append(
            f'<section style="width:{width};background:{color}" title="{title}">'
            f"{escape(dim)} {value:.1f}</section>"
        )
    contributions = quality.get("dimension_contributions") or {}
    notes_rows = []
    if contributions:
        notes_rows.append("<p><strong>Per-issue deductions (hover the score bar):</strong></p><ul>")
        for dim, items in sorted(contributions.items()):
            for issue_id, impact in sorted(items.items()):
                notes_rows.append(
                    f"<li>{escape(dim)} / <code>{escape(issue_id)}</code>: {impact:.4f}</li>"
                )
        notes_rows.append("</ul>")
    return (
        f'<h2 id="quality_score">Quality Score</h2>'
        f"<p>Overall: <strong>{quality['overall']:.1f}</strong> "
        f"(score_version <code>{escape(quality['score_version'])}</code>)</p>"
        f'<div class="score-bar">{"".join(sections)}</div>'
        f'<p class="notes">{escape(quality["calculation_notes"])}</p>' + "".join(notes_rows)
    )


def _contributions_tooltip(quality: dict[str, Any], dim: str) -> str:
    contributions = quality.get("dimension_contributions") or {}
    items = contributions.get(dim, {})
    if not items:
        return f"{dim}: no deductions"
    return f"{dim}: " + "; ".join(f"{i}: {v:.4f}" for i, v in items.items())


def _trends_section(trends: list[dict[str, Any]]) -> str:
    svgs = [svg for svg in (render_trend_svg(t) for t in trends) if svg]
    if not svgs:
        return ""
    return '<h2 id="quality_trends">Quality Trends</h2>' + "".join(svgs)


def _issue_breakdown(
    report: Report,
    *,
    page_size: int = 25,
    server_base_url: str | None = None,
) -> str:
    return render_interactive_issue_table(
        report, page_size=page_size, server_base_url=server_base_url
    )


def _critical_findings(report: Report) -> str:
    findings = critical_findings(report)
    if not findings:
        return ""
    items = []
    for issue in findings:
        items.append(
            "<li>"
            f'<span class="badge-{escape(issue["severity"])}">[{escape(issue["severity"])}]</span> '
            f"{escape(mask_text_pii(issue['title']))} &mdash; "
            f"priority {issue['priority_score']:.1f}, "
            f"{issue['affected_count']} rows affected"
            "</li>"
        )
    return f'<h2 id="critical_findings">Critical Findings</h2><ul>{"".join(items)}</ul>'


def _dataset_overview(report: Report) -> str:
    scan = report["scan"]
    fingerprint = scan["fingerprint"]
    rows = [
        f"<tr><th>dataset_id</th><td>{escape(scan['dataset_id'])}</td></tr>",
        f"<tr><th>status</th><td>{escape(scan['status'])}</td></tr>",
        f"<tr><th>rows</th><td>{fingerprint['row_count']}</td></tr>",
        f"<tr><th>schema_hash</th><td><code>{escape(fingerprint['schema_hash'])}</code></td></tr>",
    ]
    columns = [
        f"<li><code>{escape(name)}</code> ({escape(ptype)})</li>"
        for name, ptype in fingerprint["column_signature"]
    ]
    return (
        '<h2 id="dataset_overview">Dataset Overview</h2>'
        f"<table>{''.join(rows)}</table>"
        f"<p><strong>Columns:</strong></p><ul>{''.join(columns)}</ul>"
    )


def _methodology(report: Report) -> str:
    return (
        '<h2 id="methodology">Methodology</h2>'
        "<p>Deterministic SQL pushdown detectors (15 in MVP) run per column; "
        "candidates are fused per issue cluster (confidence = 1 - prod(1 - c_i)); "
        "each issue receives a priority score (0-100, 12.8 formula); "
        "the quality score aggregates per dimension (27.1 formula, ADR-013). "
        "No LLM calls in MVP: scores are fully reproducible.</p>"
    )


def _reproducibility(report: Report) -> str:
    scan = report["scan"]
    repro = scan["reproducibility"]
    return (
        '<h2 id="reproducibility">Reproducibility Metadata</h2><ul>'
        f"<li>datasentry_version: <code>{escape(repro['datasentry_version'])}</code></li>"
        f"<li>detector_versions: <code>{escape(repr(repro['detector_versions']))}</code></li>"
        f"<li>seed: <code>{repro['seed']}</code></li>"
        f"<li>scanned_at: <code>{escape(repro['scanned_at'])}</code></li>"
        "</ul>"
    )


def _footer(report: Report) -> str:
    return (
        "<footer>"
        f"Generated by DataSentry {escape(report['datasentry_version'])} &middot; "
        f"report_schema_version {escape(report['report_schema_version'])} &middot; "
        "sections: " + ", ".join(f'<a href="#{s}">{s}</a>' for s in HTML_SECTIONS) + "</footer>"
    )
