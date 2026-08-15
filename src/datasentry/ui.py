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
from typing import Any

from datasentry.trends import DatasetTrend, ScanPoint
from datasentry_core.models.issue import Issue
from datasentry_core.models.repair import RepairPreview, RepairProposal, RepairRun
from datasentry_core.models.scan import ScanConfig, ScanRun
from datasentry_core.reporting import mask_text_pii
from datasentry_core.reporting.i18n import t
from datasentry_core.reporting.translate import translate_title

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
.badge-ok { background: #1a7f37; }
.badge-err { background: #cf222e; }
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
.trend-bars { margin: .6rem 0 1rem; }
.trend-bar { background: #d0d7de; border-radius: .3rem; margin: .15rem 0; height: 1.1rem;
             overflow: hidden; }
.trend-bar span { display: block; background: #0969da; color: #fff; font-size: .7rem;
                  line-height: 1.1rem; padding-left: .3rem; white-space: nowrap; }
.trend-spark { display: block; margin: .4rem 0; }
.trend-spark polyline { fill: none; stroke: #0969da; stroke-width: 1.5; }
.trend-spark circle { fill: #0969da; }
.delta-up { color: #1a7f37; font-weight: 600; }
.delta-down { color: #cf222e; font-weight: 600; }
"""


def _severity_badge(severity: str) -> str:
    return f'<span class="badge badge-{escape(severity.lower())}">{escape(severity)}</span>'


def _page(title: str, body: str, *, active: str = "", lang: str = "en") -> str:
    return "\n".join(
        [
            "<!DOCTYPE html>",
            f'<html lang="{t(lang, "html.lang")}"><head><meta charset="utf-8">',
            f"<title>{escape(title)} · DataSentry</title>",
            f"<style>{_CSS}</style></head><body>",
            "<nav>"
            f'<a href="/ui/">{escape(t(lang, "ui.nav_home"))}</a>'
            f'<a href="/ui/scans">{escape(t(lang, "ui.nav_scans"))}</a>'
            f'<a href="/ui/trends">{escape(t(lang, "ui.nav_trends"))}</a>'
            f'<a href="/ui/pii">{escape(t(lang, "ui.nav_pii"))}</a>'
            f'<a href="/api/docs">{escape(t(lang, "ui.nav_api_docs"))}</a>'
            "</nav>",
            f"<h1>{escape(title)}</h1>",
            body,
            f"<footer>{escape(t(lang, 'ui.footer'))}</footer>",
            "</body></html>",
        ]
    )


def _scan_table(scans: list[ScanRun], *, lang: str = "en") -> str:
    if not scans:
        return f'<p class="meta">{escape(t(lang, "ui.no_scans"))}</p>'
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
        "<table><tr>"
        f"<th>{escape(t(lang, 'ui.run_id'))}</th><th>{escape(t(lang, 'ui.dataset'))}</th>"
        f"<th>{escape(t(lang, 'ui.rows_cols'))}</th><th>{escape(t(lang, 'ui.score'))}</th>"
        f"<th>{escape(t(lang, 'ui.status'))}</th><th>{escape(t(lang, 'ui.started'))}</th>"
        f"</tr>{''.join(rows)}</table>"
    )


def render_home(scans: list[ScanRun], *, lang: str = "en") -> str:
    body = [
        f"<h2>{escape(t(lang, 'ui.workspace_overview'))}</h2>",
        _scan_table(scans, lang=lang),
        f"<h2>{escape(t(lang, 'ui.new_scan'))}</h2>",
        '<form method="post" action="/ui/scans">'
        f'<label for="path">{escape(t(lang, "ui.data_file_path"))}</label>'
        '<input type="text" id="path" name="path" required placeholder="data/customers.csv">'
        f'<button type="submit">{escape(t(lang, "ui.scan_button"))}</button>',
        "</form>",
    ]
    return _page(t(lang, "ui.home_title"), "\n".join(body), lang=lang)


def _direction_badge(direction: str, *, lang: str = "en") -> str:
    if direction == "up":
        return f'<span class="badge badge-ok">{escape(t(lang, "ui.direction_up"))}</span>'
    if direction == "down":
        return f'<span class="badge badge-err">{escape(t(lang, "ui.direction_down"))}</span>'
    return f'<span class="badge">{escape(t(lang, "ui.direction_flat"))}</span>'


def _sparkline(scores: list[float]) -> str:
    """内联 SVG 折线（零依赖，Step 67，ADR-067）：分数序列 → 迷你趋势线。"""
    if len(scores) < 2:
        return ""
    width, height = 220, 40
    low, high = min(scores), max(scores)
    span = max(high - low, 1.0)
    pad = 4
    points = []
    step = (width - 2 * pad) / max(len(scores) - 1, 1)
    for i, score in enumerate(scores):
        x = pad + i * step
        y = height - pad - ((score - low) / span) * (height - 2 * pad)
        points.append(f"{x:.1f},{y:.1f}")
    polyline = f'<polyline points="{" ".join(points)}"/>'
    circles = "".join(
        f'<circle cx="{points[i].split(",")[0]}" cy="{points[i].split(",")[1]}" r="2"/>'
        for i in (0, len(points) - 1)
    )
    return (
        f'<svg class="trend-spark" width="{width}" height="{height}" '
        f'aria-label="score trend: {" → ".join(f"{s:.1f}" for s in scores)}">'
        f"{polyline}{circles}</svg>"
    )


def _delta_cell(point: ScanPoint, previous: ScanPoint | None) -> str:
    """run 行 Δ badge（对前一 run，首行 —）：Step 67，ADR-067。"""
    if previous is None:
        return '<td class="meta">—</td>'
    delta = point.score - previous.score
    if delta > 0:
        return f'<td class="delta-up">+{delta:.1f}</td>'
    if delta < 0:
        return f'<td class="delta-down">{delta:.1f}</td>'
    return '<td class="meta">0.0</td>'


def render_trends(trends: list[DatasetTrend], *, lang: str = "en") -> str:
    """跨扫描趋势页（Step 45，18.2 V1）。trends 来自 trends.build_trends。"""
    if not trends:
        body = [f'<p class="meta">{escape(t(lang, "ui.no_trend_data"))}</p>']
        return _page(t(lang, "ui.trends_title"), "\n".join(body), lang=lang)
    sections: list[str] = []
    for trend in trends:
        points = trend.points
        rows = []
        bars = []
        for index, point in enumerate(points):
            width = max(0.0, min(100.0, point.score))
            prior = points[index - 1] if index else None
            rows.append(
                "<tr>"
                f'<td><a href="/ui/scans/{escape(point.run_id)}">{escape(point.run_id)}</a></td>'
                f"<td>{point.finished_at:%Y-%m-%d %H:%M}</td>"
                f'<td class="priority">{point.score:.1f}</td>'
                + _delta_cell(point, prior)
                + f"<td>{point.issues_total}</td>"
                "</tr>"
            )
            bars.append(
                f'<div class="trend-bar"><span style="width:{width:.1f}%">'
                f"{point.score:.1f}</span></div>"
            )
        latest = trend.latest_score
        sections.append(
            "<section>"
            f"<h2>{escape(trend.dataset_id)} "
            f"{_direction_badge(trend.direction, lang=lang)} "
            f'<span class="meta">{escape(t(lang, "ui.delta"))} {trend.delta:+.1f}</span></h2>'
            f'<p class="meta">{len(points)} {escape(t(lang, "ui.completed_scans"))} · '
            f"{escape(t(lang, 'ui.latest_score'))} "
            f"{latest:.1f} · {escape(t(lang, 'ui.latest_issues'))} {trend.latest_issues}</p>"
            + _sparkline([p.score for p in points])
            + '<div class="trend-bars">'
            + "".join(bars)
            + "</div>"
            "<table><tr>"
            f"<th>{escape(t(lang, 'ui.run_id'))}</th><th>{escape(t(lang, 'ui.finished'))}</th>"
            f"<th>{escape(t(lang, 'ui.score'))}</th>"
            f"<th>{escape(t(lang, 'ui.delta'))}</th><th>{escape(t(lang, 'ui.issues_title'))}</th>"
            f"</tr>{''.join(rows)}</table>"
            "</section>"
        )
    return _page(t(lang, "ui.trends_title"), "\n".join(sections), lang=lang)


def _sampling_enabled(config: ScanConfig) -> bool:
    """实际抽样判定（与 runner._resolve_sample_size 一致，Step 71/ADR-071）。"""
    sampling = config.sampling
    return sampling.method != "none" and (
        sampling.sample_size is not None or sampling.ratio is not None
    )


def _issue_rows(issues: list[Issue], run_id: str, *, lang: str = "en") -> str:
    if not issues:
        return f'<p class="meta">{escape(t(lang, "ui.no_issues"))}</p>'
    rows = []
    for issue in issues:
        cols = ", ".join(escape(c) for c in issue.columns) or "—"
        rows.append(
            '<div class="issue-card">'
            f"<h3>{_severity_badge(issue.severity.value)} "
            f"{escape(mask_text_pii(translate_title(lang, issue.title, issue.issue_type)))}</h3>"
            f'<p class="meta">{escape(t(lang, "ui.priority"))} {issue.priority_score:.1f} · '
            f"{escape(t(lang, 'ui.confidence'))} "
            f"{issue.confidence:.2f} · {escape(t(lang, 'ui.affected'))} {issue.affected_count} "
            f"rows · {escape(t(lang, 'ui.columns'))}: {cols} · "
            f"{escape(t(lang, 'ui.detectors'))}: "
            f"{', '.join(escape(d) for d in issue.detector_ids)}</p>"
            f'<a href="/ui/scans/{escape(run_id)}/issues/{escape(issue.id)}">'
            f"{escape(t(lang, 'ui.repair_workbench_link'))}</a>"
            "</div>"
        )
    return "\n".join(rows)


def render_scan_detail(
    scan: ScanRun,
    issues: list[Issue],
    *,
    severity_filter: str | None = None,
    lang: str = "en",
) -> str:
    overall = (
        f"{scan.quality_score.overall:.1f}" if scan.quality_score else t(lang, "meta.not_scored")
    )
    dims = []
    if scan.quality_score:
        for name, value in scan.quality_score.dimensions.items():
            if value is not None:
                dims.append(f"{name}={value:.1f}")
    body = [
        '<p class="meta">'
        f"{escape(scan.id)} · {escape(scan.dataset_id)} · "
        f"{scan.fingerprint.row_count} rows × {scan.fingerprint.column_count} cols · "
        f"{escape(scan.status)}"
        + (
            ' · <span class="badge">sampled '
            f"{escape(str(scan.config.sampling.model_dump(mode='json')))}</span>"
            if _sampling_enabled(scan.config)
            else ""
        )
        + "</p>",
        '<div class="score-ring">' + overall + "</div>",
        '<p class="meta">' + " &middot; ".join(dims) + "</p>",
        f"<h2>{escape(t(lang, 'ui.issues_title'))}</h2>",
        '<div class="severity-filters">'
        '<a href="." class="'
        + ("active" if severity_filter is None else "")
        + f'">{escape(t(lang, "ui.filter_all"))}</a>'
        + "".join(
            f'<a href="?severity={level}" class="'
            + ("active" if severity_filter == level else "")
            + f'">{level}</a>'
            for level in ("critical", "high", "medium", "low", "info")
        )
        + "</div>",
        _issue_rows(issues, scan.id, lang=lang),
        f'<p class="meta"><a href="/api/reports/">{escape(t(lang, "ui.json_report"))}</a> &middot; '
        f'<a href="/scans/{escape(scan.id)}/report.html">'
        f"{escape(t(lang, 'ui.interactive_report'))}</a></p>",
    ]
    return _page(f"Scan {scan.id}", "\n".join(body), lang=lang)


def render_workbench(
    issue: Issue,
    *,
    run_id: str = "",
    source_path: str | None = None,
    proposal: RepairProposal | None = None,
    preview: RepairPreview | None = None,
    run: RepairRun | None = None,
    error: str | None = None,
    lang: str = "en",
) -> str:
    cols = ", ".join(escape(c) for c in issue.columns) or "—"
    body = [
        f"<p>{_severity_badge(issue.severity.value)} {escape(mask_text_pii(issue.title))}</p>",
        f'<p class="meta">issue {escape(issue.id)} · {escape(t(lang, "ui.priority"))} '
        f"{issue.priority_score:.1f} · {escape(t(lang, 'ui.confidence'))} "
        f"{issue.confidence:.2f} · {escape(t(lang, 'ui.affected'))} {issue.affected_count} "
        f"rows · {escape(t(lang, 'ui.columns'))}: {cols}</p>",
    ]
    if error:
        body.append(f'<div class="alert alert-err">{escape(error)}</div>')
    if proposal:
        body.append(
            f"<h2>{escape(t(lang, 'ui.proposal_title'))}</h2>"
            "<table>"
            f"<tr><th>{escape(t(lang, 'ui.operation'))}</th>"
            f"<td>{escape(proposal.operation)}</td></tr>"
            f"<tr><th>{escape(t(lang, 'ui.target_columns'))}</th>"
            f"<td>{escape(', '.join(proposal.target_columns))}</td></tr>"
            f"<tr><th>{escape(t(lang, 'ui.parameters'))}</th>"
            f"<td><pre>{escape(repr(proposal.parameters))}</pre></td></tr>"
            f"<tr><th>{escape(t(lang, 'ui.rows_affected'))}</th>"
            f"<td>{proposal.estimated_rows_changed}</td></tr>"
            f"<tr><th>{escape(t(lang, 'ui.risk'))}</th>"
            f"<td>{escape(proposal.risk_level.value)}</td></tr>"
            "</table>"
        )
        if preview:
            body.append(
                f"<h2>{escape(t(lang, 'ui.preview_title'))}</h2>"
                "<table>"
                f"<tr><th>{escape(t(lang, 'ui.rule_failures_before'))}</th>"
                f"<td>{preview.rule_failures_before}</td></tr>"
                f"<tr><th>{escape(t(lang, 'ui.rule_failures_after'))}</th>"
                f"<td>{preview.rule_failures_after}</td></tr>"
                f"<tr><th>{escape(t(lang, 'ui.rows_changed_ratio'))}</th>"
                f"<td>{preview.rows_changed_ratio:.3f}</td></tr>"
                "</table>"
            )
    if run:
        body.append(
            '<div class="alert alert-ok">'
            f"{escape(t(lang, 'ui.repair_applied'))} <code>{escape(run.id)}</code> · "
            f"status {escape(run.status)} · "
            f'<a href="/ui/scans/{escape(run_id)}/repairs/{escape(run.id)}/rollback">'
            f"{escape(t(lang, 'ui.rollback'))}</a>"
            "</div>"
        )
    body.append(
        f"<h2>{escape(t(lang, 'ui.workbench_title'))}</h2>"
        '<form method="post">'
        f'<label for="source_path">{escape(t(lang, "ui.source_file_path"))}</label>'
        f'<input type="text" id="source_path" name="source_path" required '
        f'value="{escape(source_path or "")}">'
        f'<button type="submit" name="action" value="propose">'
        f"{escape(t(lang, 'ui.propose_repair'))}</button>"
        f'<button class="secondary" type="submit" name="action" value="apply">'
        f"{escape(t(lang, 'ui.apply_repair'))}</button>"
        "</form>"
    )
    return _page(t(lang, "ui.workbench_title"), "\n".join(body), lang=lang)


def render_error(title: str, message: str, *, lang: str = "en") -> str:
    return _page(
        title,
        f'<div class="alert alert-err">{escape(message)}</div>'
        f'<p><a href="/ui/">{escape(t(lang, "ui.back_home"))}</a></p>',
        lang=lang,
    )


def render_pii(
    sessions: list[dict[str, Any]],
    *,
    key_source: str = "dev",
    key_configured: bool = True,
    restored: str | None = None,
    error: str | None = None,
    key_ok: str | None = None,
    key_result: dict[str, Any] | None = None,
    purge_ok: str | None = None,
    purged: int | None = None,
    lang: str = "en",
) -> str:
    """PII 加密会话管理页（Step 101-103，V17/V18，ADR-101/102/103）。

    默认打码语义不变：列表只显示 session_id/key_version/时间；
    还原表单提交到本页，还原结果仅存在于本次响应体（内存），
    不落盘。缺 key 时只显示提示、不提供还原表单，也不显示
    密钥管理卡片。key_ok/key_result 用于密钥轮换/设置结果，
    purge_ok/purged 用于按龄清理结果。
    """
    body: list[str] = []
    if not key_configured:
        body.append(f'<div class="alert alert-err">{escape(t(lang, "ui.pii_key_missing"))}</div>')
        body.append(f'<p class="meta">{escape(t(lang, "ui.pii_key_hint"))}</p>')
    else:
        body.append(
            f'<p class="meta">{escape(t(lang, "ui.pii_key_source"))}: '
            f"<code>{escape(key_source)}</code></p>"
        )
        if not sessions:
            body.append(f'<p class="meta">{escape(t(lang, "ui.pii_no_sessions"))}</p>')
        else:
            rows = []
            for session in sessions:
                created = session["created_at"]
                created_text = (
                    f"{created:%Y-%m-%d %H:%M}" if hasattr(created, "strftime") else str(created)
                )
                rows.append(
                    "<tr>"
                    f"<td><code>{escape(str(session['session_id']))}</code></td>"
                    f"<td>{escape(str(session['key_version']))}</td>"
                    f"<td>{escape(created_text)}</td>"
                    "</tr>"
                )
            body.append(
                "<table><tr>"
                f"<th>{escape(t(lang, 'ui.pii_session_id'))}</th>"
                f"<th>{escape(t(lang, 'ui.pii_key_version'))}</th>"
                f"<th>{escape(t(lang, 'ui.pii_created'))}</th>"
                f"</tr>{''.join(rows)}</table>"
            )
        body.append(
            f"<h2>{escape(t(lang, 'ui.pii_key_card'))}</h2>"
            '<form method="post" action="/ui/pii/rotate">'
            f'<button type="submit">{escape(t(lang, "ui.pii_rotate_button"))}</button>'
            "</form>"
            '<form method="post" action="/ui/pii/key">'
            f'<label for="new_key">{escape(t(lang, "ui.pii_new_key_label"))}</label>'
            '<input type="text" id="new_key" name="new_key" autocomplete="off">'
            f'<button type="submit">{escape(t(lang, "ui.pii_set_key_form"))}</button>'
            "</form>"
            f"<h2>{escape(t(lang, 'ui.pii_restore_form'))}</h2>"
            f'<p class="meta">{escape(t(lang, "ui.pii_explicit_note"))}</p>'
            '<form method="post" action="/ui/pii">'
            f'<label for="session_id">{escape(t(lang, "ui.pii_session_id"))}</label>'
            '<input type="text" id="session_id" name="session_id" required '
            'placeholder="pii_...">'
            f'<label for="text">{escape(t(lang, "ui.pii_restore_text"))}</label>'
            '<textarea id="text" name="text" rows="4" required></textarea>'
            f'<button type="submit">{escape(t(lang, "ui.pii_restore_button"))}</button>'
            "</form>"
        )
    body.append(
        f"<h2>{escape(t(lang, 'ui.pii_purge_form'))}</h2>"
        f'<p class="meta">{escape(t(lang, "ui.pii_purge_days"))}</p>'
        '<form method="post" action="/ui/pii/purge">'
        '<input type="number" id="older_than_days" name="older_than_days" '
        'min="1" value="30" required>'
        f'<button type="submit">{escape(t(lang, "ui.pii_purge_button"))}</button>'
        "</form>"
    )
    if key_ok:
        result_lines = [f"<strong>{escape(key_ok)}:</strong>"]
        if key_result:
            result_lines.append("<pre>")
            for field, value in key_result.items():
                result_lines.append(f"{escape(str(field))}: {escape(str(value))}")
            result_lines.append("</pre>")
        body.append('<div class="alert alert-ok">' + "".join(result_lines) + "</div>")
    if purge_ok:
        body.append(
            '<div class="alert alert-ok"><strong>'
            f"{escape(purge_ok)}:</strong> <code>purged: {purged}</code></div>"
        )
    if restored is not None:
        body.append(
            '<div class="alert alert-ok"><strong>'
            f"{escape(t(lang, 'ui.pii_restore_result'))}:</strong>"
            f"<pre>{escape(restored)}</pre></div>"
        )
    if error:
        body.append(f'<div class="alert alert-err">{escape(error)}</div>')
    return _page(t(lang, "ui.pii_title"), "\n".join(body), lang=lang)
