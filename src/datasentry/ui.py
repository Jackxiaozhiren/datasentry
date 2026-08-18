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
from typing import Any, cast

from datasentry.trends import DatasetTrend, ScanPoint
from datasentry_core.models.drift import DriftReport
from datasentry_core.models.enums import RepairRunStatus, Severity
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
.stats { display: flex; gap: .6rem; margin: .5rem 0; }
.stat { padding: .35rem .8rem; border-radius: .5rem; background: #f0f2f4;
        border: 1px solid #d0d7de; font-weight: 600; }
.stat-ok { background: #e8f5e9; border-color: #a5d6a7; }
.stat-bad { background: #fde8e8; border-color: #f6b1b1; }
.batch-banner { padding: 0.7rem 1rem; border-radius: 6px; margin-bottom: 1rem;
                background: #e8f5e9; border: 1px solid #a5d6a7; }
.batch-banner.warn { background: #fff3e0; border-color: #ffcc80; }
.batch-banner ul { margin: 0.4rem 0 0; padding-left: 1.2rem; }
.batch-banner li { font-size: 0.9rem; }
.delta.pos { color: #1a7f37; font-weight: 600; }
.delta.neg { color: #cf222e; font-weight: 600; }
.delta.flat { color: #59636e; }
.diff-del { background: #fde8e8; }
.diff-add { background: #e8f5e9; }
.diff-side { font-weight: 600; white-space: nowrap; }
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
            f'<a href="/ui/repairs">{escape(t(lang, "ui.nav_repairs"))}</a>'
            f'<a href="/ui/pii">{escape(t(lang, "ui.nav_pii"))}</a>'
            f'<a href="/api/docs">{escape(t(lang, "ui.nav_api_docs"))}</a>'
            "</nav>",
            f"<h1>{escape(title)}</h1>",
            body,
            f"<footer>{escape(t(lang, 'ui.footer'))}</footer>",
            "</body></html>",
        ]
    )


_DIM_COLORS = ["#1f77b4", "#ff7f0e", "#2ca02c", "#d62728", "#9467bd", "#8c564b"]


def _dim_strip(scan: ScanRun) -> str:
    """V27：扫描列表行内六维迷你条（色同趋势图，悬停显示维度分数）。"""
    if not scan.quality_score or not scan.quality_score.dimensions:
        return ""
    dims_sorted = sorted(scan.quality_score.dimensions)
    segments = []
    for idx, dim in enumerate(dims_sorted):
        value = scan.quality_score.dimensions[dim]
        if value is None:
            continue
        segments.append(
            f'<span style="display:inline-block;height:6px;flex:0 0 {value:.1f}%;'
            f"background:{_DIM_COLORS[idx % len(_DIM_COLORS)]}"
            f'" title="{escape(dim)} {value:.1f}"></span>'
        )
    if not segments:
        return ""
    joined = "".join(segments)
    return f'<div class="dim-strip" style="display:flex;gap:1px;max-width:120px">{joined}</div>'


def _batch_banner(batch: dict[str, object] | None, *, lang: str = "en") -> str:
    """V27：批量扫描汇总横幅（成功绿 / 有失败橙），一次渲染后清除。"""
    if not batch:
        return ""
    failed = cast(list[dict[str, str]], batch.get("errors") or [])
    if failed:
        detail = "".join(
            f"<li>{escape(str(e['path']))}: {escape(str(e['error']))}</li>" for e in failed
        )
        tone = "batch-banner warn"
    else:
        detail = ""
        tone = "batch-banner"
    summary = t(
        lang,
        "ui.batch_done" if not failed else "ui.batch_done_partial",
    ).format(
        files=batch["files_scanned"],
        issues=batch["total_issues"],
        score=batch.get("avg_score") if batch.get("avg_score") is not None else "—",
        failed=len(failed) if failed else 0,
    )
    return (
        f'<div class="{tone}"><strong>{escape(summary)}</strong>'
        + (f"<ul>{detail}</ul>" if detail else "")
        + "</div>"
    )


def _scan_table(scans: list[ScanRun], *, lang: str = "en") -> str:
    if not scans:
        return f'<p class="meta">{escape(t(lang, "ui.no_scans"))}</p>'
    rows = []
    for scan in scans:
        overall = f"{scan.quality_score.overall:.1f}" if scan.quality_score else "—"
        rows.append(
            "<tr>"
            f'<td><input type="checkbox" name="runs" value="{escape(scan.id)}" '
            'class="run-check"></td>'
            f'<td><a href="/ui/scans/{escape(scan.id)}">{escape(scan.id)}</a></td>'
            f"<td>{escape(scan.dataset_id)}</td>"
            f"<td>{scan.fingerprint.row_count} × {scan.fingerprint.column_count}</td>"
            f'<td class="priority">{overall}{_dim_strip(scan)}</td>'
            f"<td>{escape(scan.status)}</td>"
            f"<td>{scan.started_at:%Y-%m-%d %H:%M}</td>"
            "</tr>"
        )
    return (
        f'<form method="get" action="/ui/compare" id="compare-form">'
        "<table><tr>"
        f'<th><input type="checkbox" id="run-check-all" aria-label="select all"></th>'
        f"<th>{escape(t(lang, 'ui.run_id'))}</th><th>{escape(t(lang, 'ui.dataset'))}</th>"
        f"<th>{escape(t(lang, 'ui.rows_cols'))}</th><th>{escape(t(lang, 'ui.score'))}</th>"
        f"<th>{escape(t(lang, 'ui.status'))}</th><th>{escape(t(lang, 'ui.started'))}</th>"
        f"</tr>{''.join(rows)}</table>"
        '<p><button type="submit" id="compare-btn" disabled>'
        f"{escape(t(lang, 'ui.compare_selected'))}</button></p>"
        "</form>"
        "<script>(function () {"
        'var form = document.getElementById("compare-form");'
        'var btn = document.getElementById("compare-btn");'
        'var all = document.getElementById("run-check-all");'
        "function sync() {"
        'var n = form.querySelectorAll(".run-check:checked").length;'
        "btn.disabled = n !== 2;"
        "if (all) { all.checked = n === form.querySelectorAll('.run-check').length; }"
        "}"
        'form.addEventListener("change", sync);'
        "form.addEventListener('submit', function (ev) {"
        "var n = form.querySelectorAll('.run-check:checked').length;"
        "if (n !== 2) { ev.preventDefault(); }"
        "});"
        'if (all) { all.addEventListener("change", function () {'
        "form.querySelectorAll('.run-check').forEach(function (c) { c.checked = all.checked; });"
        "sync(); }); }"
        "})();</script>"
    )


def render_home(
    scans: list[ScanRun],
    *,
    batch: dict[str, object] | None = None,
    lang: str = "en",
) -> str:
    scan_label = escape(t(lang, "ui.scan_button"))
    body = [
        f"<h2>{escape(t(lang, 'ui.workspace_overview'))}</h2>",
        _batch_banner(batch, lang=lang),
        _scan_table(scans, lang=lang),
        f"<h2>{escape(t(lang, 'ui.new_scan'))}</h2>",
        '<form method="post" action="/ui/scans" id="scan-form">'
        f'<label for="path">{escape(t(lang, "ui.data_file_path"))}</label>'
        '<input type="text" id="path" name="path" required placeholder="data/customers.csv">'
        f'<button type="submit" id="scan-btn">{scan_label}</button>',
        "</form>",
        '<div id="scan-progress" hidden><p id="scan-progress-text"></p>'
        '<div style="border:1px solid #999;height:14px;width:100%;max-width:480px">'
        '<div id="scan-progress-bar" style="height:14px;width:0%;background:#4caf50"></div>'
        "</div></div>",
        _scan_progress_script(),
    ]
    return _page(t(lang, "ui.home_title"), "\n".join(body), lang=lang)


def _scan_progress_script() -> str:
    """V25：扫描表单 AJAX + 轮询进度渲染实时进度条（批量走 latest 端点）。"""
    return """<script>
(function () {
  var form = document.getElementById("scan-form");
  if (!form) return;
  var bar = document.getElementById("scan-progress-bar");
  var text = document.getElementById("scan-progress-text");
  var box = document.getElementById("scan-progress");
  var btn = document.getElementById("scan-btn");
  form.addEventListener("submit", function (ev) {
    ev.preventDefault();
    var path = document.getElementById("path").value.trim();
    if (!path) return;
    var batch = /[,;\\n\\r*?]/.test(path);
    btn.disabled = true;
    box.hidden = false;
    text.textContent = "starting…";
    var timer = setInterval(function () {
      var url = batch
        ? "/scans/progress/latest"
        : "/scans/progress?path=" + encodeURIComponent(path);
      fetch(url, {cache: "no-store"})
        .then(function (r) { return r.ok ? r.json() : null; })
        .then(function (p) {
          if (!p) return;
          var pct = p.total > 0 ? Math.round(100 * p.done / p.total) : 0;
          bar.style.width = pct + "%";
          var label = p.path ? p.path.replace(/^.*[\\/\\\\]/, "") : "";
          var pctText = p.scanning
            ? (label ? label + " · " : "") + "detector " + p.done + "/" + p.total
              + " — " + (p.detector || "")
            : "done (" + p.done + "/" + p.total + ")";
          text.textContent = pctText;
          if (!p.scanning) { clearInterval(timer); }
        }).catch(function () {});
    }, 400);
    fetch("/ui/scans", {method: "POST", body: new FormData(form)})
      .then(function (r) {
        if (r.redirected) { window.location.href = r.url; return; }
        return r.text();
      })
      .then(function (body) {
        if (!body) return;
        clearInterval(timer);
        box.hidden = false;
        text.textContent = "scan failed";
        bar.style.background = "#e53935";
        var m = body.match(/<h1>([^<]*)</);
        if (m && m[1]) text.textContent = m[1];
      })
      .catch(function () { clearInterval(timer); btn.disabled = false; });
  });
})();
</script>"""


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


def _dimension_lines(points: list[ScanPoint]) -> str:
    """六维质量折线（V25，内联 SVG 零依赖）：每个维度一条线，y 轴 0-100。

    仅当至少一个点带维度分且维度 >1 时渲染；None 段跳过（断开）。
    """
    dims: set[str] = set()
    for p in points:
        if p.dimensions:
            dims.update(p.dimensions)
    if not dims or not any(p.dimensions for p in points):
        return ""
    dims_sorted = sorted(dims)
    if len(points) < 2:
        return ""
    width, height, pad = 480, 140, 8
    step = (width - 2 * pad) / (len(points) - 1)
    colors = ["#1f77b4", "#ff7f0e", "#2ca02c", "#d62728", "#9467bd", "#8c564b"]
    lines_svg: list[str] = []
    for idx, dim in enumerate(dims_sorted):
        coords: list[str] = []
        for i, point in enumerate(points):
            value = (point.dimensions or {}).get(dim)
            if value is None:
                continue
            x = pad + i * step
            y = height - pad - (min(max(value, 0.0), 100.0) / 100.0) * (height - 2 * pad)
            coords.append(f"{x:.1f},{y:.1f}")
        if len(coords) < 2:
            continue
        color = colors[idx % len(colors)]
        lines_svg.append(
            f'<polyline points="{" ".join(coords)}" fill="none" '
            f'stroke="{color}" stroke-width="1.6" opacity="0.9"/>'
        )
    if not lines_svg:
        return ""
    legend = " ".join(
        f'<span style="color:{colors[i % len(colors)]}">▬ {escape(d)}</span>'
        for i, d in enumerate(dims_sorted)
    )
    return (
        f'<svg class="dim-lines" width="{width}" height="{height}" '
        f'viewBox="0 0 {width} {height}" role="img" aria-label="quality dimensions over time">'
        + "".join(lines_svg)
        + "</svg>"
        f'<p class="meta dim-legend">{legend}</p>'
    )


def _dimension_table(points: list[ScanPoint], *, lang: str = "en") -> str:
    """V26：维度数值表（行=扫描，列=六维分；None 显示 —）。"""
    dims: set[str] = set()
    for p in points:
        if p.dimensions:
            dims.update(p.dimensions)
    dims_sorted = sorted(dims)
    if not dims_sorted or not any(p.dimensions for p in points):
        return ""
    header = (
        f"<tr><th>{escape(t(lang, 'ui.run_id'))}</th>"
        + "".join(f"<th>{escape(d)}</th>" for d in dims_sorted)
        + "</tr>"
    )
    rows = []
    for p in points:
        cells = []
        for d in dims_sorted:
            v = (p.dimensions or {}).get(d)
            cells.append(f"<td>{v:.1f}</td>" if v is not None else '<td class="meta">—</td>')
        rows.append(
            f'<tr><td><a href="/ui/scans/{escape(p.run_id)}">'
            f"{escape(p.run_id)}</a></td>{''.join(cells)}</tr>"
        )
    return f'<table class="dim-table">{header}{"".join(rows)}</table>'


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
            + _dimension_lines(points)
            + _dimension_table(points, lang=lang)
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


def render_batch_repair(
    run_id: str,
    issues: list[Issue],
    proposals: dict[str, RepairProposal],
    errors: dict[str, str],
    *,
    source_path: str = "",
    lang: str = "en",
) -> str:
    """V30：批量修复提案结果页（只生成提案，不应用——apply 走单条工作台）。

    V31：提案行可勾选 → 批量 apply（写数据，需用户显式提交）。
    """
    rows = []
    for issue in issues:
        prop = proposals.get(issue.id)
        err = errors.get(issue.id)
        if prop is not None:
            badge = f'<td class="badge badge-ok">{escape(t(lang, "ui.proposed"))}</td>'
            cells = (
                f"<td>{escape(prop.operation)}</td>"
                f"<td>{escape(', '.join(prop.target_columns))}</td>"
                f"<td>{escape(prop.risk_level.value)}</td>"
                f"<td>{prop.estimated_rows_changed}</td>"
            )
            check = (
                f'<td><input type="checkbox" name="issue_ids" '
                f'value="{escape(issue.id)}" class="apply-check"></td>'
            )
        elif err:
            badge = f'<td class="badge badge-critical">{escape(t(lang, "ui.error"))}</td>'
            cells = f'<td colspan="4" class="meta">{escape(err)}</td>'
            check = "<td></td>"
        else:
            badge = f'<td class="badge">{escape(t(lang, "ui.unsupported"))}</td>'
            cells = f'<td colspan="4" class="meta">{escape(t(lang, "ui.no_proposal_hint"))}</td>'
            check = "<td></td>"
        title_cell = escape(mask_text_pii(translate_title(lang, issue.title, issue.issue_type)))
        rows.append(
            "<tr>" + check + f'<td><a href="/ui/scans/{escape(run_id)}/issues/{escape(issue.id)}">'
            f"{escape(issue.id)}</a></td>"
            f"<td>{title_cell}</td>"
            + badge
            + cells
            + f"<td><a href='/ui/scans/{escape(run_id)}/issues/{escape(issue.id)}'>"
            f"{escape(t(lang, 'ui.apply_repair'))}</a></td>"
            "</tr>"
        )
    summary = (
        f'<p class="meta">{escape(t(lang, "ui.batch_repair_summary"))}: '
        f"{len(proposals)} / {len(issues)} · {escape(source_path)}</p>"
    )
    body = [
        '<p class="meta">'
        f'<a href="/ui/scans/{escape(run_id)}">{escape(t(lang, "ui.back_to_scan"))}</a></p>',
        summary,
        '<form method="post" '
        f'action="/ui/scans/{escape(run_id)}/repairs/batch-apply" '
        'id="batch-apply-form">'
        f'<input type="hidden" name="source_path" value="{escape(source_path)}">'
        f'<p class="meta">{escape(t(lang, "ui.batch_apply_note"))}</p>'
        "<table><tr><th>"
        '<input type="checkbox" id="select-all" aria-label="select all">'
        "</th><th>issue</th><th>title</th><th>status</th><th>operation</th>"
        "<th>columns</th><th>risk</th><th>rows</th><th></th></tr>" + "".join(rows) + "</table>"
        f'<button type="submit" id="batch-apply-btn" disabled>'
        f"{escape(t(lang, 'ui.batch_apply'))}</button>"
        "</form>"
        "<script>(function () {"
        'var btn = document.getElementById("batch-apply-btn");'
        'var all = document.getElementById("select-all");'
        "if (!btn || !all) return;"
        "function sync() {"
        "var checks = document.querySelectorAll('.apply-check');"
        "var n = 0;"
        "for (var i = 0; i < checks.length; i++) {"
        "if (checks[i].checked) n++;"
        "}"
        "btn.disabled = n === 0;"
        "all.checked = n > 0 && n === checks.length;"
        "}"
        "all.addEventListener('change', function () {"
        "var checks = document.querySelectorAll('.apply-check');"
        "for (var i = 0; i < checks.length; i++) {"
        "checks[i].checked = all.checked;"
        "}"
        "sync();"
        "});"
        "document.addEventListener('change', sync);"
        "})();</script>",
        f'<p class="meta">{escape(t(lang, "ui.batch_propose_note"))}</p>',
    ]
    return _page(
        t(lang, "ui.batch_repair_title"),
        "\n".join(body),
        active="scans",
        lang=lang,
    )


def render_batch_rollback(
    runs: list[RepairRun],
    errors: dict[str, str],
    *,
    lang: str = "en",
) -> str:
    """V34：批量回滚结果页——每行 rolled back 或 error。"""
    rows = []
    for run in runs:
        err = errors.get(run.id)
        if err is None:
            status_cell = (
                f'<td class="badge">{escape(t(lang, "ui.repair_status_rolled_back"))}</td>'
                "<td>—</td>"
            )
        else:
            status_cell = (
                f'<td class="badge badge-critical">{escape(t(lang, "ui.error"))}</td>'
                f'<td colspan="2" class="meta">{escape(err)}</td>'
            )
        ops = ", ".join(sorted({str(op.operation) for op in run.operations})) or "—"
        rows.append(
            "<tr>"
            f"<td>{escape(run.id)}</td>"
            f"<td>{escape(run.dataset_id)}</td>"
            f"<td>{escape(ops)}</td>" + status_cell + "</tr>"
        )
    body = [
        f'<p class="meta">{escape(t(lang, "ui.batch_rollback_summary"))}: '
        f"{len(runs) - len(errors)} / {len(runs)}</p>",
        '<p class="meta">'
        f'<a href="/ui/repairs">{escape(t(lang, "ui.repairs_title"))}</a>'
        f" · <a href='/ui/scans'>{escape(t(lang, 'ui.nav_scans'))}</a></p>",
        "<table><tr><th>run id</th><th>dataset</th><th>operation</th><th>status</th>"
        "<th>rows</th></tr>" + "".join(rows) + "</table>",
    ]
    return _page(
        t(lang, "ui.batch_rollback_title"),
        "\n".join(body),
        active="repairs",
        lang=lang,
    )


def render_repairs(
    runs: list[RepairRun],
    *,
    lang: str = "en",
) -> str:
    """V32：修复历史页——所有修复 run（状态/操作/行数/时间/回滚入口）。"""
    if not runs:
        body = f'<p class="meta">{escape(t(lang, "ui.repairs_empty"))}</p>'
    else:
        rows = []
        for run in sorted(runs, key=lambda r: r.created_at, reverse=True):
            ops = ", ".join(sorted({str(op.operation) for op in run.operations})) or "—"
            rows_changed = len(run.operations)
            if run.status == RepairRunStatus.APPLIED:
                status_cell = (
                    f'<td class="badge badge-ok">{escape(t(lang, "ui.repair_status_applied"))}</td>'
                    "<td>"
                    f'<form method="post" action="/ui/repairs/{escape(run.id)}/rollback">'
                    f'<button class="linklike" type="submit">'
                    f"{escape(t(lang, 'ui.rollback_link'))}</button>"
                    "</form></td>"
                    "<td>"
                    f'<form method="post" action="/ui/repairs/{escape(run.id)}/verify">'
                    f'<button class="linklike" type="submit">'
                    f"{escape(t(lang, 'ui.verify'))}</button>"
                    "</form></td>"
                )
            elif run.status == RepairRunStatus.ROLLED_BACK:
                status_cell = (
                    f'<td class="badge">{escape(t(lang, "ui.repair_status_rolled_back"))}</td>'
                    "<td></td><td></td>"
                )
            else:
                failed_badge = escape(t(lang, "ui.repair_status_failed"))
                status_cell = (
                    f'<td class="badge badge-critical">{failed_badge}</td><td></td><td></td>'
                )
            rows.append(
                f'<tr id="{escape(run.id)}">'
                f'<td><a href="/ui/repairs/{escape(run.id)}/artifact">{escape(run.id)}</a></td>'
                f"<td>{escape(run.dataset_id)}</td>"
                f"<td>{escape(ops)}</td>"
                f"<td>{rows_changed}</td>"
                + status_cell
                + f"<td>{escape(run.created_at.strftime('%Y-%m-%d %H:%M'))}</td>"
                "</tr>"
            )
        body = (
            "<table><tr><th>run id</th><th>dataset</th><th>operation</th><th>rows</th>"
            "<th>status</th><th></th><th></th><th>created</th></tr>" + "".join(rows) + "</table>"
        )
    return _page(
        t(lang, "ui.repairs_title"),
        body,
        active="repairs",
        lang=lang,
    )


def render_repair_artifact(
    run: RepairRun,
    columns: list[str],
    before_rows: list[list[object]],
    after_rows: list[list[object]],
    changed_indices: list[int],
    *,
    lang: str = "en",
) -> str:
    """V43：修复工件页——before 快照 vs 修复副本的逐行 diff（行号 1-based 数据行）。"""
    ops = ", ".join(sorted({str(op.operation) for op in run.operations})) or "—"
    rows_changed = len(run.operations)
    head = (
        "<table>"
        f"<tr><th>run id</th><td>{escape(run.id)}</td></tr>"
        f"<tr><th>dataset</th><td>{escape(run.dataset_id)}</td></tr>"
        f"<tr><th>{escape(t(lang, 'ui.artifact_ops'))}</th><td>{escape(ops)}</td></tr>"
        f"<tr><th>{escape(t(lang, 'ui.artifact_columns'))}</th>"
        f"<td>{escape(', '.join(columns))}</td></tr>"
        f"<tr><th>{escape(t(lang, 'ui.artifact_changed_rows'))}</th>"
        f"<td>{rows_changed} / {len(before_rows)}</td></tr>"
        "</table>"
    )
    actions = ""
    if run.status == RepairRunStatus.APPLIED:
        actions = (
            "<p>"
            f'<form method="post" action="/ui/repairs/{escape(run.id)}/verify" '
            f'style="display:inline">'
            f'<button class="linklike" type="submit">'
            f"{escape(t(lang, 'ui.verify'))}</button></form>"
            " &middot; "
            f'<form method="post" action="/ui/repairs/{escape(run.id)}/rollback" '
            f'style="display:inline">'
            f'<button class="linklike" type="submit">'
            f"{escape(t(lang, 'ui.rollback_link'))}</button></form>"
            "</p>"
        )
    if not changed_indices:
        body = f'<p class="meta">{escape(t(lang, "ui.artifact_no_changes"))}</p>'
    else:
        thead = "".join(f"<th>{escape(c)}</th>" for c in columns)
        diff_rows = []
        for i in changed_indices:
            before = before_rows[i] if i < len(before_rows) else []
            after = after_rows[i] if i < len(after_rows) else []
            line_no = i + 2
            before_cells = "".join(
                (
                    f'<td class="diff-del">{escape(str(v)) if v is not None else "∅"}</td>'
                    if (i2 < len(before) and i2 < len(after) and before[i2] != after[i2])
                    else f"<td>{escape(str(v)) if v is not None else '∅'}</td>"
                )
                for i2, v in enumerate(before)
            )
            after_cells = "".join(
                (
                    f'<td class="diff-add">{escape(str(v)) if v is not None else "∅"}</td>'
                    if (i2 < len(before) and i2 < len(after) and before[i2] != after[i2])
                    else f"<td>{escape(str(v)) if v is not None else '∅'}</td>"
                )
                for i2, v in enumerate(after)
            )
            diff_rows.append(
                '<tr class="diff-row">'
                f'<td rowspan="2" class="meta">{escape(t(lang, "ui.artifact_line"))} {line_no}</td>'
                f'<td class="meta diff-side">{escape(t(lang, "ui.artifact_before"))}</td>'
                f"{before_cells}</tr>"
            )
            diff_rows.append(
                f'<tr><td class="meta diff-side">{escape(t(lang, "ui.artifact_after"))}</td>'
                f"{after_cells}</tr>"
            )
        body = f"<table><tr><th></th><th></th>{thead}</tr>" + "".join(diff_rows) + "</table>"
    return _page(t(lang, "ui.artifact_title"), head + actions + body, active="repairs", lang=lang)


def render_batch_apply(
    run_id: str,
    issues: list[Issue],
    runs: dict[str, RepairRun],
    errors: dict[str, str],
    *,
    skipped: dict[str, str] | None = None,
    source_path: str = "",
    lang: str = "en",
) -> str:
    """V31：批量 apply 结果页——每行 applied（含回滚链接）或 error。

    V34：applied 行可勾选 → 批量回滚（写数据，需用户显式提交）。
    V40：skipped（无提案）单独归类 + 顶部统计卡片。
    """
    rows = []
    for issue in issues:
        run = runs.get(issue.id)
        err = errors.get(issue.id)
        if run is not None:
            rollback = f"/ui/scans/{escape(run_id)}/repairs/{escape(run.id)}/rollback"
            ops = ", ".join(sorted({str(op.operation) for op in run.operations})) or "—"
            rows_changed = len(run.operations)
            status_cell = (
                f'<td class="badge badge-ok">{escape(t(lang, "ui.applied"))}</td>'
                f'<td><a href="/ui/repairs/{escape(run.id)}/artifact">{escape(run.id)}</a></td>'
                f"<td>{escape(ops)}</td>"
                f"<td>{rows_changed}</td>"
                "<td>"
                f'<form method="post" action="{rollback}">'
                f'<button class="linklike" type="submit">'
                f"{escape(t(lang, 'ui.rollback_link'))}</button>"
                "</form></td>"
                "<td>"
                f'<form method="post" action="/ui/repairs/{escape(run.id)}/verify">'
                f'<button class="linklike" type="submit">'
                f"{escape(t(lang, 'ui.verify'))}</button>"
                "</form></td>"
            )
            check = (
                f'<td><input type="checkbox" name="repair_run_ids" '
                f'value="{escape(run.id)}" class="rollback-check"></td>'
            )
        else:
            status_cell = (
                f'<td class="badge badge-critical">{escape(t(lang, "ui.error"))}</td>'
                f'<td colspan="4" class="meta">{escape(err or "")}</td>'
            )
            check = "<td></td>"
        title_cell = escape(mask_text_pii(translate_title(lang, issue.title, issue.issue_type)))
        rows.append(
            "<tr>" + check + f'<td><a href="/ui/scans/{escape(run_id)}/issues/{escape(issue.id)}">'
            f"{escape(issue.id)}</a></td>"
            f"<td>{title_cell}</td>" + status_cell + "</tr>"
        )
    skipped = skipped or {}
    applied_n = len(runs)
    skipped_n = len(skipped)
    failed_n = len(errors)
    body = [
        '<div class="stats">'
        f'<span class="stat stat-ok">{escape(t(lang, "ui.applied"))}: {applied_n}</span>'
        f'<span class="stat">{escape(t(lang, "ui.skipped"))}: {skipped_n}</span>'
        f'<span class="stat stat-bad">{escape(t(lang, "ui.error"))}: {failed_n}</span>'
        "</div>"
        f'<p class="meta">{escape(t(lang, "ui.batch_apply_summary"))}: '
        f"{len(issues)} issues · {escape(source_path)}</p>",
        '<p class="meta">'
        f'<a href="/ui/scans/{escape(run_id)}">{escape(t(lang, "ui.back_to_scan"))}</a></p>',
        '<form method="post" '
        f'action="/ui/scans/{escape(run_id)}/repairs/batch-rollback" '
        'id="batch-rollback-form">'
        f'<p class="meta">{escape(t(lang, "ui.batch_rollback_note"))}</p>'
        "<table><tr><th></th><th>issue</th><th>title</th><th>status</th><th>run id</th>"
        "<th>operation</th><th>rows</th><th></th></tr>" + "".join(rows) + "</table>"
        f'<button type="submit" id="batch-rollback-btn" disabled>'
        f"{escape(t(lang, 'ui.rollback_selected'))}</button>"
        "</form>"
        "<script>(function () {"
        'var btn = document.getElementById("batch-rollback-btn");'
        "if (!btn) return;"
        "function sync() {"
        "var n = document.querySelectorAll('.rollback-check:checked').length;"
        "btn.disabled = n === 0;"
        "}"
        "document.addEventListener('change', sync);"
        "})();</script>",
        f'<p class="meta">{escape(t(lang, "ui.batch_apply_done_note"))}</p>',
    ]
    return _page(
        t(lang, "ui.batch_apply_title"),
        "\n".join(body),
        active="scans",
        lang=lang,
    )


def _issue_rows(issues: list[Issue], run_id: str, *, lang: str = "en") -> str:
    if not issues:
        return f'<p class="meta">{escape(t(lang, "ui.no_issues"))}</p>'
    rows = []
    for issue in issues:
        cols = ", ".join(escape(c) for c in issue.columns) or "—"
        rows.append(
            '<div class="issue-card">'
            f'<input type="checkbox" name="issue_ids" value="{escape(issue.id)}" '
            'class="issue-check" aria-label="select issue">'
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
        '<form method="post" '
        f'action="/ui/scans/{escape(scan.id)}/repairs/batch-propose" '
        'id="batch-repair-form">'
        f'<label for="batch-source-path">{escape(t(lang, "ui.source_path"))}</label>'
        f'<input type="text" id="batch-source-path" name="source_path" '
        f'value="{escape(scan.source_path or "")}" '
        'placeholder="data/orders.csv" required>'
        f'<button type="submit" id="batch-propose-btn" disabled>'
        f"{escape(t(lang, 'ui.batch_propose'))}</button>"
        "</form>",
        _issue_rows(issues, scan.id, lang=lang),
        "<script>(function () {"
        'var btn = document.getElementById("batch-propose-btn");'
        "if (!btn) return;"
        "function sync() {"
        "var n = document.querySelectorAll('.issue-check:checked').length;"
        "btn.disabled = n === 0;"
        "}"
        "document.addEventListener('change', sync);"
        "})();</script>",
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


def _fixed_repair_context(
    key: tuple[str, str],
    repairs: list[RepairRun] | None,
    ref_dataset_id: str | None,
    lang: str,
) -> str:
    """V37：FIXED 组显示关联的 applied 修复（dataset + 操作列交集匹配）。"""
    if not repairs or not ref_dataset_id:
        return "<td></td>"
    _, columns = key
    col_set = set(c.strip() for c in columns.split(","))
    links = []
    for run in repairs:
        if run.status != RepairRunStatus.APPLIED or run.dataset_id != ref_dataset_id:
            continue
        run_cols = {op.column for op in run.operations}
        if not (run_cols & col_set):
            continue
        ops = ", ".join(sorted({str(op.operation) for op in run.operations})) or "—"
        date = run.created_at.strftime("%Y-%m-%d")
        links.append(
            f'<a href="/ui/repairs#{escape(run.id)}">{escape(run.id)}</a>'
            f" · {escape(ops)} · {escape(date)}"
        )
    if not links:
        return "<td></td>"
    label = escape(t(lang, "ui.fixed_by"))
    return f"<td><span class='badge badge-info'>{label}</span> " + "<br>".join(links) + "</td>"


def _issue_diff_table(
    issues_ref: list[Issue],
    issues_cur: list[Issue],
    *,
    propose_run_id: str | None = None,
    source_path: str | None = None,
    repairs: list[RepairRun] | None = None,
    ref_dataset_id: str | None = None,
    lang: str = "en",
) -> str:
    """V29：两 run 问题级 diff——按 (issue_type, columns) 分组三类：

    new（ref 无/cur 有，红 NEW）、fixed（ref 有/cur 无，绿 FIXED）、
    persistent（两者都有，Δ 计数）；每组附 cur 侧前 3 个示例。
    """

    def key(issue: Issue) -> tuple[str, str]:
        return (issue.issue_type, ",".join(issue.columns))

    from collections import Counter

    ref_counts = Counter(key(i) for i in issues_ref)
    cur_counts = Counter(key(i) for i in issues_cur)
    cur_examples: dict[tuple[str, str], list[Issue]] = {}
    for issue in issues_cur:
        cur_examples.setdefault(key(issue), []).append(issue)

    def rows_for(group: list[tuple[tuple[str, str], int]], kind: str) -> str:
        rows = []
        for k, cur_total in group:
            issue_type, columns = k
            before = ref_counts.get(k, 0)
            delta = cur_total - before
            if kind == "new":
                badge = '<span class="badge badge-critical">NEW</span>'
            elif kind == "fixed":
                badge = '<span class="badge badge-ok">FIXED</span>'
                delta = before - cur_total
            else:
                badge = ""
                delta = cur_total - before
            tone = "pos" if delta < 0 else ("neg" if delta > 0 else "flat")
            examples = ""
            for ex in cur_examples.get(k, [])[:3]:
                link = f"/ui/scans/{escape(ex.scan_run_id)}?severity={ex.severity.value}"
                examples += (
                    f'<li><a href="{link}">{escape(ex.title)}</a>'
                    f" · {_severity_badge(ex.severity.value)} · "
                    f"{ex.affected_count} rows</li>"
                )
            propose = ""
            if kind == "new" and propose_run_id:
                ids = [i.id for i in cur_examples.get(k, [])]
                if ids:
                    hidden = "".join(
                        f'<input type="hidden" name="issue_ids" value="{escape(i)}">' for i in ids
                    )
                    src = (
                        f'<input type="hidden" name="source_path" '
                        f'value="{escape(source_path or "")}">'
                    )
                    propose = (
                        "<td>"
                        '<form method="post" '
                        f'action="/ui/scans/{escape(propose_run_id)}/repairs/batch-propose">'
                        f"{hidden}{src}"
                        f'<button class="linklike" type="submit">'
                        f"{escape(t(lang, 'ui.propose_repair'))}</button>"
                        "</form></td>"
                    )
                else:
                    propose = "<td></td>"
            else:
                propose = _fixed_repair_context(k, repairs, ref_dataset_id, lang)
            rows.append(
                f"<tr><td>{escape(issue_type)}</td><td>{escape(columns)}</td>"
                f"<td>{before}</td><td>{cur_total}</td>"
                f'<td class="delta {tone}">{badge}'
                f"{'+' if delta > 0 else ''}{delta}</td>"
                + propose
                + "</tr>"
                + (
                    f'<tr class="examples"><td colspan="6"><ul>{examples}</ul></td></tr>'
                    if examples
                    else ""
                ),
            )
        return "".join(rows)

    new_rows = rows_for([(k, v) for k, v in cur_counts.items() if ref_counts.get(k, 0) == 0], "new")
    fixed_keys = [k for k in ref_counts if cur_counts.get(k, 0) == 0]
    fixed_rows = rows_for([(k, ref_counts[k]) for k in fixed_keys], "fixed")
    persistent = [(k, cur_counts[k]) for k in ref_counts if cur_counts.get(k, 0) > 0]
    persistent_rows = rows_for(persistent, "persistent")

    def section(title: str, rows: str) -> str:
        return (
            f"<h3>{escape(title)}</h3>"
            "<table><tr><th>type</th><th>columns</th><th>ref</th><th>cur</th><th>Δ</th>"
            "<th></th></tr>"
            + (
                rows
                or '<tr><td colspan="6" class="meta">'
                + f"{escape(t(lang, 'ui.no_issue_diff'))}</td></tr>"
            )
            + "</table>"
        )

    return (
        f"<h2>{escape(t(lang, 'ui.issue_diff'))}</h2>"
        + section(t(lang, "ui.new_issues"), new_rows)
        + section(t(lang, "ui.fixed_issues"), fixed_rows)
        + section(t(lang, "ui.persistent_issues"), persistent_rows)
    )


def render_compare(
    reference: ScanRun,
    current: ScanRun,
    report: DriftReport,
    issues_ref: list[Issue],
    issues_cur: list[Issue],
    *,
    repairs: list[RepairRun] | None = None,
    lang: str = "en",
) -> str:
    """V28：两 run 对比页——六维差值、severity 变化、列漂移、schema 变更。

    reference 为基线（较早），current 为现状；差值 = current − reference。
    """
    ref_score = reference.quality_score.overall if reference.quality_score else None
    cur_score = current.quality_score.overall if current.quality_score else None
    score_delta = (
        cur_score - ref_score if (ref_score is not None and cur_score is not None) else None
    )

    dim_rows = []
    ref_dims = reference.quality_score.dimensions if reference.quality_score else {}
    cur_dims = current.quality_score.dimensions if current.quality_score else {}
    for dim in sorted(set(ref_dims) | set(cur_dims)):
        before = ref_dims.get(dim)
        after = cur_dims.get(dim)
        if before is None or after is None:
            continue
        delta = after - before
        tone = "pos" if delta > 0.05 else ("neg" if delta < -0.05 else "flat")
        dim_rows.append(
            f"<tr><td>{escape(dim)}</td><td>{before:.1f}</td><td>{after:.1f}</td>"
            f'<td class="delta {tone}">{"+" if delta > 0 else ""}{delta:.1f}</td></tr>'
        )

    severity_rows = []
    for level in ("critical", "high", "medium", "low", "info"):
        before = reference.issues_count.get(Severity(level), 0)
        after = current.issues_count.get(Severity(level), 0)
        delta = after - before
        tone = "pos" if delta < 0 else ("neg" if delta > 0 else "flat")
        severity_rows.append(
            f"<tr><td>{_severity_badge(level)}</td><td>{before}</td><td>{after}</td>"
            f'<td class="delta {tone}">{"+" if delta > 0 else ""}{delta}</td></tr>'
        )

    drift_rows = []
    for d in report.column_drifts:
        direction = (
            "↑"
            if d.direction in ("increase", "new_category")
            else ("↓" if d.direction in ("decrease", "gone_category") else "⇄")
        )
        drift_rows.append(
            f"<tr><td>{escape(d.column)}</td><td>{escape(d.drift_type)}</td>"
            f"<td>{escape(d.metric)}</td><td>{d.value:.3g} / {d.threshold:.3g}</td>"
            f"<td>{direction} {escape(d.direction)}</td>"
            f"<td>{_severity_badge(d.severity.value)}</td></tr>"
        )

    schema_rows = []
    for c in report.schema_changes:
        schema_rows.append(
            f"<tr><td>{escape(c.change_type)}</td><td>{escape(c.column)}</td>"
            f"<td>{escape(str(c.before))}</td><td>{escape(str(c.after))}</td></tr>"
        )

    score_line = (
        f"<p class='meta'>{escape(t(lang, 'ui.score_delta'))}: "
        f"<strong>{'+' if (score_delta or 0) > 0 else ''}{score_delta:.1f}</strong>"
        f" ({reference.quality_score.overall:.1f} → {current.quality_score.overall:.1f})</p>"
        if score_delta is not None and reference.quality_score and current.quality_score
        else ""
    )
    body = [
        '<p class="meta">'
        f'{escape(t(lang, "ui.compare_reference"))}: <a href="/ui/scans/{escape(reference.id)}">'
        f"{escape(reference.id)}</a> · {escape(reference.dataset_id)} · "
        f"{reference.started_at:%Y-%m-%d %H:%M}</p>",
        '<p class="meta">'
        f'{escape(t(lang, "ui.compare_current"))}: <a href="/ui/scans/{escape(current.id)}">'
        f"{escape(current.id)}</a> · {escape(current.dataset_id)} · "
        f"{current.started_at:%Y-%m-%d %H:%M}</p>",
        score_line,
        f"<h2>{escape(t(lang, 'ui.dimension_delta'))}</h2>",
        "<table><tr><th></th>"
        f"<th>{escape(t(lang, 'ui.compare_reference_short'))}</th>"
        f"<th>{escape(t(lang, 'ui.compare_current_short'))}</th>"
        f"<th>{escape(t(lang, 'ui.delta'))}</th></tr>" + "".join(dim_rows) + "</table>",
        f"<h2>{escape(t(lang, 'ui.severity_delta'))}</h2>",
        "<table><tr><th></th><th>ref</th><th>cur</th><th>Δ</th></tr>"
        + "".join(severity_rows)
        + "</table>",
        _issue_diff_table(
            issues_ref,
            issues_cur,
            propose_run_id=current.id,
            source_path=current.source_path,
            repairs=repairs,
            ref_dataset_id=reference.dataset_id,
            lang=lang,
        ),
        f"<h2>{escape(t(lang, 'ui.column_drifts'))}</h2>",
        "<table><tr><th>column</th><th>type</th><th>metric</th><th>value / threshold</th>"
        "<th>direction</th><th></th></tr>"
        + (
            "".join(drift_rows)
            if drift_rows
            else "<tr><td colspan='6'>" + f"{escape(t(lang, 'ui.no_drifts'))}</td></tr>"
        )
        + "</table>",
        f"<h2>{escape(t(lang, 'ui.schema_changes'))}</h2>",
        "<table><tr><th>type</th><th>column</th><th>before</th><th>after</th></tr>"
        + (
            "".join(schema_rows)
            if schema_rows
            else "<tr><td colspan='4'>" + f"{escape(t(lang, 'ui.no_schema_changes'))}</td></tr>"
        )
        + "</table>",
    ]
    return _page(
        f"{escape(reference.dataset_id)} vs {escape(current.dataset_id)}",
        "\n".join(body),
        active="scans",
        lang=lang,
    )


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
