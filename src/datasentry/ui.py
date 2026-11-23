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

from collections.abc import Sequence
from html import escape
from typing import Any, cast

from datasentry.trends import DatasetTrend, ScanPoint
from datasentry_core.models.drift import DriftReport
from datasentry_core.models.enums import RepairRunStatus, Severity
from datasentry_core.models.issue import Issue
from datasentry_core.models.repair import RepairPreview, RepairProposal, RepairRun
from datasentry_core.models.scan import ScanConfig, ScanRun
from datasentry_core.repair.engine import changed_cells
from datasentry_core.reporting import mask_text_pii
from datasentry_core.reporting.i18n import t
from datasentry_core.reporting.translate import translate_title

# The batch-repair form and the issue checkboxes live in different parts of the page, so the
# association is explicit (`form=`) rather than positional; one constant keeps the two ends from
# drifting apart, which is what made every browser submit of this form answer "no issues selected".
BATCH_REPAIR_FORM_ID = "batch-repair-form"

# The apply response is on the write path: a repair over a million-row file used to answer with a
# ~380 MB page. The artefact page stays uncapped because that is what "full" means there.
APPLY_DIFF_ROW_CAP = 50

_CSS = """
:root {
  color-scheme: light;
  --bg: #f5f7fa;
  --surface: #ffffff;
  --raised: #eceff4;
  --line: rgba(17, 24, 39, .09);
  --line-strong: rgba(17, 24, 39, .16);
  --ink: #111827;
  --ink-2: #4b5565;
  --ink-3: #6b7487;
  --accent: #1d4ed8;
  --accent-hover: #1a45bd;
  --accent-soft: rgba(29, 78, 216, .10);
  --pos: #15803d;
  --neg: #b91c1c;
  --sans: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Helvetica Neue", Arial,
          sans-serif;
  --mono: ui-monospace, SFMono-Regular, "SF Mono", Menlo, Consolas, monospace;
  --ease: cubic-bezier(.22, 1, .36, 1);
}
* { box-sizing: border-box; }
body {
  margin: 0;
  min-height: 100vh;
  display: grid;
  grid-template-columns: 248px minmax(0, 1fr);
  background: var(--bg);
  color: var(--ink);
  font-family: var(--sans);
  font-size: 14px;
  line-height: 1.55;
}
a { color: var(--accent); text-decoration: none; }
a:hover { text-decoration: underline; }
:focus-visible { outline: 2px solid var(--accent); outline-offset: 2px; border-radius: 4px; }

.sidebar {
  position: sticky;
  top: 0;
  align-self: start;
  height: 100vh;
  padding: 20px 12px;
  border-right: 1px solid var(--line);
}
.brand {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 0 10px 20px;
  font-size: 15px;
  font-weight: 500;
}
.brand .rule { width: 2px; height: 14px; background: var(--accent); }
.nav { display: grid; gap: 2px; }
.nav a {
  display: flex;
  align-items: center;
  height: 34px;
  padding: 0 10px;
  border-radius: 8px;
  color: var(--ink-2);
  transition: background-color 140ms var(--ease), color 140ms var(--ease);
}
.nav a:hover { background: var(--raised); color: var(--ink); text-decoration: none; }
.nav a[aria-current="page"] { background: var(--raised); color: var(--ink); font-weight: 500; }

.main { padding: 28px 40px 64px; max-width: 1440px; }
h1 { margin: 0 0 4px; font-size: 28px; font-weight: 500; line-height: 1.2; }
h2 {
  margin: 40px 0 12px;
  padding-top: 20px;
  border-top: 1px solid var(--line);
  font-size: 15px;
  font-weight: 500;
}
h3 { margin: 24px 0 8px; font-size: 14px; font-weight: 500; }
p { margin: 8px 0; }

table { border-collapse: collapse; width: 100%; margin: 12px 0;
        font-variant-numeric: tabular-nums; }
th {
  padding: 8px 12px;
  border-bottom: 1px solid var(--line-strong);
  text-align: left;
  font-size: 12px;
  font-weight: 500;
  color: var(--ink-3);
  white-space: nowrap;
}
td { padding: 8px 12px; border-bottom: 1px solid var(--line); vertical-align: top; }
tbody tr:hover td,
table tr:hover td { background: rgba(255, 255, 255, .6); }

table.kv th { width: 180px; font-weight: 400; color: var(--ink-3); }

.meta { font-size: 12px; color: var(--ink-3); font-variant-numeric: tabular-nums; }
.priority { font-weight: 500; }

.badge {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 1px 8px;
  border: 1px solid var(--line);
  border-radius: 4px;
  background: var(--raised);
  color: var(--ink-2);
  font-family: var(--mono);
  font-size: 11px;
  line-height: 18px;
  white-space: nowrap;
}
.badge::before {
  content: "";
  width: 5px;
  height: 5px;
  border-radius: 50%;
  background: currentColor;
}
.badge-ok { background: #ecf7f0; border-color: #cfe9d8; color: var(--pos); }
.badge-err { background: #fdeceb; border-color: #f7d4d1; color: var(--neg); }
.badge-flat { background: var(--raised); border-color: var(--line); color: var(--ink-2); }
.badge-critical { background: #fdeceb; border-color: #f7d4d1; color: var(--neg); }
.badge-high { background: #fdf0e7; border-color: #f6ddc9; color: #9a3412; }
.badge-medium { background: #fdf6e3; border-color: #f3e4bd; color: #92400e; }
.badge-low { background: var(--raised); border-color: var(--line); color: var(--ink-2); }
.badge-info { background: rgba(29, 78, 216, .07); border-color: rgba(29, 78, 216, .18);
              color: var(--accent); }

.score { display: flex; align-items: baseline; gap: 8px; margin: 12px 0 8px; }
.score .fig { font-size: 40px; font-weight: 500; line-height: 1; letter-spacing: -.02em;
              font-variant-numeric: tabular-nums; }
.score .k { font-size: 12px; color: var(--ink-3); }
.score .of { font-size: 12px; color: var(--ink-3); }
.dims { display: flex; flex-wrap: wrap; gap: 4px 16px; margin: 0; font-size: 12px;
        color: var(--ink-3); }
.dims b { color: var(--ink-2); font-weight: 500; font-variant-numeric: tabular-nums; }

.stats { display: flex; flex-wrap: wrap; gap: 8px; margin: 12px 0 16px; }
.stat { min-width: 128px; padding: 12px 16px; background: var(--surface);
        border: 1px solid var(--line); border-radius: 12px; }
.stat .k { display: block; font-size: 12px; color: var(--ink-3); }
.stat .v { display: block; margin-top: 2px; font-size: 24px; font-weight: 500; line-height: 1.2;
           font-variant-numeric: tabular-nums; }

.severity-filters { display: flex; flex-wrap: wrap; gap: 4px; margin: 12px 0 4px; }
.severity-filters a {
  display: inline-flex;
  align-items: center;
  height: 28px;
  padding: 0 10px;
  border: 1px solid transparent;
  border-radius: 6px;
  color: var(--ink-2);
  font-size: 13px;
  transition: background-color 140ms var(--ease), border-color 140ms var(--ease),
              color 140ms var(--ease);
}
.severity-filters a:hover { background: var(--raised); color: var(--ink); text-decoration: none; }
.severity-filters a.active { background: var(--surface); border-color: var(--line-strong);
                             color: var(--ink); font-weight: 500; }

form { margin: 16px 0; }
form.panel { padding: 20px; background: var(--surface); border: 1px solid var(--line);
             border-radius: 12px; }
form label { display: block; margin: 0 0 6px; font-size: 13px; color: var(--ink-2); }
form input[type=text], form input[type=number], form select, form textarea {
  width: 100%;
  padding: 8px 10px;
  border: 1px solid var(--line-strong);
  border-radius: 8px;
  background: var(--surface);
  color: var(--ink);
  font: inherit;
  font-size: 14px;
  transition: border-color 140ms var(--ease);
}
form input:focus, form select:focus, form textarea:focus {
  border-color: var(--accent);
  box-shadow: 0 0 0 3px var(--accent-soft);
  outline: none;
}
form input[type=checkbox], td input[type=checkbox] { accent-color: var(--accent); }

button {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  height: 34px;
  padding: 0 16px;
  border: 1px solid var(--accent);
  border-radius: 8px;
  background: var(--accent);
  color: #fff;
  font: inherit;
  font-size: 14px;
  font-weight: 500;
  cursor: pointer;
  transition: background-color 140ms var(--ease), border-color 140ms var(--ease),
              color 140ms var(--ease);
}
button:hover { background: var(--accent-hover); border-color: var(--accent-hover); }
button:disabled { background: var(--raised); border-color: var(--line); color: var(--ink-3);
                  cursor: not-allowed; }
button.secondary { background: var(--surface); border-color: var(--line-strong);
                  color: var(--ink); }
button.secondary:hover { background: var(--raised); }
form button { margin-top: 12px; }
td form { margin: 0; }
td button { margin-top: 0; }
button.linklike {
  height: auto;
  padding: 0;
  border: 0;
  border-radius: 4px;
  background: none;
  color: var(--accent);
  font-weight: 400;
}
button.linklike:hover { background: none; color: var(--accent-hover); text-decoration: underline; }

.issue-card {
  display: grid;
  grid-template-columns: 16px minmax(0, 1fr);
  column-gap: 12px;
  row-gap: 4px;
  margin: 8px 0;
  padding: 16px 20px;
  background: var(--surface);
  border: 1px solid var(--line);
  border-radius: 12px;
}
.issue-card > input { grid-row: 1; grid-column: 1; margin: 4px 0 0; }
.issue-card > h3, .issue-card > p, .issue-card > a { grid-column: 2; }
.issue-card h3 { margin: 0; font-size: 14px; }

.alert { margin: 16px 0; padding: 12px 16px; border: 1px solid; border-radius: 8px;
         font-size: 13px; }
.alert-ok { background: #ecf7f0; border-color: #cfe9d8; color: #14532d; }
.alert-err { background: #fdeceb; border-color: #f7d4d1; color: #7f1d1d; }
.batch-banner { margin: 16px 0; padding: 12px 16px; background: #ecf7f0; border: 1px solid #cfe9d8;
                border-radius: 8px; color: #14532d; font-size: 13px; }
.batch-banner.warn { background: #fdf6e3; border-color: #f3e4bd; color: #78350f; }
.batch-banner ul { margin: 8px 0 0; padding-left: 20px; }
.batch-banner li { font-size: 12px; }

.delta { font-variant-numeric: tabular-nums; }
.delta.pos, .delta-up { color: var(--pos); }
.delta.neg, .delta-down { color: var(--neg); }
.delta.flat { color: var(--ink-3); }

.diff-del { background: #fdeceb; white-space: pre-wrap; }
.diff-add { background: #ecf7f0; white-space: pre-wrap; }
/* The box hugs the value's own leading/trailing blanks, so a whitespace repair is visible. */
.diff-del code, .diff-add code { background: rgba(255, 255, 255, .8); white-space: pre-wrap; }
.diff-side { color: var(--ink-3); white-space: nowrap; }
.examples ul { margin: 4px 0 12px; padding-left: 20px; }
.examples li { font-size: 13px; }

pre { margin: 8px 0; padding: 12px; background: var(--surface); border: 1px solid var(--line);
      border-radius: 8px; overflow-x: auto; font-family: var(--mono); font-size: 12px; }
code { font-family: var(--mono); font-variant-numeric: tabular-nums; }

.dim-strip { display: flex; gap: 2px; max-width: 96px; margin-top: 6px; }
.dim-strip span { height: 4px; flex: 1 1 0; border-radius: 2px; background: var(--line-strong); }
.dim-strip span.weak { background: var(--accent); }

.chart-label { margin: 24px 0 0; font-family: var(--mono); font-size: 11px; color: var(--ink-3); }
.trend-spark, .dim-lines { display: block; max-width: 100%; height: auto; margin: 4px 0 0; }
.trend-spark polyline { fill: none; stroke: var(--accent); stroke-width: 2; }
.trend-spark circle { fill: var(--accent); }
.trend-spark .grid, .dim-lines .grid { stroke: var(--line-strong); stroke-width: 1; }
.trend-spark text, .dim-lines text { fill: var(--ink-3); font-family: var(--mono);
                                     font-size: 11px; }
.dim-lines { margin-top: 8px; }
.dim-legend { display: flex; flex-wrap: wrap; gap: 4px 16px; margin: 8px 0 0;
              font-family: var(--mono); font-size: 11px; color: var(--ink-2); }
.dim-legend i { display: inline-block; width: 14px; margin-right: 6px;
                border-top: 2px solid currentColor; vertical-align: middle; }

footer { margin-top: 56px; padding-top: 16px; border-top: 1px solid var(--line);
         font-size: 12px; color: var(--ink-3); }

@media (max-width: 900px) {
  body { grid-template-columns: 1fr; }
  .sidebar { position: static; height: auto; padding: 12px 16px;
             border-right: 0; border-bottom: 1px solid var(--line); }
  .brand { padding: 0 0 12px; }
  .nav { display: flex; flex-wrap: wrap; gap: 2px; }
  .main { min-width: 0; overflow-x: auto; padding: 20px 16px 48px; }
  h1 { font-size: 24px; }
}
@media (prefers-reduced-motion: reduce) {
  * { transition: none !important; }
}
"""


def _severity_badge(severity: str) -> str:
    return f'<span class="badge badge-{escape(severity.lower())}">{escape(severity)}</span>'


def _nav_link(href: str, label: str, name: str, active: str) -> str:
    current = ' aria-current="page"' if active == name else ""
    return f'<a href="{href}"{current}>{label}</a>'


def _page(title: str, body: str, *, active: str = "", lang: str = "en") -> str:
    """The shell every page shares: a quiet sidebar, a fluid main column.

    `active` was passed by four callers and never read, so no page said where you were; it now
    drives `aria-current`, which both the CSS and screen readers use.
    """
    nav = "".join(
        [
            _nav_link("/ui/", escape(t(lang, "ui.nav_home")), "home", active),
            _nav_link("/ui/scans", escape(t(lang, "ui.nav_scans")), "scans", active),
            _nav_link("/ui/trends", escape(t(lang, "ui.nav_trends")), "trends", active),
            _nav_link("/ui/repairs", escape(t(lang, "ui.nav_repairs")), "repairs", active),
            _nav_link("/ui/pii", escape(t(lang, "ui.nav_pii")), "pii", active),
            _nav_link("/api/docs", escape(t(lang, "ui.nav_api_docs")), "api", active),
        ]
    )
    return "\n".join(
        [
            "<!DOCTYPE html>",
            f'<html lang="{t(lang, "html.lang")}"><head><meta charset="utf-8">'
            '<link rel="icon" href="data:,">',
            f"<title>{escape(title)} · DataSentry</title>",
            f"<style>{_CSS}</style></head><body>",
            '<aside class="sidebar">'
            '<div class="brand"><span class="rule"></span>DataSentry</div>'
            f'<nav class="nav">{nav}</nav>'
            "</aside>",
            '<main class="main">',
            f"<h1>{escape(title)}</h1>",
            body,
            f"<footer>{escape(t(lang, 'ui.footer'))}</footer>",
            "</main>",
            "</body></html>",
        ]
    )


_ACCENT = "#1d4ed8"
# Six dimensions genuinely have to be told apart, so they keep distinct hues, but at the weight of
# a technical drawing rather than a default chart palette. The dimension with the lowest mean takes
# the accent, because that is the one pulling the score down.
_DIM_TONES = ("#0f766e", "#a16207", "#52525b", "#8a5a3c", "#3f6212", "#5b6b7c")


def _dim_strip(scan: ScanRun) -> str:
    """Six dimension cells beside the score: the weakest carries the accent, the rest stay quiet.

    The strip used to draw one rainbow segment per dimension sized by its own value, which summed
    past the box and read as a colour bar. Equal cells plus one marked cell answer the only
    question the column asks: which dimension is holding the score down.
    """
    if not scan.quality_score or not scan.quality_score.dimensions:
        return ""
    scored = {
        dim: value
        for dim, value in sorted(scan.quality_score.dimensions.items())
        if value is not None
    }
    if not scored:
        return ""
    weakest = min(scored, key=lambda dim: scored[dim])
    cells = []
    for dim, value in scored.items():
        cls = ' class="weak"' if dim == weakest else ""
        cells.append(f'<span{cls} title="{escape(dim)} {value:.1f}"></span>')
    return f'<div class="dim-strip">{"".join(cells)}</div>'


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
    title: str | None = None,
) -> str:
    scan_label = escape(t(lang, "ui.scan_button"))
    body = [
        f"<h2>{escape(t(lang, 'ui.workspace_overview'))}</h2>",
        _batch_banner(batch, lang=lang),
        _scan_table(scans, lang=lang),
        f"<h2>{escape(t(lang, 'ui.new_scan'))}</h2>",
        '<form method="post" action="/ui/scans" id="scan-form" class="panel">'
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
    # UI-01：/ui/scans 曾与 /ui/ 同标题同正文；列表路由现传独立标题。
    return _page(title or t(lang, "ui.home_title"), "\n".join(body), active="home", lang=lang)


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
    # UI-03：flat 徽标曾是无背景的白字（对比度 ~1.0 不可读），现给深灰底。
    return f'<span class="badge badge-flat">{escape(t(lang, "ui.direction_flat"))}</span>'


def _domain(values: list[float]) -> tuple[float, float]:
    """A readable y-range: padded, but never so wide that a real change flattens into a line."""
    low, high = min(values), max(values)
    if high - low < 4:
        mid = (high + low) / 2
        return mid - 2, mid + 2
    pad = (high - low) * 0.15
    return low - pad, high + pad


def _sparkline(scores: list[float]) -> str:
    """The overall score over time: the one chart on a trend section that carries the accent."""
    if len(scores) < 2:
        return ""
    width, height, left, right, top, bottom = 800, 148, 40, 12, 12, 24
    low, high = _domain(scores)
    span = high - low
    plot_w = width - left - right
    plot_h = height - top - bottom
    step = plot_w / (len(scores) - 1)
    points: list[tuple[float, float]] = []
    for i, score in enumerate(scores):
        x = left + i * step
        y = top + plot_h - ((score - low) / span) * plot_h
        points.append((x, y))
    grid = []
    for frac in (0.0, 0.5, 1.0):
        y = top + plot_h - frac * plot_h
        value = low + frac * span
        grid.append(
            f'<line class="grid" x1="{left}" y1="{y:.1f}" '
            f'x2="{width - right}" y2="{y:.1f}"/>'
            f'<text x="{left - 8}" y="{y + 4:.1f}" text-anchor="end">{value:.0f}</text>'
        )
    polyline = f'<polyline points="{" ".join(f"{x:.1f},{y:.1f}" for x, y in points)}"/>'
    dots = "".join(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="2.5"/>' for x, y in points)
    label = " → ".join(f"{score:.1f}" for score in scores)
    return (
        f'<svg class="trend-spark" width="{width}" height="{height}" '
        f'viewBox="0 0 {width} {height}" role="img" aria-label="score trend: {label}">'
        + "".join(grid)
        + polyline
        + dots
        + "</svg>"
    )


def _dimension_lines(points: list[ScanPoint]) -> str:
    """六维质量折线（内联 SVG 零依赖）：每个维度一条线，y 轴按数据留白。

    六条线必须能相互区分，所以保留不同色相，但压到技术图的轻重；均值最低的维度取强调色，
    因为它是拖低总分的那一条。旧版把 0-100 当固定域，六条线全挤在顶部一厘米内。
    """
    dims: set[str] = set()
    for point in points:
        if point.dimensions:
            dims.update(point.dimensions)
    if not dims or not any(point.dimensions for point in points):
        return ""
    dims_sorted = sorted(dims)
    if len(points) < 2:
        return ""

    series: dict[str, list[float]] = {dim: [] for dim in dims_sorted}
    for point in points:
        for dim in dims_sorted:
            value = (point.dimensions or {}).get(dim)
            if value is not None:
                series[dim].append(min(max(value, 0.0), 100.0))
    if not any(series.values()):
        return ""
    means = {
        dim: (sum(values) / len(values) if values else 100.0) for dim, values in series.items()
    }
    weakest = min(means, key=lambda dim: means[dim])

    tones: dict[str, str] = {}
    spare = iter(_DIM_TONES)
    for dim in dims_sorted:
        tones[dim] = _ACCENT if dim == weakest else next(spare, "#52525b")

    width, height, left, right, top, bottom = 800, 188, 40, 12, 12, 24
    every = [value for values in series.values() for value in values]
    low, high = _domain(every)
    span = high - low
    plot_w = width - left - right
    plot_h = height - top - bottom
    step = plot_w / (len(points) - 1)

    grid = []
    for frac in (0.0, 0.5, 1.0):
        y = top + plot_h - frac * plot_h
        value = low + frac * span
        grid.append(
            f'<line class="grid" x1="{left}" y1="{y:.1f}" '
            f'x2="{width - right}" y2="{y:.1f}"/>'
            f'<text x="{left - 8}" y="{y + 4:.1f}" text-anchor="end">{value:.0f}</text>'
        )

    lines: list[str] = []
    drawn: list[str] = []
    for dim in dims_sorted:
        coords: list[str] = []
        for i, point in enumerate(points):
            value = (point.dimensions or {}).get(dim)
            if value is None:
                continue
            clamped = min(max(value, 0.0), 100.0)
            x = left + i * step
            y = top + plot_h - ((clamped - low) / span) * plot_h
            coords.append(f"{x:.1f},{y:.1f}")
        if len(coords) < 2:
            continue
        drawn.append(dim)
        weight = 2 if dim == weakest else 1.5
        lines.append(
            f'<polyline points="{" ".join(coords)}" fill="none" '
            f'stroke="{tones[dim]}" stroke-width="{weight}"/>'
        )
    if not lines:
        return ""

    legend = "".join(
        f'<span style="color:{tones[dim]}"><i></i>{escape(dim)}</span>' for dim in drawn
    )
    return (
        f'<svg class="dim-lines" width="{width}" height="{height}" '
        f'viewBox="0 0 {width} {height}" role="img" aria-label="quality dimensions over time">'
        + "".join(grid)
        + "".join(lines)
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


def _issues_delta_cell(point: ScanPoint, previous: ScanPoint | None) -> str:
    """V51：issue 数 Δ 列（并列展示漂移信号，行级轻量面）。"""
    if previous is None:
        return '<td class="meta">—</td>'
    delta = point.issues_total - previous.issues_total
    if delta > 0:
        return f'<td class="delta-down">+{delta}</td>'
    if delta < 0:
        return f'<td class="delta-up">{delta}</td>'
    return '<td class="meta">0</td>'


def _compare_link(points: list[ScanPoint], lang: str) -> str:
    """V51：最新两 run 的漂移并列入口（compare 页深链）。"""
    if len(points) < 2:
        return ""
    prev_id, latest_id = points[-2].run_id, points[-1].run_id
    href = f"/ui/compare?runs={prev_id}&runs={latest_id}"
    return (
        '<p class="meta"><a class="badge badge-info" href="'
        f'{href}">{escape(t(lang, "ui.drift_compare_link"))}</a>'
        f" · {escape(prev_id)} → {escape(latest_id)}</p>"
    )


def render_trends(trends: list[DatasetTrend], *, lang: str = "en") -> str:
    """跨扫描趋势页（Step 45，18.2 V1）。trends 来自 trends.build_trends。"""
    if not trends:
        body = [f'<p class="meta">{escape(t(lang, "ui.no_trend_data"))}</p>']
        return _page(t(lang, "ui.trends_title"), "\n".join(body), active="trends", lang=lang)
    sections: list[str] = []
    for trend in trends:
        points = trend.points
        rows = []
        for index, point in enumerate(points):
            prior = points[index - 1] if index else None
            rows.append(
                "<tr>"
                f'<td><a href="/ui/scans/{escape(point.run_id)}">{escape(point.run_id)}</a></td>'
                f"<td>{point.finished_at:%Y-%m-%d %H:%M}</td>"
                f'<td class="priority">{point.score:.1f}</td>'
                + _delta_cell(point, prior)
                + f"<td>{point.issues_total}</td>"
                + _issues_delta_cell(point, prior)
                + "</tr>"
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
            + _compare_link(points, lang)
            + f'<p class="chart-label">{escape(t(lang, "ui.score"))}</p>'
            + _sparkline([p.score for p in points])
            + f'<p class="chart-label">{escape(t(lang, "ui.chart_dimensions"))}</p>'
            + _dimension_lines(points)
            + _dimension_table(points, lang=lang)
            + "<table><tr>"
            f"<th>{escape(t(lang, 'ui.run_id'))}</th><th>{escape(t(lang, 'ui.finished'))}</th>"
            f"<th>{escape(t(lang, 'ui.score'))}</th>"
            f"<th>{escape(t(lang, 'ui.delta'))}</th>"
            f"<th>{escape(t(lang, 'ui.issues_title'))}</th>"
            f"<th>{escape(t(lang, 'ui.issues_delta'))}</th>"
            f"</tr>{''.join(rows)}</table>"
            "</section>"
        )
    return _page(t(lang, "ui.trends_title"), "\n".join(sections), active="trends", lang=lang)


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
            badge = f'<td><span class="badge badge-ok">{escape(t(lang, "ui.proposed"))}</span></td>'
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
            badge = (
                f'<td><span class="badge badge-critical">{escape(t(lang, "ui.error"))}</span></td>'
            )
            cells = f'<td colspan="4" class="meta">{escape(err)}</td>'
            check = "<td></td>"
        else:
            badge = f'<td><span class="badge">{escape(t(lang, "ui.unsupported"))}</span></td>'
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
        '<form method="post" class="panel" '
        f'action="/ui/scans/{escape(run_id)}/repairs/batch-apply" '
        'id="batch-apply-form">'
        f'<input type="hidden" name="source_path" value="{escape(source_path)}">'
        f'<p class="meta">{escape(t(lang, "ui.batch_apply_note"))}</p>'
        "<table><tr><th>"
        '<input type="checkbox" id="select-all" aria-label="select all">'
        "</th><th>Issue</th><th>Title</th><th>Status</th><th>Operation</th>"
        "<th>Columns</th><th>Risk</th><th>Rows</th><th></th></tr>" + "".join(rows) + "</table>"
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


def _ops_summary(run: RepairRun) -> str:
    return ", ".join(sorted({str(op.operation) for op in run.operations})) or "—"


def render_batch_rollback(
    runs: list[RepairRun],
    errors: dict[str, str],
    *,
    lang: str = "en",
) -> str:
    """V48：批量回滚结果页——含失败原因细分（快照缺失/未找到等）。"""
    rows = []
    seen = set()
    for run in runs:
        seen.add(run.id)
        err = errors.get(run.id)
        if err is None:
            status_cell = (
                f'<td><span class="badge">'
                f"{escape(t(lang, 'ui.repair_status_rolled_back'))}</span></td>"
                "<td>—</td>"
            )
        else:
            status_cell = (
                f'<td><span class="badge badge-critical">{escape(t(lang, "ui.error"))}</span></td>'
                f'<td colspan="2" class="meta">{escape(err)}</td>'
            )
        rows.append(
            "<tr>"
            f"<td>{escape(run.id)}</td>"
            f"<td>{escape(run.dataset_id)}</td>"
            f"<td>{escape(_ops_summary(run))}</td>" + status_cell + "</tr>"
        )
    for run_id in errors:
        if run_id in seen:
            continue
        rows.append(
            "<tr>"
            f"<td>{escape(run_id)}</td>"
            "<td>—</td>"
            "<td>—</td>"
            f'<td><span class="badge badge-critical">{escape(t(lang, "ui.error"))}</span></td>'
            f'<td colspan="2" class="meta">{escape(errors[run_id])}</td>'
            "</tr>"
        )
    body = [
        f'<p class="meta">{escape(t(lang, "ui.batch_rollback_summary"))}: '
        f"{len(runs) - len(errors)} / {len(runs)}</p>",
        '<p class="meta">'
        f'<a href="/ui/repairs">{escape(t(lang, "ui.repairs_title"))}</a>'
        f" · <a href='/ui/scans'>{escape(t(lang, 'ui.nav_scans'))}</a></p>",
        "<table><tr><th>Run id</th><th>Dataset</th><th>Operation</th><th>Status</th>"
        "<th>Rows</th></tr>" + "".join(rows) + "</table>",
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
                    f'<td><span class="badge badge-ok">'
                    f"{escape(t(lang, 'ui.repair_status_applied'))}</span></td>"
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
                    f'<td><span class="badge">'
                    f"{escape(t(lang, 'ui.repair_status_rolled_back'))}</span></td>"
                    "<td></td><td></td>"
                )
            else:
                failed_badge = escape(t(lang, "ui.repair_status_failed"))
                status_cell = (
                    f'<td><span class="badge badge-critical">{failed_badge}</span></td>'
                    "<td></td><td></td>"
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
            "<table><tr><th>Run id</th><th>Dataset</th><th>Operation</th><th>Rows</th>"
            "<th>Status</th><th></th><th></th><th>Created</th></tr>" + "".join(rows) + "</table>"
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
        '<table class="kv">'
        f"<tr><th>Run id</th><td>{escape(run.id)}</td></tr>"
        f"<tr><th>Dataset</th><td>{escape(run.dataset_id)}</td></tr>"
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
        body = _diff_table(columns, before_rows, after_rows, changed_indices, lang=lang)
    return _page(t(lang, "ui.artifact_title"), head + actions + body, active="repairs", lang=lang)


def _diff_table(
    columns: list[str],
    before_rows: list[list[object]],
    after_rows: list[list[object]],
    changed_indices: list[int],
    *,
    lang: str = "en",
) -> str:
    """The changed rows, before and after, as one table. Shared by the artifact page and the
    workbench's apply response so the two cannot render the evidence differently."""
    thead = "".join(f"<th>{escape(c)}</th>" for c in columns)
    diff_rows = []
    width = len(columns)

    def at(index: int, rows: list[list[object]]) -> list[object]:
        # A row present on one side only must still render every column, or the row shows up with
        # an empty partner and nothing highlighted.
        values = rows[index] if index < len(rows) else []
        return [*values, *([None] * (width - len(values)))]

    def cell(value: object, cls: str | None) -> str:
        text = escape(str(value)) if value is not None else "∅"
        if cls is None:
            return f"<td>{text}</td>"
        # `<code>` with pre-wrap: HTML collapses exactly the blanks a whitespace repair removes.
        return f'<td class="{cls}"><code>{text}</code></td>'

    for i in changed_indices:
        before = at(i, before_rows)
        after = at(i, after_rows)
        line_no = i + 2
        differs = changed_cells(before, after)
        before_cells = "".join(
            cell(v, "diff-del" if i2 in differs else None) for i2, v in enumerate(before)
        )
        after_cells = "".join(
            cell(v, "diff-add" if i2 in differs else None) for i2, v in enumerate(after)
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
    return f"<table><tr><th></th><th></th>{thead}</tr>" + "".join(diff_rows) + "</table>"


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
                f'<td><span class="badge badge-ok">{escape(t(lang, "ui.applied"))}</span></td>'
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
                f'<td><span class="badge badge-critical">{escape(t(lang, "ui.error"))}</span></td>'
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
        f'<div class="stat"><span class="k">{escape(t(lang, "ui.applied"))}</span>'
        f'<span class="v">{applied_n}</span></div>'
        f'<div class="stat"><span class="k">{escape(t(lang, "ui.skipped"))}</span>'
        f'<span class="v">{skipped_n}</span></div>'
        f'<div class="stat"><span class="k">{escape(t(lang, "ui.error"))}</span>'
        f'<span class="v">{failed_n}</span></div>'
        "</div>"
        f'<p class="meta">{escape(t(lang, "ui.batch_apply_summary"))}: '
        f"{len(issues)} issues · {escape(source_path)}</p>",
        '<p class="meta">'
        f'<a href="/ui/scans/{escape(run_id)}">{escape(t(lang, "ui.back_to_scan"))}</a></p>',
        '<form method="post" class="panel" '
        f'action="/ui/scans/{escape(run_id)}/repairs/batch-rollback" '
        'id="batch-rollback-form">'
        f'<p class="meta">{escape(t(lang, "ui.batch_rollback_note"))}</p>'
        "<table><tr><th></th><th>Issue</th><th>Title</th><th>Status</th><th>Run id</th>"
        "<th>Operation</th><th>Rows</th><th></th></tr>" + "".join(rows) + "</table>"
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
            f'form="{BATCH_REPAIR_FORM_ID}" '
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
    known_sources: Sequence[str] = (),
) -> str:
    overall = (
        f"{scan.quality_score.overall:.1f}" if scan.quality_score else t(lang, "meta.not_scored")
    )
    dims = []
    if scan.quality_score:
        for name, value in scan.quality_score.dimensions.items():
            if value is not None:
                dims.append(f"<span>{escape(name)} <b>{value:.1f}</b></span>")
    body = [
        '<p class="meta">'
        f"{escape(scan.dataset_id)} · "
        f"{scan.fingerprint.row_count} rows × {scan.fingerprint.column_count} cols · "
        f"{escape(scan.status)}"
        + (
            ' · <span class="badge">sampled '
            f"{escape(str(scan.config.sampling.model_dump(mode='json')))}</span>"
            if _sampling_enabled(scan.config)
            else ""
        )
        + "</p>",
        f'<div class="score"><span class="fig">{overall}</span><span class="of">/ 100</span></div>',
        '<p class="dims">' + "".join(dims) + "</p>",
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
        '<form method="post" class="panel" '
        f'action="/ui/scans/{escape(scan.id)}/repairs/batch-propose" '
        f'id="{BATCH_REPAIR_FORM_ID}">'
        + _source_path_field(
            known_sources,
            scan.source_path,
            html_id="batch-source-path",
            label=t(lang, "ui.source_registered"),
            lang=lang,
        )
        + '<button type="submit" id="batch-propose-btn" disabled>'
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
    return _page(f"Scan {scan.id}", "\n".join(body), active="scans", lang=lang)


def _source_path_field(
    known_sources: Sequence[str],
    current: str | None,
    *,
    html_id: str,
    label: str,
    lang: str,
) -> str:
    """A picker over registered sources, plus the copy-only promise the repair face owes the user.

    The select is not the security boundary -- a crafted POST can still name any path, and
    `resolve_allowed_scan_path` is what refuses it (row r5). This exists because a free-text box
    invited a typo against the server's path space and never said where the bytes would go (UI-04).
    """
    options: list[str] = []
    for candidate in [*known_sources, current or ""]:
        if candidate and candidate not in options:
            options.append(candidate)
    if not options:
        # Not `disabled`: a disabled control is barred from constraint validation and never
        # submitted, so `required` on it does nothing and the POST arrives without `source_path` at
        # all -- which FastAPI answers with bare JSON 422, the same UI-06 dead end as an unhandled
        # exception. Left enabled and empty, the browser blocks the submit with a message at the
        # control and the note below says why (review B-6).
        return (
            f'<label for="{html_id}">{escape(label)}</label>'
            f'<select id="{html_id}" name="source_path" required></select>'
            f'<p class="meta">{escape(t(lang, "ui.no_registered_sources"))}</p>'
        )
    tags = "".join(
        f'<option value="{escape(path)}"'
        + (" selected" if path == (current or options[0]) else "")
        + f">{escape(path)}</option>"
        for path in options
    )
    return (
        f'<label for="{html_id}">{escape(label)}</label>'
        f'<select id="{html_id}" name="source_path" required>{tags}</select>'
        f'<p class="meta">{escape(t(lang, "ui.repair_copy_note"))}</p>'
    )


def render_workbench(
    issue: Issue,
    *,
    run_id: str = "",
    source_path: str | None = None,
    proposal: RepairProposal | None = None,
    preview: RepairPreview | None = None,
    run: RepairRun | None = None,
    diff: tuple[list[str], list[list[object]], list[list[object]], list[int]] | None = None,
    error: str | None = None,
    lang: str = "en",
    known_sources: Sequence[str] = (),
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
        if diff is None:
            body.append(f'<p class="meta">{escape(t(lang, "ui.artifact_unavailable"))}</p>')
        elif not diff[3]:
            body.append(f'<p class="meta">{escape(t(lang, "ui.artifact_no_changes"))}</p>')
        else:
            columns, before_rows, after_rows, changed_indices = diff
            shown = changed_indices[:APPLY_DIFF_ROW_CAP]
            body.append(f"<h2>{escape(t(lang, 'ui.artifact_heading'))}</h2>")
            body.append(_diff_table(columns, before_rows, after_rows, shown, lang=lang))
            hidden = len(changed_indices) - len(shown)
            if hidden > 0:
                body.append(
                    f'<p class="meta">{escape(t(lang, "ui.artifact_more").format(n=hidden))}</p>'
                )
        body.append(
            f'<p><a href="/ui/repairs/{escape(run.id)}/artifact">'
            f"{escape(t(lang, 'ui.view_full_artifact'))}</a></p>"
        )
    body.append(
        f"<h2>{escape(t(lang, 'ui.workbench_title'))}</h2>"
        '<form method="post" class="panel">'
        + _source_path_field(
            known_sources,
            source_path,
            html_id="source_path",
            label=t(lang, "ui.source_file_path"),
            lang=lang,
        )
        + f'<button type="submit" name="action" value="propose">'
        f"{escape(t(lang, 'ui.propose_repair'))}</button>"
        f'<button class="secondary" type="submit" name="action" value="apply">'
        f"{escape(t(lang, 'ui.apply_repair'))}</button>"
        "</form>"
    )
    return _page(t(lang, "ui.workbench_title"), "\n".join(body), active="scans", lang=lang)


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
            f'<a href="/ui/repairs/{escape(run.id)}/artifact">{escape(run.id)}</a>'
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
            "<table><tr><th>Type</th><th>Columns</th>"
            f"<th>{escape(t(lang, 'ui.compare_reference_short'))}</th>"
            f"<th>{escape(t(lang, 'ui.compare_current_short'))}</th>"
            f"<th>{escape(t(lang, 'ui.delta'))}</th>"
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

    if (
        score_delta is not None
        and reference.quality_score
        and current.quality_score
        # The score is the headline of a comparison, so it gets a figure instead of a meta line.
        and score_delta
    ):
        tone = "pos" if score_delta > 0 else "neg"
    else:
        tone = "flat"
    score_line = (
        '<div class="score">'
        f'<span class="k">{escape(t(lang, "ui.score_delta"))}</span>'
        f'<span class="fig delta {tone}">'
        f"{'+' if (score_delta or 0) > 0 else ''}{score_delta:.1f}</span>"
        f'<span class="of">{reference.quality_score.overall:.1f} → '
        f"{current.quality_score.overall:.1f}</span>"
        "</div>"
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
        "<table><tr><th></th>"
        f"<th>{escape(t(lang, 'ui.compare_reference_short'))}</th>"
        f"<th>{escape(t(lang, 'ui.compare_current_short'))}</th>"
        f"<th>{escape(t(lang, 'ui.delta'))}</th></tr>" + "".join(severity_rows) + "</table>",
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
        "<table><tr><th>Column</th><th>Type</th><th>Metric</th><th>Value / threshold</th>"
        "<th>Direction</th><th></th></tr>"
        + (
            "".join(drift_rows)
            if drift_rows
            else "<tr><td colspan='6'>" + f"{escape(t(lang, 'ui.no_drifts'))}</td></tr>"
        )
        + "</table>",
        f"<h2>{escape(t(lang, 'ui.schema_changes'))}</h2>",
        "<table><tr><th>Type</th><th>Column</th><th>Before</th><th>After</th></tr>"
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


def render_error(
    title: str,
    message: str,
    *,
    lang: str = "en",
    back_href: str | None = None,
    back_label: str | None = None,
) -> str:
    """A failure page that still tells the user where to go next.

    `back_href` defaults to Home; a caller that refused an action the user took *from* a specific
    page passes that page, so the way out is the way back in rather than a reload of the root.
    """
    href = back_href or "/ui/"
    label = back_label or t(lang, "ui.back_home")
    return _page(
        title,
        f'<div class="alert alert-err">{escape(message)}</div>'
        f'<p><a href="{escape(href)}">{escape(label)}</a></p>',
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
    return _page(t(lang, "ui.pii_title"), "\n".join(body), active="pii", lang=lang)
