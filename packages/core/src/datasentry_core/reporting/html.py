"""HTML 报告渲染（26.1：自包含单文件，内嵌 CSS/JS，可离线打开）。

MVP 节（26 章清单可填充部分）：Executive Summary / Dataset Overview /
Quality Score（含 27.3 维度条形分解与「哪些 Issue 扣分」悬停）/ Issue Breakdown /
Critical Findings / Column Profiles / Methodology / Reproducibility。
Drift / Suggested Rules / Repair History 归 V1。
Step 49（V2-B，ADR-049）：Issue Breakdown 升级为交互表格（severity/维度筛选、
列排序、详情折叠、分页，原生 JS 零依赖内联）；可选 Quality Trends 迷你 SVG
（消费 trends.py 序列化数据）；server_base_url 非空时 issue 行联动修复工作台。
Step 60（V6，ADR-060）：报告内部联动与导航 —— 评分条维度条可点击（联动维度筛选
并滚动定位）、Critical Findings 条目可点击（定位并高亮对应问题行）、粘性章节导航
（scrollspy 高亮当前章节）+ 回到顶部；联动脚本同样原生 JS 内联零依赖，事件委托
保证脚本顺序无关；契约：`data-dim-link` / `.finding-link[data-issue-id]` /
`#report-nav` / `#back-to-top` / `#issues._render`。
Step 61（V6，ADR-061）：Column Profiles 交互节 —— `render_html(profiles=...)`
消费 `DatasetProfile.model_dump(mode="json")`（扫描期画像 sidecar，见
client.py），可排序画像表 + 迷你空值条 + 语义/PII 徽标 + top 类别 chips，
渲染期 PII 掩码；节可选，无画像时不渲染、导航不含其锚点。
Step 63（V6，ADR-063）：深色模式 —— 全 CSS 色板改为 CSS 自定义属性
（:root 亮色默认 + `@media (prefers-color-scheme: dark)` 暗色覆盖 +
`@media print` 强制亮色防打印白字），趋势 SVG 改走 `.trend-line` /
`.trend-dot` 类（`var(--accent)`）；评分条六色段为双主题可读的中间饱和
色，保持硬编码。
Step 64（V6，ADR-064）：报告间对比 —— `render_html(comparison=...)`
消费 `build_comparison`（app 侧纯函数，同数据集历史 run 评分/维度/issue
计数/Δ），静态对比表（当前 run 高亮、Δ 按符号上色、按严重度列动态、
维度列动态）；节可选，对比数据不足 2 run 时不渲染、导航不含其锚点。
"""

from __future__ import annotations

from html import escape
from typing import Any

from datasentry_core.reporting import HTML_SECTIONS, Report, critical_findings, mask_text_pii
from datasentry_core.reporting.column_profiles import render_column_profiles
from datasentry_core.reporting.i18n import t
from datasentry_core.reporting.interactive import render_interactive_issue_table, render_trend_svg

_CSS = """
:root {
  color-scheme: light;
  --fg: #1f2328;
  --fg-muted: #57606a;
  --fg-subtle: #8c959f;
  --accent: #0969da;
  --border: #d0d7de;
  --surface: #f6f8fa;
  --surface-strong: #fff;
  --surface-nav: rgba(255,255,255,.95);
  --on-accent: #fff;
  --critical: #cf222e;
  --high: #bc4c00;
  --medium: #9a6700;
  --ok: #1a7f37;
  --highlight: #fff8c5;
  --semantic: #8250df;
}
@media (prefers-color-scheme: dark) {
  :root {
    color-scheme: dark;
    --fg: #e6edf3;
    --fg-muted: #8c959f;
    --fg-subtle: #6e7681;
    --accent: #58a6ff;
    --border: #30363d;
    --surface: #21262d;
    --surface-strong: #161b22;
    --surface-nav: rgba(22,27,34,.92);
    --on-accent: #fff;
    --critical: #f85149;
    --high: #d29922;
    --medium: #bb8009;
    --ok: #3fb950;
    --highlight: #3d3320;
    --semantic: #a371f7;
  }
}
@media print {
  :root {
    color-scheme: light;
    --fg: #1f2328;
    --fg-muted: #57606a;
    --fg-subtle: #8c959f;
    --accent: #0969da;
    --border: #d0d7de;
    --surface: #f6f8fa;
    --surface-strong: #fff;
    --surface-nav: rgba(255,255,255,.95);
    --on-accent: #fff;
    --critical: #cf222e;
    --high: #bc4c00;
    --medium: #9a6700;
    --ok: #1a7f37;
    --highlight: #fff8c5;
    --semantic: #8250df;
  }
}
body { font-family: -apple-system, "Segoe UI", Roboto, sans-serif; margin: 2rem auto;
       max-width: 960px; color: var(--fg); line-height: 1.5; }
h1 { border-bottom: 2px solid var(--accent); padding-bottom: .3rem; }
h2 { margin-top: 2rem; border-bottom: 1px solid var(--border); padding-bottom: .2rem; }
table { border-collapse: collapse; width: 100%; margin: .5rem 0; }
th, td { border: 1px solid var(--border); padding: .35rem .6rem; text-align: left;
       font-size: .9rem; }
th { background: var(--surface); }
.badge-critical { color: var(--critical); font-weight: 600; }
.badge-high { color: var(--high); font-weight: 600; }
.badge-medium { color: var(--medium); font-weight: 600; }
.badge-low, .badge-info { color: var(--fg-muted); }
.score-bar { display: flex; width: 100%; height: 1.4rem; border-radius: .4rem;
             overflow: hidden; border: 1px solid var(--border); }
.score-bar section { color: var(--on-accent); font-size: .7rem; text-align: center; }
footer { margin-top: 3rem; font-size: .8rem; color: var(--fg-muted);
         border-top: 1px solid var(--border); }
.notes { font-size: .85rem; color: var(--fg-muted); background: var(--surface); padding: .6rem;
         border-radius: .4rem; }
.issue-controls { display: flex; gap: .5rem; align-items: center; flex-wrap: wrap;
                  margin: .6rem 0; }
.issue-controls select, .issue-controls input[type=search] { padding: .25rem .45rem;
                  font-size: .85rem; }
.issue-row td { cursor: pointer; }
.issue-detail td { background: var(--surface); font-size: .85rem; }
.issue-detail.collapsed { display: none; }
th[data-key] { cursor: pointer; user-select: none; white-space: nowrap; }
th[data-key]::after { content: " \\21C5"; font-size: .7rem; color: var(--fg-subtle); }
th[data-key].sorted-asc::after { content: " \\2191"; }
th[data-key].sorted-desc::after { content: " \\2193"; }
.trend-block { margin: 1rem 0; }
.trend-svg { display: block; background: var(--surface-strong); border: 1px solid var(--border);
             border-radius: .4rem; padding: .3rem; }
.trend-line { stroke: var(--accent); }
.trend-dot { fill: var(--accent); }
.issue-pager { display: flex; gap: .6rem; align-items: center; margin: .6rem 0; }
.issue-pager button { background: var(--surface); color: var(--accent);
                      border: 1px solid var(--border); border-radius: .3rem;
                      padding: .2rem .7rem; cursor: pointer; }
.workbench-link { margin-left: .6rem; font-size: .8rem; }
html { scroll-behavior: smooth; }
h2 { scroll-margin-top: 3.2rem; }
.report-nav { position: sticky; top: 0; z-index: 10; display: flex; gap: .9rem;
              flex-wrap: wrap; align-items: center; background: var(--surface-nav);
              border-bottom: 1px solid var(--border); padding: .45rem .2rem; font-size: .85rem;
              margin-top: .6rem; }
.report-nav a { color: var(--accent); text-decoration: none; }
.report-nav a.active { font-weight: 600; text-decoration: underline; }
.score-bar section { cursor: pointer; }
.score-bar section:hover { filter: brightness(1.08); }
.score-bar section:focus-visible { outline: 2px solid var(--accent); outline-offset: 1px; }
.finding-link { color: inherit; text-decoration: none; }
.finding-link:hover { text-decoration: underline; }
tr.issue-row.highlight td { background: var(--highlight); }
.issue-controls button { background: var(--surface); color: var(--accent);
                         border: 1px solid var(--border); border-radius: .3rem;
                         padding: .2rem .55rem; font-size: .8rem; cursor: pointer; }
#back-to-top { position: fixed; right: 1.2rem; bottom: 1.2rem; display: none;
               background: var(--accent); color: var(--on-accent); border: 0; border-radius: .3rem;
               padding: .45rem .7rem; font-size: .8rem; cursor: pointer; z-index: 20; }
#back-to-top.show { display: block; }
.profiles-bar-track { display: inline-block; width: 64px; height: .6rem;
                      background: var(--surface); border: 1px solid var(--border);
                      border-radius: .3rem; vertical-align: middle; overflow: hidden; }
.profiles-bar { display: block; height: 100%; background: var(--critical); }
.chip { display: inline-block; background: var(--surface); border: 1px solid var(--border);
        border-radius: .8rem; padding: .05rem .45rem; font-size: .78rem;
        margin-right: .3rem; }
.badge-semantic { color: var(--semantic); font-weight: 600; }
.badge-pii { color: var(--critical); font-weight: 600; }
.cmp-up { color: var(--ok); font-weight: 600; }
.cmp-down { color: var(--critical); font-weight: 600; }
tr.cmp-current td { background: var(--surface); font-weight: 600; }
.cmp-badge { color: var(--accent); font-size: .75rem; margin-left: .4rem; }
"""

_BAR_COLORS = [
    "#0969da",
    "#1a7f37",
    "#bf8700",
    "#8250df",
    "#cf222e",
    "#57606a",
]

#: 内嵌联动脚本（Step 60，ADR-060）：评分条钻取 / 发现定位 / scrollspy / 回到顶部。
#: 全部用事件委托（document 级 click/keydown），与区块渲染顺序无关；
#: 聚焦依赖交互表导出 `#issues._render`（interactive.py），不存在时静默降级为锚点跳转。
_LINKAGE_JS = """(function () {
  "use strict";
  function byId(id) { return document.getElementById(id); }
  function jumpToDimension(dim) {
    var sel = byId("f-dimension");
    if (!sel) { return; }
    sel.value = dim;
    var ev = document.createEvent("Event");
    ev.initEvent("change", true, false);
    sel.dispatchEvent(ev);
    var target = byId("issue_breakdown");
    if (target) { target.scrollIntoView(); }
  }
  function focusIssue(id) {
    var issues = byId("issues");
    var tbody = byId("issue-tbody");
    if (!issues || !tbody || typeof issues._render !== "function") { return; }
    var sel = byId("f-severity"), dim = byId("f-dimension"), search = byId("f-search");
    if (sel) { sel.value = "all"; }
    if (dim) { dim.value = "all"; }
    if (search) {
      search.value = "";
      var ev = document.createEvent("Event");
      ev.initEvent("input", true, false);
      search.dispatchEvent(ev);
    }
    issues._render();
    var match = null;
    Array.prototype.forEach.call(tbody.querySelectorAll(".issue-row"), function (tr) {
      if (tr.getAttribute("data-issue-id") === id) { match = tr; }
    });
    if (!match) { return; }
    var detail = match.querySelector(".issue-detail");
    if (detail) { detail.classList.remove("collapsed"); }
    match.classList.add("highlight");
    match.scrollIntoView({block: "center"});
    window.setTimeout(function () { match.classList.remove("highlight"); }, 4000);
  }
  document.addEventListener("click", function (e) {
    var seg = e.target.closest("[data-dim-link]");
    if (seg) { jumpToDimension(seg.getAttribute("data-dim-link")); return; }
    var f = e.target.closest(".finding-link");
    if (f) { focusIssue(f.getAttribute("data-issue-id")); }
  });
  document.addEventListener("keydown", function (e) {
    var seg = e.target.closest("[data-dim-link]");
    if (seg && (e.key === "Enter" || e.key === " ")) {
      e.preventDefault();
      jumpToDimension(seg.getAttribute("data-dim-link"));
    }
  });
  var nav = byId("report-nav");
  var links = nav ? Array.prototype.slice.call(nav.querySelectorAll("a[href^='#']")) : [];
  var sections = [];
  function refreshSections() {
    sections = links.map(function (a) {
      return byId(a.getAttribute("href").slice(1));
    }).filter(function (el) { return el !== null; });
  }
  refreshSections();
  var topBtn = byId("back-to-top");
  document.addEventListener("scroll", function () {
    var y = window.scrollY + 96;
    var active = null;
    for (var i = sections.length - 1; i >= 0; i -= 1) {
      if (y >= sections[i].offsetTop) { active = sections[i]; break; }
    }
    links.forEach(function (a) { a.classList.remove("active"); });
    if (active) {
      var href = "#" + active.id;
      links.forEach(function (a) {
        if (a.getAttribute("href") === href) { a.classList.add("active"); }
      });
    }
    if (topBtn) { topBtn.classList.toggle("show", window.scrollY > 600); }
  });
  if (topBtn) {
    topBtn.addEventListener("click", function () {
      window.scrollTo({top: 0, behavior: "smooth"});
    });
  }
})();"""


def render_html(
    report: Report,
    *,
    trends: list[dict[str, Any]] | None = None,
    page_size: int = 25,
    server_base_url: str | None = None,
    profiles: dict[str, Any] | None = None,
    comparison: list[dict[str, Any]] | None = None,
    lang: str = "en",
) -> str:
    """26 章报告 → 自包含 HTML（内嵌 CSS/JS，无外部资源）。

    trends：trends.py `DatasetTrend.to_report_dict()` 列表 → Quality Trends 迷你 SVG；
    server_base_url：非空时 issue 行附带修复工作台链接（server 模式联动 REST API）；
    profiles：`DatasetProfile.model_dump(mode="json")` → Column Profiles 交互节
    （Step 61，ADR-061），缺省不渲染该节；
    comparison：`build_comparison` 输出（Step 64，ADR-064）→ 同数据集历史
    run 对比表，缺省/空不渲染；
    lang：框架文案语言（en/zh，V8，ADR-069），未知语言回退 en；
    检测器产出的 issue 标题/描述不译。
    """
    parts = [
        "<!DOCTYPE html>",
        f'<html lang="{t(lang, "html.lang")}"><head><meta charset="utf-8">',
        f"<title>{escape(t(lang, 'report.title'))}</title>",
        f"<style>{_CSS}</style></head><body>",
        f"<h1>{escape(t(lang, 'report.title'))}</h1>",
        _report_nav(
            include_profiles=bool(profiles and profiles.get("column_profiles")),
            include_comparison=bool(comparison),
            lang=lang,
        ),
        _meta(report),
        _executive_summary(report, lang=lang),
        _quality_score(report, lang=lang),
    ]
    if trends:
        parts.append(_trends_section(trends, lang=lang))
    parts.extend(
        [
            _issue_breakdown(
                report, page_size=page_size, server_base_url=server_base_url, lang=lang
            ),
            _critical_findings(report, lang=lang),
            _dataset_overview(report, lang=lang),
        ]
    )
    if profiles and profiles.get("column_profiles"):
        parts.append(_column_profiles(profiles, lang=lang))
    if comparison:
        parts.append(_comparison_section(comparison, lang=lang))
    parts.extend(
        [
            _methodology(lang=lang),
            _reproducibility(report, lang=lang),
            _footer(report, lang=lang),
            _back_to_top(lang=lang),
            f"<script>{_LINKAGE_JS}</script>",
            "</body></html>",
        ]
    )
    return "\n".join(parts)


def _column_profiles(profiles: dict[str, Any], *, lang: str = "en") -> str:
    heading = f'<h2 id="column_profiles">{escape(t(lang, "section.column_profiles"))}</h2>'
    return heading + render_column_profiles(profiles, lang=lang)


def _report_nav(
    *,
    include_profiles: bool = False,
    include_comparison: bool = False,
    lang: str = "en",
) -> str:
    """粘性章节导航（scrollspy 追踪当前章节，脚本见 _LINKAGE_JS）。"""
    sections = list(HTML_SECTIONS)
    if include_profiles:
        sections.append("column_profiles")
    if include_comparison:
        sections.append("comparison")
    links = "".join(f'<a href="#{s}">{escape(t(lang, f"section.{s}"))}</a>' for s in sections)
    return (
        f'<nav class="report-nav" id="report-nav" '
        f'aria-label="{escape(t(lang, "report.nav_aria"))}">'
        f"{links}</nav>"
    )


_SEVERITY_ORDER = ["critical", "high", "medium", "low", "info"]


def _comparison_section(comparison: list[dict[str, Any]], *, lang: str = "en") -> str:
    """同数据集多 run 对比表（Step 64）：静态表，当前 run 高亮，Δ 按符号上色。"""
    dimensions: list[str] = []
    for row in comparison:
        for dim in row.get("dimensions") or {}:
            if dim not in dimensions:
                dimensions.append(dim)
    present = {sev for row in comparison for sev in (row.get("issues") or {})}
    severities = [s for s in _SEVERITY_ORDER if s in present]
    heads = [
        t(lang, "comparison.run"),
        t(lang, "comparison.scanned_at"),
        t(lang, "comparison.overall"),
    ]
    heads += [f"{d.capitalize()} {t(lang, 'comparison.score_suffix')}" for d in dimensions]
    heads += [f"{s.capitalize()} {t(lang, 'comparison.issues_suffix')}" for s in severities]
    header = "".join(f"<th>{escape(h)}</th>" for h in heads)
    current_label = t(lang, "comparison.current")
    body = []
    for row in comparison:
        cells = []
        if row.get("current"):
            badge = f'<span class="cmp-badge">{escape(current_label)}</span>'
            run_cell = f"<code>{escape(row['run_id'])}</code>{badge}"
        else:
            run_cell = f"<code>{escape(row['run_id'])}</code>"
        cells.append(f"<td>{run_cell}</td>")
        cells.append(f"<td>{escape(str(row.get('finished_at') or ''))}</td>")
        overall = row.get("overall")
        overall_text = f"{overall:.1f}" if overall is not None else "n/a"
        delta = row.get("delta")
        if delta is None:
            cells.append(f"<td>{escape(overall_text)}</td>")
        elif delta > 0:
            cells.append(
                f'<td>{escape(overall_text)} <span class="cmp-up">(+{delta:.1f})</span></td>'
            )
        elif delta < 0:
            cells.append(
                f'<td>{escape(overall_text)} <span class="cmp-down">({delta:.1f})</span></td>'
            )
        else:
            cells.append(f'<td>{escape(overall_text)} <span class="meta">(0.0)</span></td>')
        for dim in dimensions:
            value = (row.get("dimensions") or {}).get(dim)
            cells.append(f"<td>{escape(f'{value:.1f}' if value is not None else '-')}</td>")
        for sev in severities:
            cells.append(f"<td>{int((row.get('issues') or {}).get(sev, 0))}</td>")
        cls = ' class="cmp-current"' if row.get("current") else ""
        body.append(f"<tr{cls}>{''.join(cells)}</tr>")
    return (
        f'<h2 id="comparison">{escape(t(lang, "section.comparison"))}</h2>'
        f"<table><thead><tr>{header}</tr></thead><tbody>{''.join(body)}</tbody></table>"
    )


def _back_to_top(*, lang: str = "en") -> str:
    return (
        f'<button id="back-to-top" type="button" '
        f'aria-label="{escape(t(lang, "report.back_to_top_aria"))}">'
        f"{escape(t(lang, 'report.back_to_top'))} &uarr;</button>"
    )


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


def _executive_summary(report: Report, *, lang: str = "en") -> str:
    scan = report["scan"]
    quality = report["quality"]
    overall = f"{quality['overall']:.1f}" if quality else t(lang, "meta.not_scored")
    return (
        f'<h2 id="executive_summary">{escape(t(lang, "section.executive_summary"))}</h2>'
        "<table>"
        f"<tr><th>{escape(t(lang, 'meta.overall'))}</th><td>{overall}</td></tr>"
        f"<tr><th>{escape(t(lang, 'meta.issues'))}</th><td>{len(report['issues'])}</td></tr>"
        f"<tr><th>{escape(t(lang, 'meta.detector_runs'))}</th>"
        f"<td>{len(report['detector_runs'])}</td></tr>"
        f"<tr><th>{escape(t(lang, 'meta.rows_cols'))}</th><td>{scan['fingerprint']['row_count']} / "
        f"{scan['fingerprint']['column_count']}</td></tr>"
        "</table>"
    )


def _quality_score(report: Report, *, lang: str = "en") -> str:
    quality = report["quality"]
    if quality is None:
        return (
            f'<h2 id="quality_score">{escape(t(lang, "section.quality_score"))}</h2>'
            f"<p>{escape(t(lang, 'meta.not_scored'))}</p>"
        )
    sections = []
    no_detector = t(lang, "meta.no_detector_ran")
    n_a = t(lang, "meta.n_a")
    for index, (dim, value) in enumerate(quality["dimensions"].items()):
        if value is None:
            sections.append(
                f'<section title="{escape(dim)}: {escape(no_detector)}">'
                f"{escape(dim)}: {escape(n_a)}</section>"
            )
            continue
        weight = quality["weights"].get(dim, 0.0)
        width = f"{weight * 100:.1f}%"
        color = _BAR_COLORS[index % len(_BAR_COLORS)]
        title = escape(_contributions_tooltip(quality, dim, lang=lang))
        sections.append(
            '<section class="score-dim" role="button" tabindex="0" '
            f'data-dim-link="{escape(dim)}" '
            f'style="width:{width};background:{color}" title="{title}" '
            f'aria-label="{escape(dim)}: {value:.1f}">'
            f"{escape(dim)} {value:.1f}</section>"
        )
    contributions = quality.get("dimension_contributions") or {}
    notes_rows = []
    if contributions:
        notes_rows.append(
            f"<p><strong>{escape(t(lang, 'meta.per_issue_deductions'))}</strong></p><ul>"
        )
        for dim, items in sorted(contributions.items()):
            for issue_id, impact in sorted(items.items()):
                notes_rows.append(
                    f"<li>{escape(dim)} / <code>{escape(issue_id)}</code>: {impact:.4f}</li>"
                )
        notes_rows.append("</ul>")
    return (
        f'<h2 id="quality_score">{escape(t(lang, "section.quality_score"))}</h2>'
        f"<p>{escape(t(lang, 'meta.overall'))}: <strong>{quality['overall']:.1f}</strong> "
        f"(score_version <code>{escape(quality['score_version'])}</code>)</p>"
        f'<div class="score-bar">{"".join(sections)}</div>'
        f'<p class="notes">{escape(quality["calculation_notes"])}</p>' + "".join(notes_rows)
    )


def _contributions_tooltip(quality: dict[str, Any], dim: str, *, lang: str = "en") -> str:
    contributions = quality.get("dimension_contributions") or {}
    items = contributions.get(dim, {})
    if not items:
        return f"{dim}: {t(lang, 'meta.no_deductions')}"
    return f"{dim}: " + "; ".join(f"{i}: {v:.4f}" for i, v in items.items())


def _trends_section(trends: list[dict[str, Any]], *, lang: str = "en") -> str:
    svgs = [svg for svg in (render_trend_svg(t, lang=lang) for t in trends) if svg]
    if not svgs:
        return ""
    return f'<h2 id="quality_trends">{escape(t(lang, "section.quality_trends"))}</h2>' + "".join(
        svgs
    )


def _issue_breakdown(
    report: Report,
    *,
    page_size: int = 25,
    server_base_url: str | None = None,
    lang: str = "en",
) -> str:
    return render_interactive_issue_table(
        report, page_size=page_size, server_base_url=server_base_url, lang=lang
    )


def _critical_findings(report: Report, *, lang: str = "en") -> str:
    findings = critical_findings(report)
    if not findings:
        return ""
    rows_affected = t(lang, "meta.rows_affected")
    priority = t(lang, "meta.priority")
    items = []
    for issue in findings:
        items.append(
            "<li>"
            f'<a class="finding-link" href="#issue_breakdown" '
            f'data-issue-id="{escape(issue["id"])}">'
            f'<span class="badge-{escape(issue["severity"])}">[{escape(issue["severity"])}]</span> '
            f"{escape(mask_text_pii(issue['title']))} &mdash; "
            f"{escape(priority)} {issue['priority_score']:.1f}, "
            f"{issue['affected_count']} {escape(rows_affected)}"
            "</a>"
            "</li>"
        )
    return (
        f'<h2 id="critical_findings">{escape(t(lang, "section.critical_findings"))}</h2>'
        f"<ul>{''.join(items)}</ul>"
    )


def _dataset_overview(report: Report, *, lang: str = "en") -> str:
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
        f'<h2 id="dataset_overview">{escape(t(lang, "section.dataset_overview"))}</h2>'
        f"<table>{''.join(rows)}</table>"
        f"<p><strong>{escape(t(lang, 'meta.columns'))}</strong></p><ul>{''.join(columns)}</ul>"
    )


def _methodology(*, lang: str = "en") -> str:
    return (
        f'<h2 id="methodology">{escape(t(lang, "section.methodology"))}</h2>'
        f"<p>{escape(t(lang, 'methodology.body'))}</p>"
    )


def _reproducibility(report: Report, *, lang: str = "en") -> str:
    scan = report["scan"]
    repro = scan["reproducibility"]
    return (
        f'<h2 id="reproducibility">{escape(t(lang, "section.reproducibility"))}</h2><ul>'
        f"<li>datasentry_version: <code>{escape(repro['datasentry_version'])}</code></li>"
        f"<li>detector_versions: <code>{escape(repr(repro['detector_versions']))}</code></li>"
        f"<li>seed: <code>{repro['seed']}</code></li>"
        f"<li>scanned_at: <code>{escape(repro['scanned_at'])}</code></li>"
        "</ul>"
    )


def _footer(report: Report, *, lang: str = "en") -> str:
    sections_label = t(lang, "report.footer_sections")
    return (
        "<footer>"
        f"{escape(t(lang, 'report.generated_by'))} "
        f"DataSentry {escape(report['datasentry_version'])} "
        f"&middot; report_schema_version {escape(report['report_schema_version'])} &middot; "
        f"{escape(sections_label)} "
        + ", ".join(f'<a href="#{s}">{escape(t(lang, f"section.{s}"))}</a>' for s in HTML_SECTIONS)
        + "</footer>"
    )
