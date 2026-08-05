"""DataSentry Web UI（Step 24，MVP 五个核心页的服务端渲染最小集）。

技术路线：FastAPI 服务端渲染 + 内嵌 CSS（与 reporting/html.py 同一
零依赖风格，无前端构建链、无 JS 框架）。页面直接消费 `DataSentry`
闭合并渲染 HTML 字符串，全部输出经 `escape()` 转义（XSS 安全）。

核心页（docs/03 1.2）：
    /ui/                    首页：工作区概览 + ScanRun 列表
    /ui/scans/{run_id}      Dataset Overview + Issue Center（筛选）
    /ui/scans/{run_id}/issues/{issue_id}   修复工作台（15 章 propose→preview→apply）
Column Explorer / 跨扫描趋势归 V1（MVP 只做问题定位闭环）。
"""

from __future__ import annotations

from html import escape

from datasentry_core.models.issue import Issue
from datasentry_core.models.repair import RepairPreview, RepairProposal, RepairRun
from datasentry_core.models.scan import ScanRun

_CSS = """
:root { color-scheme: light; }
body { font-family: -apple-system, "Segoe UI", Roboto, sans-serif; margin: 2rem auto;
       max-width: 960px; color: #1f2328; line-height: 1.5; }
h1 { border-bottom: 2px solid #0969da; padding-bottom: .3rem; }
h2 { margin-top: 2rem; border-bottom: 1px solid #d0d7de; padding-bottom: .2rem; }
table { border-collapse: collapse; width: 100%; margin: .5rem 0; }
th, td { border: 1px solid #d0d7de; padding: .35rem .6rem; text-align: left; font-size: .9rem; }
th { background: #f6f8fa; }
nav { margin-bottom: 1.5rem; }
nav a { margin-right: 1rem; }
a { color: #0969da; }
.badge { display: inline-block; padding: .1rem .45rem; border-radius: .6rem;
         font-size: .75rem; font-weight: 600; color: #fff; }
.badge-critical { background: #cf222e; }
.badge-high { background: #bc4c00; }
.badge-medium { background: #9a6700; }
.badge-low { background: #57606a; }
.badge-info { background: #8c959f; }
.priority { font-weight: 600; }
.score-ring { font-size: 2.5rem; font-weight: 700; color: #0969da; }
.severity-filters { margin: 1rem 0; }
.severity-filters a { margin-right: .8rem; text-decoration: none; padding: .2rem .6rem;
                      border: 1px solid #d0d7de; border-radius: .4rem; }
.severity-filters a.active { background: #0969da; color: #fff; border-color: #0969da; }
form { background: #f6f8fa; border: 1px solid #d0d7de; border-radius: .5rem;
       padding: 1rem; margin: 1rem 0; }
form label { display: block; margin: .6rem 0 .2rem; font-size: .85rem; font-weight: 600; }
form input[type=text] { width: 100%; box-sizing: border-box; padding: .35rem; }
button { background: #0969da; color: #fff; border: none; border-radius: .4rem;
         padding: .45rem 1rem; cursor: pointer; margin-top: .8rem; }
button.secondary { background: #57606a; }
.issue-card { border: 1px solid #d0d7de; border-radius: .5rem; padding: .8rem 1rem;
              margin: .6rem 0; }
.issue-card h3 { margin: 0 0 .3rem; font-size: 1rem; }
.meta { font-size: .8rem; color: #57606a; }
pre { background: #f6f8fa; padding: .6rem; border-radius: .4rem; overflow-x: auto;
      font-size: .8rem; }
footer { margin-top: 3rem; font-size: .8rem; color: #57606a; border-top: 1px solid #d0d7de; }
.alert { border-radius: .4rem; padding: .6rem 1rem; margin: .8rem 0; font-size: .9rem; }
.alert-ok { background: #dafbe1; color: #1a7f37; }
.alert-err { background: #ffebe9; color: #cf222e; }
"""


def _severity_badge(severity: str) -> str:
    return f'<span class="badge badge-{escape(severity.lower())}">{escape(severity)}</span>'


def _page(title: str, body: str, *, active: str = "") -> str:
    return "\n".join(
        [
            "<!DOCTYPE html>",
            '<html lang="en"><head><meta charset="utf-8">',
            f"<title>{escape(title)} · DataSentry</title>",
            f"<style>{_CSS}</style></head><body>",
            "<nav>"
            '<a href="/ui/">Home</a>'
            '<a href="/ui/scans">Scans</a>'
            '<a href="/api/docs">API docs</a>'
            "</nav>",
            f"<h1>{escape(title)}</h1>",
            body,
            "<footer>DataSentry AI · local-first data quality copilot</footer>",
            "</body></html>",
        ]
    )


def _scan_table(scans: list[ScanRun]) -> str:
    if not scans:
        return '<p class="meta">No scans yet — <a href="/ui/scans">create one</a>.</p>'
    rows = []
    for scan in scans:
        overall = f"{scan.quality_score.overall:.1f}" if scan.quality_score else "—"
        rows.append(
            "<tr>"
            f'<td><a href="/ui/scans/{escape(scan.id)}">{escape(scan.id)}</a></td>'
            f"<td>{escape(scan.dataset_id)}</td>"
            f"<td>{scan.fingerprint.row_count} × {scan.fingerprint.column_count}</td>"
            f'<td class="priority">{overall}</td>'
            f"<td>{escape(scan.status)}</td>"
            f"<td>{scan.started_at:%Y-%m-%d %H:%M}</td>"
            "</tr>"
        )
    return (
        "<table><tr><th>Run ID</th><th>Dataset</th><th>Rows × Cols</th>"
        "<th>Score</th><th>Status</th><th>Started</th></tr>" + "".join(rows) + "</table>"
    )


def render_home(scans: list[ScanRun]) -> str:
    body = [
        "<h2>Workspace overview</h2>",
        _scan_table(scans),
        "<h2>New scan</h2>",
        '<form method="post" action="/ui/scans">'
        '<label for="path">Data file path (relative to workspace)</label>'
        '<input type="text" id="path" name="path" required placeholder="data/customers.csv">'
        '<button type="submit">Scan</button>',
        "</form>",
    ]
    return _page("Home", "\n".join(body))


def _issue_rows(issues: list[Issue], run_id: str) -> str:
    if not issues:
        return '<p class="meta">No issues.</p>'
    rows = []
    for issue in issues:
        cols = ", ".join(escape(c) for c in issue.columns) or "—"
        rows.append(
            '<div class="issue-card">'
            f"<h3>{_severity_badge(issue.severity.value)} "
            f"{escape(issue.title)}</h3>"
            f'<p class="meta">priority {issue.priority_score:.1f} · confidence '
            f"{issue.confidence:.2f} · affected {issue.affected_count} rows · "
            f"columns: {cols} · detectors: "
            f"{', '.join(escape(d) for d in issue.detector_ids)}</p>"
            f'<a href="/ui/scans/{escape(run_id)}/issues/{escape(issue.id)}">'
            "Repair workbench →</a>"
            "</div>"
        )
    return "\n".join(rows)


def render_scan_detail(
    scan: ScanRun,
    issues: list[Issue],
    *,
    severity_filter: str | None = None,
) -> str:
    overall = f"{scan.quality_score.overall:.1f}" if scan.quality_score else "not scored"
    dims = []
    if scan.quality_score:
        for name, value in scan.quality_score.dimensions.items():
            if value is not None:
                dims.append(f"{name}={value:.1f}")
    body = [
        '<p class="meta">'
        f"{escape(scan.id)} · {escape(scan.dataset_id)} · "
        f"{scan.fingerprint.row_count} rows × {scan.fingerprint.column_count} cols · "
        f"{escape(scan.status)}</p>",
        '<div class="score-ring">' + overall + "</div>",
        '<p class="meta">' + " &middot; ".join(dims) + "</p>",
        "<h2>Issues</h2>",
        '<div class="severity-filters">'
        '<a href="." class="'
        + ("active" if severity_filter is None else "")
        + '">all</a>'
        + "".join(
            f'<a href="?severity={level}" class="'
            + ("active" if severity_filter == level else "")
            + f'">{level}</a>'
            for level in ("critical", "high", "medium", "low", "info")
        )
        + "</div>",
        _issue_rows(issues, scan.id),
        '<p class="meta"><a href="/api/reports/">JSON report</a></p>',
    ]
    return _page(f"Scan {scan.id}", "\n".join(body))


def render_workbench(
    issue: Issue,
    *,
    run_id: str = "",
    source_path: str | None = None,
    proposal: RepairProposal | None = None,
    preview: RepairPreview | None = None,
    run: RepairRun | None = None,
    error: str | None = None,
) -> str:
    cols = ", ".join(escape(c) for c in issue.columns) or "—"
    body = [
        f"<p>{_severity_badge(issue.severity.value)} {escape(issue.title)}</p>",
        f'<p class="meta">issue {escape(issue.id)} · priority {issue.priority_score:.1f} · '
        f"confidence {issue.confidence:.2f} · affected {issue.affected_count} rows · "
        f"columns: {cols}</p>",
    ]
    if error:
        body.append(f'<div class="alert alert-err">{escape(error)}</div>')
    if proposal:
        body.append(
            "<h2>Proposal</h2>"
            "<table>"
            f"<tr><th>Operation</th><td>{escape(proposal.operation)}</td></tr>"
            f"<tr><th>Target columns</th><td>{escape(', '.join(proposal.target_columns))}</td></tr>"
            f"<tr><th>Parameters</th><td><pre>{escape(repr(proposal.parameters))}</pre></td></tr>"
            f"<tr><th>Rows affected</th><td>{proposal.estimated_rows_changed}</td></tr>"
            f"<tr><th>Risk</th><td>{escape(proposal.risk_level.value)}</td></tr>"
            "</table>"
        )
        if preview:
            body.append(
                "<h2>Preview</h2>"
                "<table>"
                f"<tr><th>Rule failures before</th><td>{preview.rule_failures_before}</td></tr>"
                f"<tr><th>Rule failures after</th><td>{preview.rule_failures_after}</td></tr>"
                f"<tr><th>Rows changed ratio</th><td>{preview.rows_changed_ratio:.3f}</td></tr>"
                "</table>"
            )
    if run:
        body.append(
            '<div class="alert alert-ok">'
            f"Repair applied: <code>{escape(run.id)}</code> · status {escape(run.status)} · "
            f'<a href="/ui/scans/{escape(run_id)}/repairs/{escape(run.id)}/rollback">'
            "Rollback</a>"
            "</div>"
        )
    body.append(
        "<h2>Repair workbench</h2>"
        '<form method="post">'
        '<label for="source_path">Source file path</label>'
        f'<input type="text" id="source_path" name="source_path" required '
        f'value="{escape(source_path or "")}">'
        '<button type="submit" name="action" value="propose">Propose repair</button>'
        '<button class="secondary" type="submit" name="action" value="apply">'
        "Apply repair</button>"
        "</form>"
    )
    return _page("Repair workbench", "\n".join(body))


def render_error(title: str, message: str) -> str:
    return _page(
        title,
        f'<div class="alert alert-err">{escape(message)}</div><p><a href="/ui/">← Home</a></p>',
    )
