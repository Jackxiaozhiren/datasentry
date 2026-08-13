"""HTML 报告交互层（Step 49，V2-B：severity/维度筛选、列排序、详情折叠、分页、迷你趋势图）。

Step 60（V6，ADR-060）：行级 `data-issue-id` 定位锚、全部展开/收起、联动出口
`#issues._render`（供 html.py 的内联联动脚本聚焦问题行 / 应用维度筛选）。

设计（ADR-049）：
- 交互逻辑在浏览器端：原生 JS 零依赖，全部内联（无外链、离线可用）；
- 同名 Python 纯函数（filter/sort/paginate/find_issue_by_id）提供可测语义，
  与 JS 行为一一对应，作为快照与单测的断言参照（验收：筛选/排序/分页均有测试覆盖）；
- 动态数据经 `json_script` 内嵌（`<`/`>`/`&` → `\\uXXXX`），杜绝 `</script>` 注入；
  JS 只以 `textContent` 写单元格，服务端数据已 PII 掩码（Step 48），双保险；
- 报告仍是审计产物：元数据/证据链保留，JS 仅视图增强；
- 迷你趋势图消费 `trends.py` 序列化后的结构（list[dict]），core 不反向依赖应用层。
"""

from __future__ import annotations

import json
import math
from html import escape
from typing import Any

from datasentry_core.reporting import Report, mask_text_pii
from datasentry_core.reporting.suggestions import suggest_repairs

SEVERITY_ORDER = ("critical", "high", "medium", "low", "info")
_SORT_KEYS = frozenset({"priority", "severity", "affected", "title"})
_SEVERITY_RANK = {s: i for i, s in enumerate(SEVERITY_ORDER)}

#: 内嵌原生 JS：与下方 Python 纯函数同语义（filter → sort → paginate → 渲染）。
#: 所有动态值经 textContent 写入；severity 样式类经白名单映射。
_INTERACTIVE_JS = """(function () {
  "use strict";
  var data = JSON.parse(document.getElementById("issue-data").textContent);
  var issues = data.issues || [];
  var pageSize = data.pageSize || 25;
  var runId = data.runId || "";
  var baseUrl = data.serverBaseUrl || null;
  var SEV = {critical: 0, high: 1, medium: 2, low: 3, info: 4};
  var SEV_CLASS = {
    critical: "badge-critical", high: "badge-high", medium: "badge-medium",
    low: "badge-low", info: "badge-info"
  };
  var state = {severity: "all", dimension: "all", search: "",
               key: "priority", reverse: true, page: 1};
  var tbody = document.getElementById("issue-tbody");
  var countEl = document.getElementById("issue-count");
  var pgInfo = document.getElementById("pg-info");
  var searchInput = document.getElementById("f-search");
  var headers = Array.prototype.slice.call(document.querySelectorAll("#issue-table th[data-key]"));

  function filtered() {
    var q = state.search;
    return issues.filter(function (r) {
      if (state.severity !== "all" && r.severity !== state.severity) { return false; }
      if (state.dimension !== "all" &&
          (r.dimensions || []).indexOf(state.dimension) < 0) { return false; }
      if (q) {
        var hay = (r.title + " " + (r.columns || []).join(" ") + " " +
                   (r.detectors || []).join(" ")).toLowerCase();
        if (hay.indexOf(q) < 0) { return false; }
      }
      return true;
    });
  }

  function cmp(a, b, key, reverse) {
    var ka, kb;
    if (key === "severity") {
      ka = SEV[a.severity] === undefined ? 9 : SEV[a.severity];
      kb = SEV[b.severity] === undefined ? 9 : SEV[b.severity];
    } else if (key === "priority") { ka = a.priority; kb = b.priority; }
    else if (key === "affected") { ka = a.affected; kb = b.affected; }
    else { ka = (a.title || "").toLowerCase(); kb = (b.title || "").toLowerCase(); }
    var d = ka < kb ? -1 : ka > kb ? 1 : 0;
    return reverse ? -d : d;
  }

  function cell(text, cls) {
    var td = document.createElement("td");
    if (cls) { td.className = cls; }
    td.textContent = text;
    return td;
  }

  function detailRow(r) {
    var tr = document.createElement("tr");
    tr.className = "issue-detail";
    var td = document.createElement("td");
    td.colSpan = 6;
    var lines = [];
    lines.push(r.description ? "Description: " + r.description : "No description.");
    lines.push("Confidence: " + r.confidence.toFixed(2) +
               " - false-positive risk: " + (r.falsePositiveRisk || "n/a"));
    lines.push("Affected rows: " + r.affected + " (" + (r.affectedRatio * 100).toFixed(2) + "%)");
    var ids = r.affectedRowIds || [];
    if (ids.length) { lines.push("Row ids (first 10): " + ids.slice(0, 10).join(", ")); }
    var sugs = r.suggestions || [];
    if (sugs.length) {
      lines.push("Repair suggestions:");
      sugs.forEach(function (s) {
        lines.push("  - [" + s.operation + "] " + s.label +
                   " (risk: " + (s.risk || "n/a") + "): " + s.rationale);
      });
    } else {
      lines.push("No built-in repair suggestion for this issue type.");
    }
    td.textContent = lines.join("\\n");
    td.style.whiteSpace = "pre-line";
    tr.appendChild(td);
    return tr;
  }

  function render() {
    var rows = filtered().sort(function (a, b) { return cmp(a, b, state.key, state.reverse); });
    var totalPages = Math.max(1, Math.ceil(rows.length / pageSize));
    state.page = Math.max(1, Math.min(state.page, totalPages));
    var start = (state.page - 1) * pageSize;
    var page = rows.slice(start, start + pageSize);
    countEl.textContent = rows.length + " issue(s)";
    pgInfo.textContent = (rows.length ? (start + 1) + "-" + (start + page.length) : "0")
      + " / " + rows.length + " - page " + state.page + "/" + totalPages;
    headers.forEach(function (th) {
      th.classList.remove("sorted-asc", "sorted-desc");
      if (th.getAttribute("data-key") === state.key) {
        th.classList.add(state.reverse ? "sorted-desc" : "sorted-asc");
      }
    });
    tbody.textContent = "";
    if (!page.length) {
      var empty = document.createElement("tr");
      var emptyTd = document.createElement("td");
      emptyTd.colSpan = 6;
      emptyTd.className = "meta";
      emptyTd.textContent = "no issues";
      empty.appendChild(emptyTd);
      tbody.appendChild(empty);
      return;
    }
    page.forEach(function (r) {
      var tr = document.createElement("tr");
      tr.className = "issue-row";
      tr.setAttribute("data-issue-id", r.id);
      var sevClass = SEV_CLASS[r.severity] || "badge-info";
      tr.appendChild(cell(r.severity, sevClass));
      tr.appendChild(cell(r.priority.toFixed(1)));
      var titleTd = cell(r.title);
      if (baseUrl) {
        var a = document.createElement("a");
        a.href = baseUrl + "/ui/scans/" + encodeURIComponent(runId) +
                 "/issues/" + encodeURIComponent(r.id);
        a.textContent = "workbench";
        a.className = "workbench-link";
        titleTd.appendChild(a);
      }
      tr.appendChild(titleTd);
      tr.appendChild(cell((r.columns || []).join(", ") || "-"));
      tr.appendChild(cell((r.detectors || []).join(", ")));
      tr.appendChild(cell(r.affected + " (" + (r.affectedRatio * 100).toFixed(2) + "%)"));
      tr.appendChild(detailRow(r));
      tr.addEventListener("click", function () {
        var detail = tr.querySelector(".issue-detail");
        detail.classList.toggle("collapsed");
      });
      tbody.appendChild(tr);
    });
  }

  searchInput.addEventListener("input", function () {
    state.search = searchInput.value.trim().toLowerCase();
    state.page = 1;
    render();
  });
  headers.forEach(function (th) {
    th.addEventListener("click", function () {
      var key = th.getAttribute("data-key");
      if (state.key === key) { state.reverse = !state.reverse; }
      else { state.key = key; state.reverse = true; }
      state.page = 1;
      render();
    });
  });
  document.getElementById("f-severity").addEventListener("change", function (e) {
    state.severity = e.target.value; state.page = 1; render();
  });
  document.getElementById("f-dimension").addEventListener("change", function (e) {
    state.dimension = e.target.value; state.page = 1; render();
  });
  document.getElementById("pg-prev").addEventListener("click", function () {
    if (state.page > 1) { state.page -= 1; render(); }
  });
  document.getElementById("pg-next").addEventListener("click", function () {
    state.page += 1; render();
  });
  document.getElementById("issues")._render = render;
  var btnExpand = document.getElementById("btn-expand-all");
  var btnCollapse = document.getElementById("btn-collapse-all");
  if (btnExpand) {
    btnExpand.addEventListener("click", function () {
      var details = tbody.querySelectorAll(".issue-detail");
      Array.prototype.forEach.call(details, function (d) { d.classList.remove("collapsed"); });
    });
  }
  if (btnCollapse) {
    btnCollapse.addEventListener("click", function () {
      var details = tbody.querySelectorAll(".issue-detail");
      Array.prototype.forEach.call(details, function (d) { d.classList.add("collapsed"); });
    });
  }
  render();
})();"""


def issue_rows(report: Report) -> list[dict[str, Any]]:
    """report issues → 浏览器视图模型（title/description 已 PII 掩码，字段精简可 JSON 序列化）。"""
    rows = []
    for issue in report["issues"]:
        rows.append(
            {
                "id": issue["id"],
                "issue_type": issue["issue_type"],
                "title": mask_text_pii(issue["title"]),
                "description": mask_text_pii(issue.get("description") or ""),
                "severity": issue["severity"],
                "priority": round(issue["priority_score"], 1),
                "confidence": issue.get("confidence", 0.0),
                "falsePositiveRisk": str(issue.get("false_positive_risk", "medium")),
                "affected": issue["affected_count"],
                "affectedRatio": round(issue["affected_ratio"], 4),
                "affectedRowIds": list(issue.get("affected_row_ids") or [])[:10],
                "columns": list(issue["columns"]),
                "detectors": list(issue["detector_ids"]),
                "dimensions": [str(d) for d in (issue.get("quality_dimensions") or [])],
                "suggestions": suggest_repairs(issue),
            }
        )
    return rows


def filter_issues(
    rows: list[dict[str, Any]],
    *,
    severity: str | None = None,
    dimension: str | None = None,
    search: str | None = None,
) -> list[dict[str, Any]]:
    """severity 精确 / 维度包含 / 搜索子串（title+columns+detectors，大小写不敏感）。"""
    query = (search or "").strip().lower()
    out = []
    for row in rows:
        if severity and row["severity"] != severity:
            continue
        if dimension and dimension not in row["dimensions"]:
            continue
        if query:
            hay = " ".join([row["title"], *row["columns"], *row["detectors"]]).lower()
            if query not in hay:
                continue
        out.append(row)
    return out


def _sort_key(row: dict[str, Any], key: str) -> Any:
    if key == "severity":
        return _SEVERITY_RANK.get(row["severity"], 9)
    return row[key]


def sort_issues(
    rows: list[dict[str, Any]],
    *,
    key: str = "priority",
    reverse: bool = True,
) -> list[dict[str, Any]]:
    """列排序：priority / severity / affected / title（默认 priority 降序）。"""
    if key not in _SORT_KEYS:
        raise ValueError(f"unsupported sort key: {key}")
    return sorted(rows, key=lambda r: _sort_key(r, key), reverse=reverse)


def paginate(
    rows: list[dict[str, Any]],
    *,
    page: int = 1,
    page_size: int = 25,
) -> tuple[list[dict[str, Any]], int, int]:
    """分页：返回 (当前页行, 总页数, 总行数)；page 越界自动钳制。"""
    total = len(rows)
    total_pages = max(1, math.ceil(total / page_size))
    page = max(1, min(page, total_pages))
    start = (page - 1) * page_size
    return rows[start : start + page_size], total_pages, total


def find_issue_by_id(rows: list[dict[str, Any]], issue_id: str) -> dict[str, Any] | None:
    """与 JS 行定位同语义：按视图模型 id 查找（供发现清单等外部联动链接定位）。"""
    for row in rows:
        if row["id"] == issue_id:
            return row
    return None


def json_script(payload: Any) -> str:
    """安全内嵌 JSON：`<`/`>`/`&` → \\uXXXX，杜绝 `</script>` 提前闭合标签。"""
    return (
        json.dumps(payload, ensure_ascii=True, separators=(",", ":"))
        .replace("<", "\\u003c")
        .replace(">", "\\u003e")
        .replace("&", "\\u0026")
    )


def render_trend_svg(trend: dict[str, Any], *, width: int = 260, height: int = 60) -> str:
    """单个数据集趋势 → 迷你 SVG 折线图（trends.py 序列化结构，离线内联）。"""
    points = trend.get("points") or []
    dataset = escape(str(trend.get("dataset_id") or "dataset"))
    if len(points) < 2:
        return ""
    scores = [float(p["score"]) for p in points]
    lo, hi = min(scores), max(scores)
    span = hi - lo if hi > lo else 1.0
    pad = 5.0
    step = (width - 2 * pad) / (len(points) - 1)
    coords: list[str] = []
    dots: list[str] = []
    for index, score in enumerate(scores):
        x = pad + index * step
        y = pad + (1 - (score - lo) / span) * (height - 2 * pad)
        coords.append(f"{x:.1f},{y:.1f}")
        dots.append(
            f'<circle cx="{x:.1f}" cy="{y:.1f}" r="2.5" fill="#0969da">'
            f"<title>{score:.1f}</title></circle>"
        )
    return (
        '<div class="trend-block">'
        f'<p class="meta"><strong>{dataset}</strong> &mdash; {len(points)} completed scans</p>'
        f'<svg class="trend-svg" viewBox="0 0 {width} {height}" width="{width}" height="{height}" '
        f'role="img" aria-label="{dataset} quality trend">'
        f'<polyline points="{" ".join(coords)}" fill="none" stroke="#0969da" stroke-width="1.6"/>'
        + "".join(dots)
        + "</svg></div>"
    )


def render_interactive_issue_table(
    report: Report,
    *,
    page_size: int = 25,
    server_base_url: str | None = None,
) -> str:
    """Issue Breakdown 交互容器：控制条 + 可排序表格 + 分页 + 内联数据 JSON + 原生 JS。"""
    rows = issue_rows(report)
    dimensions = sorted({d for row in rows for d in row["dimensions"]})
    payload = {
        "issues": rows,
        "pageSize": page_size,
        "runId": report["scan_run_id"],
        "serverBaseUrl": server_base_url,
    }
    severity_options = "".join(f'<option value="{s}">{s}</option>' for s in SEVERITY_ORDER)
    dimension_options = "".join(
        f'<option value="{escape(d)}">{escape(d)}</option>' for d in dimensions
    )
    return (
        '<h2 id="issue_breakdown">Issue Breakdown</h2>'
        '<div id="issues">'
        '<div class="issue-controls">'
        '<select id="f-severity" aria-label="filter by severity">'
        '<option value="all">all severities</option>'
        f"{severity_options}</select>"
        '<select id="f-dimension" aria-label="filter by dimension">'
        '<option value="all">all dimensions</option>'
        f"{dimension_options}</select>"
        '<input id="f-search" type="search" '
        'placeholder="search title / columns / detectors" aria-label="search issues">'
        '<button id="btn-expand-all" type="button">expand all</button>'
        '<button id="btn-collapse-all" type="button">collapse all</button>'
        '<span id="issue-count" class="meta"></span>'
        "</div>"
        '<table id="issue-table">'
        "<thead><tr>"
        '<th data-key="severity">Severity</th>'
        '<th data-key="priority">Priority</th>'
        '<th data-key="title">Title</th>'
        "<th>Columns</th><th>Detectors</th>"
        '<th data-key="affected">Affected</th>'
        "</tr></thead>"
        '<tbody id="issue-tbody"></tbody>'
        "</table>"
        '<div class="issue-pager">'
        '<button id="pg-prev" type="button">&larr; prev</button>'
        '<button id="pg-next" type="button">next &rarr;</button>'
        '<span id="pg-info" class="meta"></span>'
        "</div>"
        "</div>"
        f'<script type="application/json" id="issue-data">{json_script(payload)}</script>'
        f"<script>{_INTERACTIVE_JS}</script>"
    )


__all__ = [
    "SEVERITY_ORDER",
    "filter_issues",
    "find_issue_by_id",
    "issue_rows",
    "json_script",
    "paginate",
    "render_interactive_issue_table",
    "render_trend_svg",
    "sort_issues",
]
